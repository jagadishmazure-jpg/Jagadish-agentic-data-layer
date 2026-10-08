"""The simulated hospital: Halsey Vale Health (fictional) and fall prevention on its inpatient wards.

Every patient, ward, record and number in this domain is synthetic and PHI-free: nothing comes from a
real patient, clinician or hospital.

One daily engine (`step`) plays both the 180-day history and the 28-day forward simulation:

* Each morning the nurse in charge of each ward decides which in-house patients get each preventive
  measure today: a bed alarm, hourly rounding, a sitter (one-to-one observation) or a new mobility aid.
  Bed alarms, rounding slots and sitters have a fixed daily capacity; a mobility aid stays with the
  patient for the rest of the stay.
* During the day patients are admitted and some patients fall. A patient falls by one of three
  mechanisms: getting out of bed unaided (worse with confusion and sedation), toileting urgency, or an
  unsteady gait. Each measure reduces the risk from each mechanism by a different amount; a fall can
  cause harm (an injury), which costs far more than a fall without harm.
* Each evening nurses record observations for the day (confusion, night restlessness, toileting calls,
  unsteady gait) and the medication record sets a sedation flag. The Morse fall scale is taken on
  admission and repeated every third day. Patients leave on their discharge day.

Hidden traits (cognitive impairment, gait impairment, toileting need, frailty, the length of stay, when
a delirium starts and ends, bone fragility) live in `State.ground_truth`. They drive behaviour here and
in the evaluation harness but are never written to bronze and never read by product code. Policies
receive a `View` of what the nurses can observe.

Each patient carries a synthetic group (G1 or G2). It has no effect on any hidden trait or on the risk
of falling; it is correlated with how often a confusion assessment is documented (confusion is recorded
less often for G2), which is how a model can end up treating the groups differently. It is used only by
the fairness audit.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

DAYS_HISTORY = 180  # observed history: day 0 .. 179
HORIZON = 28
DAYS_TOTAL = DAYS_HISTORY + HORIZON
REGIONS = {"north": ("W01", "W02", "W03", "W04", "W05", "W06"), "south": ("W07", "W08", "W09", "W10", "W11", "W12")}
SITES = {"north": "Halsey", "south": "Vale"}
WARD_TYPES = ("general medicine", "surgical", "orthopaedics", "elderly care", "neurology and stroke", "cardiology")
WARD_REGION = {w: r for r, ws in REGIONS.items() for w in ws}
WARDS = tuple(sorted(WARD_REGION))
WARD_NAMES = {w: f"{SITES[WARD_REGION[w]]} {WARD_TYPES[k % 6]}" for k, w in enumerate(WARDS)}
WARD_P = (0.10, 0.09, 0.075, 0.085, 0.075, 0.075) * 2  # share of admissions per ward, the same mix at both sites
GROUPS = ("G1", "G2")
AGE_BANDS = ("18-49", "50-64", "65-74", "75-84", "85+")
SOURCES = ("emergency", "elective", "transfer")
MEASURES = ("bed_alarm", "hourly_rounding", "mobility_aid", "sitter")
MECHANISMS = ("bed_exit", "toileting", "gait")
ADMISSIONS_PER_DAY = 50
# Capacity across the hospital each day: bed alarm units, patients on hourly rounding, sitters, new mobility aids.
CAPACITY = {"bed_alarm": 60, "hourly_rounding": 90, "sitter": 12, "mobility_aid": 16}
# Share of the risk from each mechanism that a measure removes (bed exit, toileting, gait). Combined multiplicatively.
EFFECT = {
    "bed_alarm": (0.45, 0.15, 0.10),
    "hourly_rounding": (0.15, 0.40, 0.15),
    "mobility_aid": (0.00, 0.10, 0.45),
    "sitter": (0.65, 0.40, 0.35),
}
# Cost assumptions, USD: a measure per patient-day (a mobility aid once), and a fall without and with harm.
MEASURE_COST = {"bed_alarm": 12.0, "hourly_rounding": 28.0, "mobility_aid": 45.0, "sitter": 520.0}
FALL_COST = 3500.0  # assessment, observation, imaging and nurse time after a fall without harm
HARM_FALL_COST = 30000.0  # treatment and extra days in hospital after a fall with harm
DOCUMENTED = (0.92, 0.70)  # chance that a confused patient's confusion is documented that day, by group
ASSESS_EVERY = 3  # the Morse scale is repeated every third day of the stay

FUTURE, IN, OUT = range(3)


@dataclass
class Patients:
    """What the hospital knows about each encounter at admission (index i is encounter ENC-{i+1:06d})."""

    admit_day: np.ndarray
    ward: np.ndarray
    age: np.ndarray  # index into AGE_BANDS
    source: np.ndarray
    group: np.ndarray  # 1 = G2
    prior_fall: np.ndarray  # a fall in the last three months, from the admission interview
    secondary_dx: np.ndarray  # more than one active diagnosis (Morse item)

    @property
    def n(self) -> int:
        return len(self.admit_day)

    def ids(self) -> list[str]:
        return [f"ENC-{i + 1:06d}" for i in range(self.n)]


@dataclass
class Hidden:
    """Ground truth the hospital never observes."""

    discharge_day: np.ndarray
    cognitive: np.ndarray
    gait: np.ndarray
    toileting: np.ndarray
    frailty: np.ndarray
    delirium_on: np.ndarray  # first day of a delirium (a large number if none)
    delirium_off: np.ndarray
    fragile: np.ndarray  # bone fragility: a fall is more likely to cause harm


def _sig(x):
    return 1 / (1 + np.exp(-x))


def _draw(rng: np.random.Generator, first_day: int, last_day: int) -> tuple[Patients, Hidden]:
    days = []
    for d in range(first_day, last_day):
        days += [d] * int(rng.poisson(ADMISSIONS_PER_DAY))
    n = len(days)
    day = np.array(days, int)
    ward = rng.choice(len(WARDS), n, p=WARD_P)
    kind = ward % len(WARD_TYPES)  # 0 general medicine, 1 surgical, 2 orthopaedics, 3 elderly care, 4 neurology, 5 cardiology
    age = np.clip(np.round(rng.normal(2.0 + 1.0 * (kind == 3) - 0.6 * (kind == 1), 1.1, n)), 0, 4).astype(int)
    source = rng.choice(3, n, p=(0.65, 0.25, 0.10))
    cognitive = rng.random(n) < 0.03 + 0.06 * age + 0.10 * np.isin(kind, [3, 4])
    gait = rng.random(n) < 0.08 + 0.07 * age + 0.25 * (kind == 2) + 0.20 * (kind == 4)
    toileting = rng.random(n) < 0.10 + 0.05 * age + 0.10 * (kind == 1)
    prior_fall = rng.random(n) < 0.05 + 0.35 * gait + 0.20 * cognitive
    los = 1 + rng.geometric(1 / (3.5 + 0.6 * age + 1.5 * np.isin(kind, [2, 4])), n)
    los = np.minimum(los, 30)
    delirium = rng.random(n) < 0.04 + 0.04 * age + 0.15 * cognitive + 0.05 * (kind == 1)
    onset = day + 1 + (rng.random(n) * np.maximum(los - 1, 1)).astype(int)
    patients = Patients(
        admit_day=day,
        ward=ward,
        age=age,
        source=source,
        group=(rng.random(n) < 0.35).astype(int),
        prior_fall=prior_fall,
        secondary_dx=rng.random(n) < 0.6,
    )
    hidden = Hidden(
        discharge_day=day + los,
        cognitive=cognitive,
        gait=gait,
        toileting=toileting,
        frailty=rng.normal(-0.3 * (source == 1), 0.3, n),
        delirium_on=np.where(delirium, onset, 10**6),
        delirium_off=np.where(delirium, onset + rng.integers(2, 7, n), 10**6),
        fragile=rng.random(n) < 0.15 + 0.06 * age,
    )
    return patients, hidden


def _concat(a, b):
    return type(a)(**{k: np.concatenate([getattr(a, k), getattr(b, k)]) for k in type(a).__dataclass_fields__})


@dataclass
class World:
    patients: Patients
    hidden: Hidden

    @property
    def n(self) -> int:
        return self.patients.n


def make_world(seed: int = 31) -> World:
    return World(*_draw(np.random.default_rng(seed), 0, DAYS_HISTORY))


def extend_world(w: World, seed: int) -> World:
    """The world for one forward replication: new admissions on days 180..207."""
    p, h = _draw(np.random.default_rng(seed), DAYS_HISTORY, DAYS_TOTAL)
    return World(_concat(w.patients, p), _concat(w.hidden, h))


# Morse items, in the order of `State.morse`.
MORSE_ITEMS = ("history_of_falling", "secondary_diagnosis", "ambulatory_aid", "iv_access", "gait", "mental_status")
OBS = ("confusion", "night_restless", "toileting_calls", "unsteady_gait", "sedation")

ARRAYS = (
    "status", "bed", "has_aid", "aid_given", "sedated", "fell", "first_measure", "assess_day", "obs_day", "alarm", "rounding", "sitter",
    "had_alarm", "had_rounding", "had_aid", "had_sitter", "present",
)  # fmt: skip


@dataclass
class State:
    status: np.ndarray
    bed: np.ndarray
    has_aid: np.ndarray  # a walking aid of the patient's own or given on the ward
    aid_given: np.ndarray  # a mobility aid given on the ward during this stay
    sedated: np.ndarray
    fell: np.ndarray  # fell during this stay
    first_measure: np.ndarray  # day the first measure started (-1 = none yet)
    assess_day: np.ndarray
    obs_day: np.ndarray
    alarm: np.ndarray  # measures in place today (yesterday's, as seen by the morning policy)
    rounding: np.ndarray
    sitter: np.ndarray
    had_alarm: np.ndarray  # measure flags over the current run, for the fairness audit
    had_rounding: np.ndarray
    had_aid: np.ndarray
    had_sitter: np.ndarray
    present: np.ndarray  # in hospital on at least one day of the current run
    morse: np.ndarray  # (n, 6) latest Morse items
    obs: np.ndarray  # (n, 5) latest observations; confusion is 1 yes, 0 no, -1 not documented
    ground_truth: Hidden
    record: bool = False
    events: dict[str, list] = field(default_factory=dict)
    totals: dict[str, float] = field(default_factory=dict)

    def copy(self) -> State:
        return State(
            *(getattr(self, k).copy() for k in ARRAYS),
            self.morse.copy(),
            self.obs.copy(),
            self.ground_truth,
            False,
            {},
            dict.fromkeys(self.totals, 0.0),
        )


TOTALS = (
    *(f"{k}_{g}" for g in GROUPS for k in ("bed_days", "falls", "falls_protected")),
    "bed_days", "admissions", "falls", "harm_falls", "falls_protected", "fall_cost_usd", "harm_fall_cost_usd", "measure_cost_usd",
    "alarm_days", "rounding_days", "aid_starts", "sitter_shifts", "first_measures", "first_measure_delay_days", "days",
)  # fmt: skip


def new_state(w: World, record: bool) -> State:
    n = w.n
    return State(
        status=np.full(n, FUTURE),
        bed=np.zeros(n, int),
        has_aid=np.zeros(n, bool),
        aid_given=np.zeros(n, bool),
        sedated=np.zeros(n, bool),
        fell=np.zeros(n, bool),
        first_measure=np.full(n, -1),
        assess_day=np.full(n, -1),
        obs_day=np.full(n, -1),
        alarm=np.zeros(n, bool),
        rounding=np.zeros(n, bool),
        sitter=np.zeros(n, bool),
        had_alarm=np.zeros(n, bool),
        had_rounding=np.zeros(n, bool),
        had_aid=np.zeros(n, bool),
        had_sitter=np.zeros(n, bool),
        present=np.zeros(n, bool),
        morse=np.zeros((n, 6), int),
        obs=np.zeros((n, 5), int),
        ground_truth=w.hidden,
        record=record,
        events={k: [] for k in ("admissions", "discharges", "assessments", "observations", "interventions", "falls")},
        totals=dict.fromkeys(TOTALS, 0.0),
    )


def resize(st: State, w: World) -> State:
    extra = w.n - len(st.status)
    if extra <= 0:
        return st
    fresh = new_state(w, False)
    for k in ARRAYS:
        a, b = getattr(st, k), getattr(fresh, k)
        setattr(st, k, np.concatenate([a, b[len(a) :]]))
    st.morse = np.concatenate([st.morse, fresh.morse[len(st.morse) :]])
    st.obs = np.concatenate([st.obs, fresh.obs[len(st.obs) :]])
    st.ground_truth = w.hidden
    return st


def start_run(st: State) -> State:
    """Clear the per-run fairness flags (used at the start of each forward replication)."""
    for k in ("had_alarm", "had_rounding", "had_aid", "had_sitter"):
        getattr(st, k)[:] = False
    st.present[:] = st.status == IN
    return st


@dataclass(frozen=True)
class View:
    """What the nurses can observe on the morning of day t (everything recorded through day t-1), for the
    patients in hospital this morning. Arrays are aligned with `idx`."""

    t: int
    idx: np.ndarray
    ward: np.ndarray
    age: np.ndarray
    source: np.ndarray
    days_in: np.ndarray
    morse: np.ndarray  # (k, 6) latest Morse items
    days_since_assessment: np.ndarray
    obs: np.ndarray  # (k, 5) yesterday's observations
    has_aid: np.ndarray
    alarm: np.ndarray  # measures in place yesterday
    rounding: np.ndarray
    sitter: np.ndarray

    @property
    def morse_total(self) -> np.ndarray:
        return self.morse.sum(1)


def view(w: World, st: State, t: int) -> View:
    p = w.patients
    idx = np.flatnonzero(st.status == IN)
    return View(
        t,
        idx,
        p.ward[idx],
        p.age[idx],
        p.source[idx],
        t - p.admit_day[idx],
        st.morse[idx].copy(),
        t - st.assess_day[idx],
        st.obs[idx].copy(),
        st.has_aid[idx].astype(int),
        st.alarm[idx].astype(int),
        st.rounding[idx].astype(int),
        st.sitter[idx].astype(int),
    )


@dataclass
class Decision:
    """Hospital indices (from View.idx) for each measure today; mobility_aid lists the new aids."""

    bed_alarm: np.ndarray
    hourly_rounding: np.ndarray
    sitter: np.ndarray
    mobility_aid: np.ndarray


class Policy:
    name = "policy"

    def decide(self, v: View) -> Decision:  # pragma: no cover - interface
        raise NotImplementedError


def top(score: np.ndarray, ids: np.ndarray, eligible: np.ndarray, cap: int) -> np.ndarray:
    """The eligible ids with the highest score, ties broken by id, at most cap of them."""
    order = np.lexsort((ids, -score))
    return ids[order[eligible[order]]][:cap]


class CurrentRules(Policy):
    """How Halsey Vale works today: a Morse total of 45 or more (from the latest assessment, taken on admission
    and every third day) is high risk. High-risk patients get bed alarms and hourly rounding, highest Morse
    first, up to capacity; a sitter goes to a patient with a Morse total of 60 or more whose mental status
    item is scored (confused), highest first; a mobility aid goes to a patient scored for walking with
    furniture and an impaired gait."""

    name = "current rules"

    def decide(self, v: View) -> Decision:
        m = v.morse_total
        high = m >= 45
        mi = {k: v.morse[:, j] for j, k in enumerate(MORSE_ITEMS)}
        return Decision(
            top(m, v.idx, high, CAPACITY["bed_alarm"]),
            top(m, v.idx, high, CAPACITY["hourly_rounding"]),
            top(m, v.idx, (m >= 60) & (mi["mental_status"] == 15), CAPACITY["sitter"]),
            top(m, v.idx, (mi["ambulatory_aid"] == 30) & (mi["gait"] == 20) & (v.has_aid == 0), CAPACITY["mobility_aid"]),
        )


def _rng(seed: int, t: int) -> np.random.Generator:
    return np.random.default_rng([seed, t])


def hazards(w: World, st: State, ids: np.ndarray, t: int) -> np.ndarray:
    """Daily fall probability by mechanism (k, 3) before any measure."""
    p, h = w.patients, st.ground_truth
    delirium = (h.delirium_on[ids] <= t) & (t < h.delirium_off[ids])
    sed = st.sedated[ids]
    age = p.age[ids]
    early = (t - p.admit_day[ids]) <= 1
    f = h.frailty[ids]
    return np.column_stack(
        [
            _sig(-9.4 + 2.4 * h.cognitive[ids] + 3.4 * delirium + 1.0 * sed + 0.25 * age + f),
            _sig(-9.1 + 2.8 * h.toileting[ids] + 0.8 * sed + 0.2 * age + 0.6 * delirium + f),
            _sig(-9.1 + 2.6 * h.gait[ids] + 0.8 * sed + 0.25 * age + 0.6 * early + f),
        ]
    )


def step(w: World, st: State, t: int, policy: Policy, seed: int) -> None:
    """Play day t: the morning's measures, admissions, falls during the day, the evening's observations and
    assessments, then discharges."""
    p, h = w.patients, st.ground_truth
    n = w.n
    rng = _rng(seed, t)
    u_fall, u_harm, u_sed, u_conf, u_rest, u_unst, u_ms, u_iv, u_gait, u_amb = (rng.random(n) for _ in range(10))
    calls_base, calls_extra = rng.poisson(0.6, n), rng.poisson(3.0, n)
    rec, tot = st.record, st.totals
    grp = p.group
    # morning: today's measures for the patients in hospital
    if t > 0:
        v = view(w, st, t)
        d = policy.decide(v)
        inside = st.status == IN
        new = {}
        for k, arr in (("bed_alarm", st.alarm), ("hourly_rounding", st.rounding), ("sitter", st.sitter)):
            chosen = np.asarray(getattr(d, k), int)
            chosen = chosen[inside[chosen]][: CAPACITY[k]]
            arr[:] = False
            arr[chosen] = True
            new[k] = chosen
        aids = np.asarray(d.mobility_aid, int)
        aids = aids[inside[aids] & ~st.has_aid[aids]][: CAPACITY["mobility_aid"]]
        st.has_aid[aids] = True
        st.aid_given[aids] = True
        st.had_alarm |= st.alarm
        st.had_rounding |= st.rounding
        st.had_sitter |= st.sitter
        st.had_aid[aids] = True
        any_measure = st.alarm | st.rounding | st.sitter
        any_measure[aids] = True
        first = np.flatnonzero(any_measure & (st.first_measure < 0))
        st.first_measure[first] = t
        tot["first_measures"] += len(first)
        tot["first_measure_delay_days"] += float((t - p.admit_day[first]).sum())
        tot["alarm_days"] += int(st.alarm.sum())
        tot["rounding_days"] += int(st.rounding.sum())
        tot["sitter_shifts"] += int(st.sitter.sum())
        tot["aid_starts"] += len(aids)
        tot["measure_cost_usd"] += (
            MEASURE_COST["bed_alarm"] * st.alarm.sum()
            + MEASURE_COST["hourly_rounding"] * st.rounding.sum()
            + MEASURE_COST["sitter"] * st.sitter.sum()
            + MEASURE_COST["mobility_aid"] * len(aids)
        )
        if rec:
            for k in ("bed_alarm", "hourly_rounding", "sitter"):
                st.events["interventions"] += [(int(i), t, k) for i in new[k]]
            st.events["interventions"] += [(int(i), t, "mobility_aid") for i in aids]
    # admissions: a bed on the ward, no measures until tomorrow morning
    adm = np.flatnonzero((st.status == FUTURE) & (p.admit_day == t))
    for i in adm:
        taken = set(st.bed[(st.status == IN) & (p.ward == p.ward[i])].tolist())
        st.bed[i] = next(b for b in range(1, 200) if b not in taken)
        st.status[i] = IN
    st.has_aid[adm] = (h.gait[adm] & (u_amb[adm] < 0.5)) | (u_amb[adm] < 0.03)
    tot["admissions"] += len(adm)
    if rec:
        st.events["admissions"] += [(int(i), t) for i in adm]
    ids = np.flatnonzero(st.status == IN)
    st.present[ids] = True
    # sedation today: a course continues with probability 0.7, a new one starts with probability 0.06
    st.sedated[ids] = np.where(st.sedated[ids], u_sed[ids] < 0.7, u_sed[ids] < 0.06 + 0.01 * p.age[ids])
    # falls during the day
    haz = hazards(w, st, ids, t)
    mult = np.ones_like(haz)
    for k, on in (("bed_alarm", st.alarm[ids]), ("hourly_rounding", st.rounding[ids]), ("sitter", st.sitter[ids]), ("mobility_aid", st.has_aid[ids])):
        mult *= 1 - np.outer(on, EFFECT[k])
    p_fall = 1 - np.prod(1 - haz * mult, axis=1)
    fall = ids[u_fall[ids] < p_fall]
    harm = fall[u_harm[fall] < _sig(-2.0 + 0.3 * p.age[fall] + 1.0 * h.fragile[fall])]
    protected = fall[(st.alarm | st.rounding | st.sitter | st.aid_given)[fall]]  # a measure from the ward in place that day
    st.fell[fall] = True
    tot["bed_days"] += len(ids)
    tot["falls"] += len(fall)
    tot["harm_falls"] += len(harm)
    tot["falls_protected"] += len(protected)
    tot["fall_cost_usd"] += FALL_COST * (len(fall) - len(harm)) + HARM_FALL_COST * len(harm)
    tot["harm_fall_cost_usd"] += HARM_FALL_COST * len(harm)
    for g, name in enumerate(GROUPS):
        tot[f"bed_days_{name}"] += int((grp[ids] == g).sum())
        tot[f"falls_{name}"] += int((grp[fall] == g).sum())
        tot[f"falls_protected_{name}"] += int((grp[protected] == g).sum())
    if rec:
        st.events["falls"] += [(int(i), t, bool(i in set(harm.tolist()))) for i in fall]
    # evening: observations for every patient, the Morse scale on admission and every third day
    delirium = (h.delirium_on[ids] <= t) & (t < h.delirium_off[ids])
    confused = h.cognitive[ids] | delirium
    documented = u_conf[ids] < np.array(DOCUMENTED)[grp[ids]]
    conf = np.where(confused, np.where(documented, 1, -1), np.where(u_conf[ids] < 0.03, 1, np.where(u_conf[ids] < 0.88, 0, -1)))
    sed = st.sedated[ids]
    st.obs[ids, 0] = conf
    st.obs[ids, 1] = u_rest[ids] < 0.05 + 0.25 * h.cognitive[ids] + 0.55 * delirium + 0.15 * sed
    st.obs[ids, 2] = np.minimum(calls_base[ids] + h.toileting[ids] * calls_extra[ids] + delirium, 12)
    st.obs[ids, 3] = u_unst[ids] < 0.05 + 0.65 * h.gait[ids] + 0.2 * sed
    st.obs[ids, 4] = sed
    st.obs_day[ids] = t
    due = ids[(t - p.admit_day[ids]) % ASSESS_EVERY == 0]
    dc = (h.delirium_on[due] <= t) & (t < h.delirium_off[due])
    conf_due = h.cognitive[due] | dc
    gait_score = np.where(h.gait[due], np.where(u_gait[due] < 0.7, 20, 10), np.where(u_gait[due] < 0.25 + 0.2 * st.sedated[due], 10, 0))
    amb = np.where(st.has_aid[due], 15, np.where(h.gait[due] & (u_amb[due] < 0.5), 30, 0))
    st.morse[due] = np.column_stack(
        [
            25 * (p.prior_fall[due] | st.fell[due]),
            15 * p.secondary_dx[due],
            amb,
            20 * (u_iv[due] < 0.65),
            gait_score,
            15 * np.where(conf_due, u_ms[due] < 0.85, u_ms[due] < 0.04),
        ]
    )
    st.assess_day[due] = t
    if rec:
        st.events["observations"] += [(int(i), t, *map(int, st.obs[i])) for i in ids]
        st.events["assessments"] += [(int(i), t, *map(int, st.morse[i])) for i in due]
    # discharges at the end of the day
    out = ids[h.discharge_day[ids] <= t + 1]
    st.status[out] = OUT
    st.alarm[out] = st.rounding[out] = st.sitter[out] = False
    if rec:
        st.events["discharges"] += [(int(i), t + 1) for i in out]
    tot["days"] += 1


def run(w: World, st: State, days: range, policy: Policy, seed: int) -> State:
    for t in days:
        step(w, st, t, policy, seed)
    return st
