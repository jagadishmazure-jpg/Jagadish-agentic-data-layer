"""Data-quality checks and service levels, evaluated with SQL against the stored table.

Every check in a contract becomes one SQL query that counts failing rows. A product passes when every
`error` check passes and its SLOs hold: completeness (rows kept after quarantine) and freshness (the
newest `day` is no older than the freshness SLA relative to the as-of day). Results feed the lineage
events (as data-quality facets), the `adl quality` report and the release gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from adl.core.contracts import Contract, sql_type


@dataclass(frozen=True)
class CheckResult:
    contract: str
    check: str
    target: str
    passed: bool
    failing: int
    severity: str = "error"
    detail: str = ""


@dataclass
class ProductQuality:
    contract: Contract
    rows: int
    quarantined: int
    results: list[CheckResult] = field(default_factory=list)
    freshness_lag: int | None = None

    @property
    def completeness_pct(self) -> float:
        total = self.rows + self.quarantined
        return 100.0 if total == 0 else 100.0 * self.rows / total

    @property
    def slo_met(self) -> bool:
        s = self.contract.slo
        fresh = self.freshness_lag is None or self.freshness_lag <= s.freshness_days
        return self.completeness_pct >= s.completeness_pct and fresh

    @property
    def passed(self) -> bool:
        return self.slo_met and all(r.passed for r in self.results if r.severity == "error")


def _q(name: str) -> str:
    return f'"{name}"'


def check_schema(c: Contract, store, layer: str, table: str) -> list[CheckResult]:
    rows = store.sql(f"DESCRIBE {store.qualified(layer, table)}")
    actual = {r["column_name"]: r["column_type"] for r in rows}
    out = []
    for col in c.schema_:
        want = sql_type(col.type)
        got = actual.get(col.name)
        ok = got is not None and (
            got == want or (want == "BIGINT" and got in {"INTEGER", "BIGINT"}) or (want == "DOUBLE" and got in {"DOUBLE", "FLOAT"})
        )
        out.append(CheckResult(c.id, "schema", col.name, ok, 0 if ok else 1, "error", f"expected {want}, found {got}"))
    extra = sorted(set(actual) - set(c.columns))
    out.append(CheckResult(c.id, "schema", "no undeclared columns", not extra, len(extra), "error", ", ".join(extra)))
    return out


def evaluate(c: Contract, store, contracts: dict[str, Contract], quarantined: int = 0, as_of_day: int | None = None) -> ProductQuality:
    t = store.qualified(c.layer, c.table)
    rows = store.sql(f"SELECT count(*) AS n FROM {t}")[0]["n"]
    res = check_schema(c, store, c.layer, c.table)
    lag = None
    for chk in c.quality:
        target = ",".join(chk.targets) or "table"
        if chk.check == "not_null":
            cond = " OR ".join(f"{_q(x)} IS NULL" for x in chk.targets)
            n = store.sql(f"SELECT count(*) AS n FROM {t} WHERE {cond}")[0]["n"]
        elif chk.check == "range":
            conds = []
            if chk.min is not None:
                conds.append(f"{_q(chk.column)} < {chk.min}")
            if chk.max is not None:
                conds.append(f"{_q(chk.column)} > {chk.max}")
            n = store.sql(f"SELECT count(*) AS n FROM {t} WHERE {' OR '.join(conds)}")[0]["n"]
        elif chk.check == "accepted_values":
            vals = ", ".join("'" + v.replace("'", "''") + "'" for v in chk.values)
            n = store.sql(f"SELECT count(*) AS n FROM {t} WHERE CAST({_q(chk.column)} AS VARCHAR) NOT IN ({vals})")[0]["n"]
        elif chk.check == "regex":
            n = store.sql(f"SELECT count(*) AS n FROM {t} WHERE NOT regexp_full_match({_q(chk.column)}, '{chk.pattern}')")[0]["n"]
        elif chk.check == "referential":
            ref_id, _, ref_col = chk.ref.rpartition(".")
            ref = contracts[ref_id]
            rt = store.qualified(ref.layer, ref.table)
            n = store.sql(
                f"SELECT count(*) AS n FROM {t} WHERE {_q(chk.column)} IS NOT NULL AND {_q(chk.column)} NOT IN (SELECT {_q(ref_col)} FROM {rt})"
            )[0]["n"]
        elif chk.check == "unique":
            keys = ", ".join(_q(x) for x in chk.targets)
            n = store.sql(f"SELECT coalesce(sum(c - 1), 0) AS n FROM (SELECT count(*) AS c FROM {t} GROUP BY {keys}) WHERE c > 1")[0]["n"]
        elif chk.check == "row_count_min":
            n = 0 if rows >= chk.value else int(chk.value - rows)
        elif chk.check == "freshness":
            col = chk.column or "day"
            newest = store.sql(f"SELECT max({_q(col)}) AS m FROM {t}")[0]["m"]
            lag = None if newest is None or as_of_day is None else int(as_of_day - newest)
            n = 0 if lag is not None and lag <= c.slo.freshness_days else 1
            target = f"{col} lag {lag} day(s), SLA {c.slo.freshness_days}"
        else:  # pragma: no cover - the contract model rejects unknown checks
            raise ValueError(chk.check)
        res.append(CheckResult(c.id, chk.check, target, int(n) == 0, int(n), chk.severity))
    return ProductQuality(c, rows, quarantined, res, lag)
