"""Bronze -> silver -> gold for the healthcare domain, contract-enforced, with lineage for every job.

All data here is synthetic and PHI-free. The shape is the one the other domains use: bronze lands each
feed unchanged; silver types and conforms with SQL, removes interface resends and resent batches and
quarantines every row that fails its contract's row-level checks (including observations for encounters
that were themselves quarantined); gold is built only from silver.

Minimum necessary, by construction:

* patient identity (synthetic name, record number, age, phone, e-mail) stays in the restricted
  `silver.patients`, and the synthetic group in the restricted `silver.demographics`;
* gold products carry a pseudonymous encounter key (`pseudonym`), never the record number, the
  encounter id, a name, a diagnosis or the group; aggregate products carry no patient key at all;
* nursing notes are redacted (record numbers, phone numbers, e-mail addresses and the known patient
  names) and screened for instruction-like text, and gold replaces encounter ids in the text with the key.

`observe` rebuilds, from silver alone, exactly what the simulator's policies see on a morning: every
patient in hospital with the latest Morse items, yesterday's observations and yesterday's measures.
A patient whose only assessment was quarantined has no Morse items on file and is read as all zeros.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

import numpy as np
import pyarrow as pa

from adl.core import guardrails
from adl.core.contracts import Contract, row_predicate
from adl.core.lineage import Lineage
from adl.core.quality import ProductQuality, evaluate
from adl.domains.healthcare import world as W

AS_OF = W.DAYS_HISTORY - 1  # last day of data; agents plan for the next morning (day 180)
# Demo salt for the pseudonymous key. A real deployment keeps it in a managed secret store and rotates it.
PSEUDONYM_SALT = "halsey-vale-synthetic-demo"
MRN_RE = re.compile(r"\bHV\d{7}\b")
ENC_RE = re.compile(r"\bENC-\d{6}\b")
SOURCES = {
    "adt_admissions": "adt-system",
    "adt_discharges": "adt-system",
    "demographics": "adt-system",
    "wards": "ward-master",
    "morse_assessments": "nursing-documentation",
    "nursing_observations": "nursing-documentation",
    "interventions": "nursing-documentation",
    "fall_incidents": "incident-reporting",
    "nursing_notes": "nursing-documentation",
}
ADT = "(SELECT *, row_number() OVER (PARTITION BY encounter_id ORDER BY ingest_seq) AS rn FROM bronze.adt_admissions)"
SILVER_SQL = {
    "wards": "SELECT ward_id, name, region, CAST(beds AS BIGINT) AS beds FROM bronze.wards",
    "patients": f"""
        SELECT mrn, patient_name, CAST(age_years AS BIGINT) AS age_years, phone, email
        FROM (SELECT *, row_number() OVER (PARTITION BY mrn ORDER BY encounter_id) AS rm FROM {ADT} WHERE rn = 1) WHERE rm = 1""",
    "demographics": "SELECT mrn, synthetic_group FROM bronze.demographics",
    "encounters": f"""
        SELECT a.encounter_id, a.mrn, a.ward_id, a.bed, CAST(a.admit_day AS BIGINT) AS admit_day, CAST(d.discharge_day AS BIGINT) AS discharge_day,
               a.admit_source, a.age_band, a.prior_fall_3m
        FROM {ADT} a LEFT JOIN bronze.adt_discharges d USING (encounter_id) WHERE a.rn = 1""",
    "morse_assessments": """
        SELECT encounter_id, CAST(day AS BIGINT) AS day, CAST(history_of_falling AS BIGINT) AS history_of_falling,
               CAST(secondary_diagnosis AS BIGINT) AS secondary_diagnosis, CAST(ambulatory_aid AS BIGINT) AS ambulatory_aid,
               CAST(iv_access AS BIGINT) AS iv_access, CAST(gait AS BIGINT) AS gait, CAST(mental_status AS BIGINT) AS mental_status,
               CAST(total AS BIGINT) AS total
        FROM bronze.morse_assessments""",
    "nursing_observations": """
        SELECT DISTINCT encounter_id, CAST(day AS BIGINT) AS day, confusion, night_restless, CAST(toileting_calls AS BIGINT) AS toileting_calls,
               unsteady_gait, sedation_flag
        FROM bronze.nursing_observations""",
    "interventions": "SELECT encounter_id, CAST(day AS BIGINT) AS day, measure FROM bronze.interventions",
    "fall_incidents": "SELECT incident_id, encounter_id, CAST(day AS BIGINT) AS day, harm, severity FROM bronze.fall_incidents",
}
# Row checks the contract format cannot express: the keyed Morse total must equal the sum of the items.
EXTRA_PREDICATE = {"morse_assessments": "total = history_of_falling + secondary_diagnosis + ambulatory_aid + iv_access + gait + mental_status"}
COST = W.MEASURE_COST
GOLD_SQL = {
    "ward_daily": f"""
        WITH e AS (SELECT encounter_id, ward_id, admit_day, coalesce(discharge_day, {W.DAYS_HISTORY}) AS out_day FROM silver.encounters),
        census AS (SELECT d.range AS day, e.ward_id, count(*) AS bed_days FROM range(0, {W.DAYS_HISTORY}) d
                   JOIN e ON e.admit_day <= d.range AND d.range < e.out_day GROUP BY ALL),
        adm AS (SELECT admit_day AS day, ward_id, count(*) AS admissions FROM e GROUP BY ALL),
        aid AS (SELECT encounter_id, min(day) AS aid_day FROM silver.interventions WHERE measure = 'mobility_aid' GROUP BY ALL),
        f AS (SELECT f.day, e.ward_id, count(*) AS falls, count(*) FILTER (WHERE f.harm) AS harm_falls,
                     count(*) FILTER (WHERE EXISTS (SELECT 1 FROM silver.interventions i WHERE i.encounter_id = f.encounter_id AND i.day = f.day
                                                     AND i.measure IN ('bed_alarm', 'hourly_rounding', 'sitter'))
                                       OR coalesce(aid.aid_day <= f.day, false)) AS falls_protected
              FROM silver.fall_incidents f JOIN e USING (encounter_id) LEFT JOIN aid USING (encounter_id) GROUP BY ALL),
        m AS (SELECT i.day, e.ward_id, count(*) FILTER (WHERE measure = 'bed_alarm') AS alarm_days,
                     count(*) FILTER (WHERE measure = 'hourly_rounding') AS rounding_days, count(*) FILTER (WHERE measure = 'mobility_aid') AS aid_starts,
                     count(*) FILTER (WHERE measure = 'sitter') AS sitter_shifts
              FROM silver.interventions i JOIN e USING (encounter_id) GROUP BY ALL),
        fm AS (SELECT first_day AS day, ward_id, count(*) AS first_measures, sum(first_day - admit_day) AS delay
               FROM (SELECT encounter_id, min(day) AS first_day FROM silver.interventions GROUP BY ALL) JOIN e USING (encounter_id) GROUP BY ALL),
        grid AS (SELECT d.range AS day, w.ward_id, w.region FROM range(0, {W.DAYS_HISTORY}) d, silver.wards w)
        SELECT CAST(g.day AS BIGINT) AS day, g.ward_id, g.region,
               CAST(coalesce(c.bed_days, 0) AS BIGINT) AS bed_days, CAST(coalesce(a.admissions, 0) AS BIGINT) AS admissions,
               CAST(coalesce(f.falls, 0) AS BIGINT) AS falls, CAST(coalesce(f.harm_falls, 0) AS BIGINT) AS harm_falls,
               CAST(coalesce(f.falls_protected, 0) AS BIGINT) AS falls_protected, CAST(coalesce(m.alarm_days, 0) AS BIGINT) AS alarm_days,
               CAST(coalesce(m.rounding_days, 0) AS BIGINT) AS rounding_days, CAST(coalesce(m.aid_starts, 0) AS BIGINT) AS aid_starts,
               CAST(coalesce(m.sitter_shifts, 0) AS BIGINT) AS sitter_shifts, CAST(coalesce(fm.first_measures, 0) AS BIGINT) AS first_measures,
               CAST(coalesce(fm.delay, 0) AS BIGINT) AS first_measure_delay_days,
               CAST({W.FALL_COST} * (coalesce(f.falls, 0) - coalesce(f.harm_falls, 0)) + {W.HARM_FALL_COST} * coalesce(f.harm_falls, 0) AS DOUBLE)
                 AS fall_cost_usd,
               CAST({COST["bed_alarm"]} * coalesce(m.alarm_days, 0) + {COST["hourly_rounding"]} * coalesce(m.rounding_days, 0)
                    + {COST["mobility_aid"]} * coalesce(m.aid_starts, 0) + {COST["sitter"]} * coalesce(m.sitter_shifts, 0) AS DOUBLE) AS measure_cost_usd
        FROM grid g LEFT JOIN census c USING (day, ward_id) LEFT JOIN adm a USING (day, ward_id) LEFT JOIN f USING (day, ward_id)
        LEFT JOIN m USING (day, ward_id) LEFT JOIN fm USING (day, ward_id)""",
    "ward_fall_rates": """
        SELECT CAST(day // 7 AS BIGINT) AS week, ward_id, region, CAST(sum(bed_days) AS BIGINT) AS bed_days, CAST(sum(falls) AS BIGINT) AS falls,
               CAST(sum(harm_falls) AS BIGINT) AS harm_falls, round(1000.0 * sum(falls) / nullif(sum(bed_days), 0), 3) AS falls_per_1000_bed_days,
               round(1000.0 * sum(harm_falls) / nullif(sum(bed_days), 0), 3) AS harm_falls_per_1000_bed_days
        FROM gold.ward_daily GROUP BY ALL""",
    "fairness_monitor": f"""
        WITH e AS (SELECT e.encounter_id, e.admit_day, coalesce(e.discharge_day, {W.DAYS_HISTORY}) AS out_day, g.synthetic_group AS grp
                   FROM silver.encounters e JOIN silver.demographics g USING (mrn)),
        had AS (SELECT encounter_id, measure FROM silver.interventions GROUP BY ALL),
        aid AS (SELECT encounter_id, min(day) AS aid_day FROM silver.interventions WHERE measure = 'mobility_aid' GROUP BY ALL),
        prot AS (SELECT f.encounter_id, f.day,
                        EXISTS (SELECT 1 FROM silver.interventions i WHERE i.encounter_id = f.encounter_id AND i.day = f.day
                                AND i.measure IN ('bed_alarm', 'hourly_rounding', 'sitter')) OR coalesce(aid.aid_day <= f.day, false) AS protected
                 FROM silver.fall_incidents f LEFT JOIN aid USING (encounter_id)),
        d AS (
          SELECT m.measure AS decision, e.grp, count(*) AS eligible, count(h.encounter_id) AS selected, NULL::DOUBLE AS r
          FROM e CROSS JOIN (SELECT unnest({list(W.MEASURES)}) AS measure) m LEFT JOIN had h ON h.encounter_id = e.encounter_id AND h.measure = m.measure
          GROUP BY ALL
          UNION ALL
          SELECT 'protected_before_fall', e.grp, count(*), count(*) FILTER (WHERE p.protected), NULL FROM prot p JOIN e USING (encounter_id) GROUP BY ALL
          UNION ALL
          SELECT 'falls_per_1000_bed_days', e.grp, sum(e.out_day - e.admit_day), (SELECT count(*) FROM silver.fall_incidents f JOIN e e2 USING (encounter_id)
                 WHERE e2.grp = e.grp), NULL
          FROM e GROUP BY ALL),
        r AS (SELECT decision, grp AS group_name, CAST(eligible AS BIGINT) AS eligible, CAST(selected AS BIGINT) AS selected,
                     CASE WHEN decision = 'falls_per_1000_bed_days' THEN 1000.0 * selected / eligible ELSE selected / eligible END AS rate FROM d)
        SELECT r.decision, r.group_name, r.eligible, r.selected, round(r.rate, 6) AS rate, round(r.rate / nullif(ref.rate, 0), 4) AS ratio_to_reference
        FROM r JOIN r AS ref ON ref.decision = r.decision AND ref.group_name = 'G1' ORDER BY 1, 2""",
}
OBSERVE_SQL = """
    WITH e AS (SELECT * FROM silver.encounters WHERE admit_day <= $t - 1 AND coalesce(discharge_day, 10000) > $t),
    m AS (SELECT encounter_id, max(day) AS assess_day, arg_max(history_of_falling, day) AS h, arg_max(secondary_diagnosis, day) AS s,
                 arg_max(ambulatory_aid, day) AS a, arg_max(iv_access, day) AS iv, arg_max(gait, day) AS g, arg_max(mental_status, day) AS ms,
                 bool_or(ambulatory_aid = 15) AS own_aid
          FROM silver.morse_assessments WHERE day <= $t - 1 GROUP BY ALL),
    o AS (SELECT encounter_id, arg_max(coalesce(confusion, 'not documented'), day) AS confusion, arg_max(night_restless, day) AS night_restless,
                 arg_max(toileting_calls, day) AS toileting_calls, arg_max(unsteady_gait, day) AS unsteady_gait, arg_max(sedation_flag, day) AS sedation
          FROM silver.nursing_observations WHERE day <= $t - 1 GROUP BY ALL),
    i AS (SELECT encounter_id, bool_or(measure = 'bed_alarm' AND day = $t - 1) AS alarm, bool_or(measure = 'hourly_rounding' AND day = $t - 1) AS rounding,
                 bool_or(measure = 'sitter' AND day = $t - 1) AS sitter, bool_or(measure = 'mobility_aid') AS aid_given
          FROM silver.interventions WHERE day <= $t - 1 GROUP BY ALL)
    SELECT e.encounter_id, e.ward_id, w.region, e.bed, e.admit_day, e.admit_source, e.age_band, coalesce(m.assess_day, e.admit_day) AS assess_day,
           coalesce(m.h, 0) AS h, coalesce(m.s, 0) AS s, coalesce(m.a, 0) AS a, coalesce(m.iv, 0) AS iv, coalesce(m.g, 0) AS g, coalesce(m.ms, 0) AS ms,
           coalesce(o.confusion, 'not documented') AS confusion, coalesce(o.night_restless, false) AS night_restless,
           coalesce(o.toileting_calls, 0) AS toileting_calls, coalesce(o.unsteady_gait, false) AS unsteady_gait, coalesce(o.sedation, false) AS sedation,
           coalesce(m.own_aid, false) OR coalesce(i.aid_given, false) AS has_aid,
           coalesce(i.alarm, false) AS alarm, coalesce(i.rounding, false) AS rounding, coalesce(i.sitter, false) AS sitter
    FROM e JOIN silver.wards w USING (ward_id) LEFT JOIN m USING (encounter_id) LEFT JOIN o USING (encounter_id) LEFT JOIN i USING (encounter_id)
    ORDER BY e.encounter_id"""
CONFUSION = {"yes": 1, "no": 0, "not documented": -1}


def pseudonym(encounter_id: str) -> str:
    """Stable pseudonymous key for an encounter (keyed hash); gold products and agents only ever see this."""
    return "PT-" + hashlib.sha256(f"{PSEUDONYM_SALT}:{encounter_id}".encode()).hexdigest()[:8].upper()


@dataclass
class Observed:
    view: W.View
    rows: list[dict]


def observe(store, t: int) -> Observed:
    """Every patient in hospital on the morning of day t, from silver only."""
    rows = store.sql(OBSERVE_SQL, {"t": t})
    col = lambda k: np.array([int(r[k]) for r in rows], int)  # noqa: E731
    v = W.View(
        t=t,
        idx=np.array([int(r["encounter_id"][4:]) - 1 for r in rows], int),
        ward=np.array([W.WARDS.index(r["ward_id"]) for r in rows], int),
        age=np.array([W.AGE_BANDS.index(r["age_band"]) for r in rows], int),
        source=np.array([W.SOURCES.index(r["admit_source"]) for r in rows], int),
        days_in=t - col("admit_day"),
        morse=np.array([[r[k] for k in ("h", "s", "a", "iv", "g", "ms")] for r in rows], int).reshape(len(rows), 6),
        days_since_assessment=t - col("assess_day"),
        obs=np.array(
            [[CONFUSION[r["confusion"]], int(r["night_restless"]), r["toileting_calls"], int(r["unsteady_gait"]), int(r["sedation"])] for r in rows],
            int,
        ).reshape(len(rows), 5),
        has_aid=col("has_aid"),
        alarm=col("alarm"),
        rounding=col("rounding"),
        sitter=col("sitter"),
    )
    return Observed(v, rows)


@dataclass
class Build:
    store: object
    contracts: dict[str, Contract]
    lineage: Lineage
    quality: dict[str, ProductQuality] = field(default_factory=dict)
    quarantined: dict[str, int] = field(default_factory=dict)
    bronze_rows: dict[str, int] = field(default_factory=dict)


def _schema(t: pa.Table) -> list[tuple[str, str]]:
    return [(f.name, str(f.type)) for f in t.schema]


def _dq_facet(q: ProductQuality) -> dict:
    return {"assertions": [{"assertion": f"{r.check}:{r.target}", "success": r.passed, "column": r.target} for r in q.results]}


def land_bronze(b: Build, tables: dict[str, pa.Table]) -> None:
    for name, data in tables.items():
        b.store.write("bronze", name, data)
        b.bronze_rows[name] = data.num_rows
        b.lineage.run(f"bronze.{name}", [f"source.{SOURCES[name]}"], [{"name": f"bronze.{name}", "schema": _schema(data), "rows": data.num_rows}])


def _conform(b: Build, table: str, data: pa.Table) -> None:
    c = b.contracts[f"healthcare.silver.{table}"]
    s = b.store
    s.stage(f"stage_{table}", data)
    pred = row_predicate(c, b.contracts, s.qualified)
    if table in EXTRA_PREDICATE:
        pred = f"({pred}) AND ({EXTRA_PREDICATE[table]})"
    cols = ", ".join(f'"{x}"' for x in c.columns)
    good = s.arrow(f"SELECT {cols} FROM stage_{table} WHERE {pred}")
    bad = s.arrow(f"SELECT * FROM stage_{table} WHERE NOT ({pred})")
    s.write("silver", table, good)
    if bad.num_rows:
        s.write("silver", f"quarantine_{table}", bad)
    b.quarantined[c.id] = bad.num_rows
    q = evaluate(c, s, b.contracts, bad.num_rows, AS_OF)
    b.quality[c.id] = q
    outs = [{"name": f"silver.{table}", "schema": _schema(good), "rows": good.num_rows, "dq": _dq_facet(q)}]
    if bad.num_rows:
        outs.append({"name": f"silver.quarantine_{table}", "rows": bad.num_rows})
    inputs = [f"{x.split('.')[1]}.{x.split('.')[2]}" for x in c.inputs]
    b.lineage.run(f"silver.{table}", inputs, outs)


def redact_note(text: str, names: list[str]) -> tuple[str, bool]:
    text, masked = guardrails.redact(text)
    if MRN_RE.search(text):
        text, masked = MRN_RE.sub("[MRN]", text), True
    for name in names:  # known patients' names, matched against the restricted patients table
        if name in text:
            text, masked = text.replace(name, "[NAME]"), True
    return text, masked


def build_silver(b: Build) -> None:
    s = b.store
    for table, sql in SILVER_SQL.items():
        _conform(b, table, s.arrow(sql))
    names = sorted({r["patient_name"] for r in s.sql("SELECT patient_name FROM silver.patients")}, key=len, reverse=True)
    notes = []
    for n in s.read("bronze", "nursing_notes").to_pylist():
        text, masked = redact_note(n["text"], names)
        notes.append(
            {
                "note_id": n["note_id"],
                "encounter_id": n["encounter_id"],
                "ward_id": n["ward_id"],
                "day": n["day"],
                "author_role": n["author_role"],
                "text_redacted": text,
                "injection_flag": guardrails.screen(n["text"]).flagged,
                "pii_redacted": masked,
            }
        )
    _conform(b, "nursing_notes", pa.Table.from_pylist(notes))


def gold_notes(store) -> pa.Table:
    rows = store.sql(
        "SELECT n.note_id, n.encounter_id, n.ward_id, w.region, n.day, n.author_role, n.text_redacted, n.injection_flag "
        "FROM silver.nursing_notes n JOIN silver.wards w USING (ward_id) ORDER BY n.note_id"
    )
    for r in rows:
        r["encounter_key"] = pseudonym(r.pop("encounter_id"))
        r["text_redacted"] = ENC_RE.sub(lambda m: pseudonym(m.group(0)), r["text_redacted"])
    return pa.Table.from_pylist(rows)


def build_gold(b: Build) -> None:
    for table, sql in GOLD_SQL.items():
        write_gold(b, table, b.store.arrow(sql))
    write_gold(b, "nursing_notes", gold_notes(b.store))


def write_gold(b: Build, table: str, data: pa.Table, extra_inputs: list[str] | None = None) -> ProductQuality:
    c = b.contracts[f"healthcare.gold.{table}"]
    cols = ", ".join(f'"{x}"' for x in c.columns)
    b.store.write("gold", table, data)
    data = b.store.arrow(f"SELECT {cols} FROM gold.{table}")  # enforce declared column order
    b.store.write("gold", table, data)
    q = evaluate(c, b.store, b.contracts, 0, AS_OF)
    b.quality[c.id] = q
    inputs = [f"{x.split('.')[1]}.{x.split('.')[2]}" for x in c.inputs] + (extra_inputs or [])
    b.lineage.run(f"gold.{table}", inputs, [{"name": f"gold.{table}", "schema": _schema(data), "rows": data.num_rows, "dq": _dq_facet(q)}])
    return q
