"""The simulated insurer: Ferrowind Insurance (fictional) and its claims operation.

One daily engine (`step`) plays both the 180-day history and the 28-day forward simulation:

* Every day new claims are reported (first notice of loss). The next morning each one is assigned to a
  queue: fast track (a light-touch desk), standard, or the complex unit. Each queue has a fixed number
  of adjuster-days per day and works its claims first in, first out, after a minimum wait for inspections and documents
  (1 day in fast track, 6 in standard, 12 in the complex unit).
* Every claim is truly simple or complex. A complex claim sent to fast track is escalated to the complex
  unit after its first assessment, so the fast-track work is wasted and the claim restarts at the back of
  the complex queue. A complex claim handled in the standard queue closes, but is more likely to be
  reopened. A simple claim in the complex unit takes more adjuster time than it needs.
* When the work is done the claim waits one night for payment. The proposed payment can contain an
  overpayment (inflated invoices, duplicate items); a leakage review before payment catches most of them.
  Some claims were caused by a third party; referring them to the subrogation unit at payment recovers
  part of the payment. Reviews and referrals have a daily capacity and a cost.
* A random sample of paid claims is audited after payment (the closed-file review). The audits are the
  unbiased record of overpayments and missed recoveries that the leakage models learn from.

Hidden traits (true complexity, true cost, the leakage propensity, third-party fault) live in
`State.ground_truth`. They drive behaviour here and in the evaluation harness but are never written to
bronze and never read by product code. Policies receive a `View` of what the insurer can observe.

The claimant's postcode district carries a synthetic proxy group (G1 or G2). It has no effect on the true
complexity, cost or leakage; it is correlated with reporting a loss later and by phone, which is how a
model can end up treating the groups differently. It is used only by the fairness audit.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

DAYS_HISTORY = 180  # observed history: day 0 .. 179
HORIZON = 28
DAYS_TOTAL = DAYS_HISTORY + HORIZON
REGIONS = {"east": ("F01", "F02", "F03"), "west": ("F04", "F05", "F06")}
OFFICE_NAMES = {"F01": "Harrowgate", "F02": "Quenby", "F03": "Lisle Cross", "F04": "Tarnbridge", "F05": "Wexmoor", "F06": "Corvane"}
OFFICE_REGION = {o: r for r, os_ in REGIONS.items() for o in os_}
OFFICES = tuple(sorted(OFFICE_NAMES))
DISTRICTS = tuple(f"FW{i + 1:02d}" for i in range(40))
DISTRICT_GROUP = tuple("G2" if i % 3 == 1 or i in (5, 30) else "G1" for i in range(40))  # 15 of 40 districts are G2
GROUPS = ("G1", "G2")
LINES = ("auto", "home", "commercial")
LINE_P = (0.55, 0.35, 0.10)
CAUSES = ("collision_third_party", "collision_single", "theft", "glass", "weather", "water_escape", "storm", "fire", "accidental_damage", "liability")
CAUSE_P = {
    "auto": {"collision_third_party": 0.34, "collision_single": 0.24, "theft": 0.10, "glass": 0.22, "weather": 0.10},
    "home": {"water_escape": 0.36, "storm": 0.24, "fire": 0.06, "theft": 0.12, "accidental_damage": 0.22},
    "commercial": {"fire": 0.14, "water_escape": 0.26, "storm": 0.20, "theft": 0.16, "liability": 0.24},
}
TP_FAULT_P = {"collision_third_party": 0.72, "water_escape": 0.30, "liability": 0.25, "fire": 0.10, "theft": 0.04}
BASE_ESTIMATE = {"auto": 3200.0, "home": 5200.0, "commercial": 16000.0}
CHANNELS = ("app", "phone", "broker")
QUEUES = ("fast_track", "standard", "complex")
FT, STD, CX = 0, 1, 2
CLAIMS_PER_DAY = 46
CAPACITY = (18.0, 58.0, 46.0)  # adjuster-days per day in each queue
MIN_DAYS = (1, 6, 12)  # days a claim waits in each queue before adjusters can work it (inspections, documents)
WORK = ((1.0, 1.0), (2.0, 4.0), (2.5, 5.0))  # [queue][complex]: adjuster-days to finish; fast track only assesses a complex claim
REOPEN_WORK = 2.0
REOPEN_WAIT = 3  # a reopened claim waits three days in the complex unit, ahead of new work
REVIEW_CAPACITY = 10  # leakage reviews per day before payment
REFERRAL_CAPACITY = 8  # subrogation referrals per day
AUDITS_PER_DAY = 6  # random closed-file audits after payment (measurement only)
REVIEW_CATCH = 0.85
REVIEW_COST = 90.0
REFERRAL_COST = 250.0
RECOVERY_SHARE = 0.6
RECOVERY_SUCCESS = 0.75
REOPEN_COST = 400.0  # extra handling expense when a paid claim is reopened

FUTURE, NEW, OPEN, PENDING, CLOSED = range(5)


@dataclass
class Claims:
    """Claim attributes the insurer knows (index i is claim CLM-{i+1:06d}). Invoices and the repairer are
    known only at payment; the policy sees them only for claims waiting for payment."""

    report_day: np.ndarray
    late: np.ndarray  # days from loss to report
    district: np.ndarray
    office: np.ndarray
    line: np.ndarray
    cause: np.ndarray
    channel: np.ndarray
    estimate: np.ndarray
    injury: np.ndarray
    police: np.ndarray
    tp_flag: np.ndarray  # the intake desk ticked "third party involved"
    photos: np.ndarray
    tenure: np.ndarray
    prior: np.ndarray
    invoices: np.ndarray
    nonnetwork: np.ndarray  # repairer outside the approved network

    @property
    def n(self) -> int:
        return len(self.report_day)

    @property
    def group(self) -> np.ndarray:
        return (np.array(DISTRICT_GROUP)[self.district] == "G2").astype(int)

    def ids(self) -> list[str]:
        return [f"CLM-{i + 1:06d}" for i in range(self.n)]


@dataclass
class Hidden:
    """Ground truth the insurer never observes."""

    complex: np.ndarray
    true_cost: np.ndarray
    leak_logit: np.ndarray
    overpay_share: np.ndarray
    tp_fault: np.ndarray


def _sig(x):
    return 1 / (1 + np.exp(-x))


def _draw_claims(rng: np.random.Generator, first_day: int, last_day: int) -> tuple[Claims, Hidden]:
    days = []
    for d in range(first_day, last_day):
        days += [d] * int(rng.poisson(CLAIMS_PER_DAY))
    n = len(days)
    district = rng.integers(0, len(DISTRICTS), n)
    g2 = np.array(DISTRICT_GROUP)[district] == "G2"
    line = rng.choice(len(LINES), n, p=LINE_P)
    cause = np.array([CAUSES.index(rng.choice(list(CAUSE_P[LINES[k]]), p=list(CAUSE_P[LINES[k]].values()))) for k in line])
    cname = np.array(CAUSES)[cause]
    late = rng.geometric(np.where(g2, 0.30, 0.45)) - 1
    channel = np.array([rng.choice(3, p=(0.35, 0.50, 0.15) if g else (0.55, 0.30, 0.15)) for g in g2])
    estimate = np.round(np.array([BASE_ESTIMATE[LINES[k]] for k in line]) * rng.lognormal(0, 0.8, n), -1)
    injury = rng.random(n) < np.where(cname == "collision_third_party", 0.14, np.where(cname == "liability", 0.40, 0.02))
    tp_fault = rng.random(n) < np.array([TP_FAULT_P.get(c, 0.02) for c in cname])
    police = rng.random(n) < np.where(np.isin(cname, ["collision_third_party", "theft"]), np.where(tp_fault, 0.75, 0.45), 0.05)
    tp_flag = np.where(tp_fault, rng.random(n) < 0.5, rng.random(n) < 0.04)
    prior = rng.poisson(0.4, n)
    cx_logit = (
        -2.0
        + 1.1 * np.log(estimate / 4000)
        + 1.8 * injury
        + 0.9 * (line == LINES.index("commercial"))
        + 0.7 * np.isin(cname, ["fire", "liability"])
        + 0.4 * (cname == "water_escape")
        + 0.05 * np.minimum(late, 14)
        + rng.normal(0, 0.9, n)
    )
    complex_ = rng.random(n) < _sig(cx_logit)
    true_cost = np.round(estimate * rng.lognormal(0, 0.3, n) * np.where(complex_, 1.5, 1.0), 2)
    leak_logit = -2.1 + 0.10 * np.minimum(late, 14) + 0.35 * prior + 0.5 * np.isin(cname, ["theft", "water_escape"]) + rng.normal(0, 1.0, n)
    pl = _sig(leak_logit)
    claims = Claims(
        report_day=np.array(days, int),
        late=late.astype(int),
        district=district,
        office=district % len(OFFICES),
        line=line,
        cause=cause,
        channel=channel,
        estimate=estimate,
        injury=injury,
        police=police,
        tp_flag=tp_flag,
        photos=rng.random(n) < np.where(channel == 0, 0.85, 0.45),
        tenure=rng.geometric(0.18, n) - 1,
        prior=prior,
        invoices=1 + rng.poisson(0.5 + 1.6 * pl),
        nonnetwork=rng.random(n) < 0.2 + 0.4 * pl,
    )
    return claims, Hidden(complex_, true_cost, leak_logit, np.round(rng.uniform(0.12, 0.40, n), 3), tp_fault)


def _concat(a, b):
    return type(a)(**{k: np.concatenate([getattr(a, k), getattr(b, k)]) for k in type(a).__dataclass_fields__})


@dataclass
class World:
    claims: Claims
    hidden: Hidden

    @property
    def n(self) -> int:
        return self.claims.n


def make_world(seed: int = 23) -> World:
    return World(*_draw_claims(np.random.default_rng(seed), 0, DAYS_HISTORY))


def extend_world(w: World, seed: int) -> World:
    """The world for one forward replication: new claims reported on days 180..207."""
    c, h = _draw_claims(np.random.default_rng(seed), DAYS_HISTORY, DAYS_TOTAL)
    return World(_concat(w.claims, c), _concat(w.hidden, h))


ARRAYS = (
    "status",
    "queue",
    "first_queue",
    "entered",
    "remaining",
    "escalated",
    "assessed",
    "ready_day",
    "proposed",
    "leak",
    "reopen_day",
    "reopened",
)


@dataclass
class State:
    status: np.ndarray
    queue: np.ndarray
    first_queue: np.ndarray
    entered: np.ndarray
    remaining: np.ndarray
    escalated: np.ndarray
    assessed: np.ndarray
    ready_day: np.ndarray
    proposed: np.ndarray
    leak: np.ndarray
    reopen_day: np.ndarray
    reopened: np.ndarray
    ground_truth: Hidden
    record: bool = False
    events: dict[str, list] = field(default_factory=dict)
    totals: dict[str, float] = field(default_factory=dict)

    def copy(self) -> State:
        return State(*(getattr(self, k).copy() for k in ARRAYS), self.ground_truth, False, {}, dict.fromkeys(self.totals, 0.0))


TOTALS = (
    *(f"{k}_{g}" for g in GROUPS for k in ("assigned", "fast_tracked", "paid", "reviews", "referrals", "cycle_days")),
    "paid_usd", "overpayment_paid_usd", "overpayment_caught_usd", "recoveries_usd", "missed_recovery_usd", "review_cost_usd",
    "referral_cost_usd", "reopen_cost_usd", "paid", "cycle_days", "reopened", "reviews", "referrals", "escalations", "assigned",
    "fast_tracked", "backlog_spread", "days", "audits",
)  # fmt: skip


def new_state(w: World, record: bool) -> State:
    n = w.n
    return State(
        status=np.full(n, FUTURE),
        queue=np.full(n, -1),
        first_queue=np.full(n, -1),
        entered=np.full(n, -1),
        remaining=np.zeros(n),
        escalated=np.zeros(n, bool),
        assessed=np.zeros(n, bool),
        ready_day=np.full(n, -1),
        proposed=np.zeros(n),
        leak=np.zeros(n, bool),
        reopen_day=np.full(n, -1),
        reopened=np.zeros(n, bool),
        ground_truth=w.hidden,
        record=record,
        events={k: [] for k in ("assignments", "claim_events", "payments", "audits", "referrals", "snapshots")},
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
    st.ground_truth = w.hidden
    return st


@dataclass(frozen=True)
class View:
    """What the insurer can observe on the morning of day t (everything through day t-1).

    `new` are the claims reported yesterday, waiting for a queue; `pay` are the claims whose work finished
    yesterday, waiting for payment (with the proposed payment, invoices and repairer)."""

    t: int
    new: np.ndarray
    pay: np.ndarray
    line: np.ndarray  # for new, then pay (indexed by position in np.concatenate([new, pay]))
    cause: np.ndarray
    channel: np.ndarray
    estimate: np.ndarray
    injury: np.ndarray
    police: np.ndarray
    tp_flag: np.ndarray
    photos: np.ndarray
    late: np.ndarray
    tenure: np.ndarray
    prior: np.ndarray
    proposed: np.ndarray  # pay claims only
    invoices: np.ndarray
    nonnetwork: np.ndarray
    queue: np.ndarray  # pay claims: the queue that finished the claim
    escalated: np.ndarray
    days_open: np.ndarray
    open_by_queue: tuple[int, int, int]

    def part(self, which: str, a: np.ndarray) -> np.ndarray:
        return a[: len(self.new)] if which == "new" else a[len(self.new) :]


def view(w: World, st: State, t: int) -> View:
    c = w.claims
    new = np.flatnonzero(st.status == NEW)
    pay = np.flatnonzero(st.status == PENDING)
    both = np.concatenate([new, pay])
    return View(
        t,
        new,
        pay,
        c.line[both],
        c.cause[both],
        c.channel[both],
        c.estimate[both],
        c.injury[both].astype(int),
        c.police[both].astype(int),
        c.tp_flag[both].astype(int),
        c.photos[both].astype(int),
        c.late[both],
        c.tenure[both],
        c.prior[both],
        st.proposed[pay],
        c.invoices[pay],
        c.nonnetwork[pay].astype(int),
        st.queue[pay],
        st.escalated[pay].astype(int),
        t - c.report_day[pay],
        tuple(int(((st.status == OPEN) & (st.queue == q)).sum()) for q in range(3)),
    )


@dataclass
class Decision:
    queue: np.ndarray  # one queue per claim in View.new
    reviews: np.ndarray  # claim indices among View.pay
    referrals: np.ndarray


class Policy:
    name = "policy"

    def decide(self, v: View) -> Decision:  # pragma: no cover - interface
        raise NotImplementedError


class CurrentRules(Policy):
    """How Ferrowind works today: injury, commercial or an estimate above $15,000 goes to the complex unit,
    an estimate below $3,000 to fast track, everything else to standard; review the largest proposed
    payments; refer to subrogation only the claims the intake desk flagged as third party, largest first."""

    name = "current rules"

    def decide(self, v: View) -> Decision:
        est, inj, line = v.part("new", v.estimate), v.part("new", v.injury), v.part("new", v.line)
        q = np.where((inj == 1) | (line == LINES.index("commercial")) | (est > 15000), CX, np.where(est < 3000, FT, STD))
        order = np.lexsort((v.pay, -v.proposed))
        reviews = v.pay[order[:REVIEW_CAPACITY]]
        flagged = [i for i in order if v.part("pay", v.tp_flag)[i] == 1]
        return Decision(q, reviews, v.pay[np.array(flagged[:REFERRAL_CAPACITY], int)])


def _rng(seed: int, t: int) -> np.random.Generator:
    return np.random.default_rng([seed, t])


def step(w: World, st: State, t: int, policy: Policy, seed: int) -> None:
    """Play day t: morning decisions and payments, reopenings, the day's work, then new claims at the end of the day."""
    c, h = w.claims, st.ground_truth
    n = w.n
    rng = _rng(seed, t)
    u_rev, u_sub, u_reo, u_aud, u_leak, u_day = (rng.random(n) for _ in range(6))
    rec, tot = st.record, st.totals
    grp = c.group
    if t > 0:
        v = view(w, st, t)
        d = policy.decide(v)
        # queue assignment
        if len(v.new):
            q = np.asarray(d.queue, int)
            st.queue[v.new] = q
            st.first_queue[v.new] = q
            st.entered[v.new] = t
            st.remaining[v.new] = np.array(WORK)[q, h.complex[v.new].astype(int)]
            st.status[v.new] = OPEN
            tot["assigned"] += len(v.new)
            tot["fast_tracked"] += int((q == FT).sum())
            for g, name in enumerate(GROUPS):
                m = grp[v.new] == g
                tot[f"assigned_{name}"] += int(m.sum())
                tot[f"fast_tracked_{name}"] += int((q[m] == FT).sum())
            if rec:
                st.events["assignments"] += [(int(i), t, QUEUES[k], "triage") for i, k in zip(v.new, q, strict=True)]
        # payment of yesterday's finished claims
        pay = v.pay
        if len(pay):
            reviewed = np.isin(pay, d.reviews)
            referred = np.isin(pay, d.referrals)
            over = h.true_cost[pay] * h.overpay_share[pay] * st.leak[pay]
            caught = reviewed & st.leak[pay] & (u_rev[pay] < REVIEW_CATCH)
            paid = st.proposed[pay] - np.where(caught, over, 0.0)
            recovered = referred & h.tp_fault[pay] & (u_sub[pay] < RECOVERY_SUCCESS)
            recovery = np.where(recovered, RECOVERY_SHARE * h.true_cost[pay], 0.0)
            missed = h.tp_fault[pay] & ~referred
            tot["paid_usd"] += float(paid.sum())
            tot["overpayment_paid_usd"] += float(np.where(caught, 0.0, over).sum())
            tot["overpayment_caught_usd"] += float(np.where(caught, over, 0.0).sum())
            tot["recoveries_usd"] += float(recovery.sum())
            tot["missed_recovery_usd"] += float((RECOVERY_SHARE * RECOVERY_SUCCESS * h.true_cost[pay] * missed).sum())
            tot["review_cost_usd"] += REVIEW_COST * int(reviewed.sum())
            tot["referral_cost_usd"] += REFERRAL_COST * int(referred.sum())
            tot["reviews"] += int(reviewed.sum())
            tot["referrals"] += int(referred.sum())
            tot["paid"] += len(pay)
            cyc = t - c.report_day[pay]
            tot["cycle_days"] += float(cyc.sum())
            for g, name in enumerate(GROUPS):
                m = grp[pay] == g
                tot[f"paid_{name}"] += int(m.sum())
                tot[f"reviews_{name}"] += int(reviewed[m].sum())
                tot[f"referrals_{name}"] += int(referred[m].sum())
                tot[f"cycle_days_{name}"] += float(cyc[m].sum())
            st.status[pay] = CLOSED
            p_reopen = 0.03 + 0.15 * (h.complex[pay] & (st.queue[pay] == STD)) + 0.04 * st.escalated[pay]
            reo = u_reo[pay] < p_reopen
            st.reopen_day[pay[reo]] = t + 5 + (u_day[pay[reo]] * 17).astype(int)
            audited = pay[np.argsort(u_aud[pay], kind="mergesort")[:AUDITS_PER_DAY]]
            tot["audits"] += len(audited)
            if rec:
                st.events["payments"] += [
                    (int(i), t, float(st.proposed[i]), round(float(p), 2), bool(r), round(float(o) if k else 0.0, 2))
                    for i, p, r, o, k in zip(pay, paid, reviewed, over, caught, strict=True)
                ]
                st.events["claim_events"] += [(int(i), t, "paid", "") for i in pay]
                st.events["referrals"] += [
                    (int(i), t, round(float(r), 2), "recovered" if r > 0 else "unsuccessful")
                    for i, r in zip(pay[referred], recovery[referred], strict=True)
                ]
                st.events["audits"] += [
                    (int(i), t, round(float(h.true_cost[i] * h.overpay_share[i] * st.leak[i]), 2), bool(h.tp_fault[i])) for i in audited
                ]
    # reopenings
    reo = np.flatnonzero((st.status == CLOSED) & (st.reopen_day == t) & ~st.reopened)
    if len(reo):
        st.status[reo] = OPEN
        st.queue[reo] = CX
        st.entered[reo] = t - MIN_DAYS[CX] + REOPEN_WAIT
        st.remaining[reo] = REOPEN_WORK
        st.reopened[reo] = True
        tot["reopened"] += len(reo)
        tot["reopen_cost_usd"] += REOPEN_COST * len(reo)
        if rec:
            st.events["claim_events"] += [(int(i), t, "reopened", "") for i in reo]
            st.events["assignments"] += [(int(i), t, "complex", "reopen") for i in reo]
    # the day's work, first in first out within each queue
    backlog = []
    for q in range(3):
        ids = np.flatnonzero((st.status == OPEN) & (st.queue == q) & (st.entered + MIN_DAYS[q] <= t))
        order = ids[np.lexsort((ids, st.entered[ids]))]
        rem = st.remaining[order]
        before = np.concatenate([[0.0], np.cumsum(rem)[:-1]])
        work = np.clip(CAPACITY[q] - before, 0, rem)
        st.remaining[order] = rem - work
        worked = order[work > 0]
        first = worked[~st.assessed[worked]]
        st.assessed[first] = True
        if rec:
            st.events["claim_events"] += [(int(i), t, "assessed", "complex" if h.complex[i] else "simple") for i in first]
        done = order[st.remaining[order] <= 1e-9]
        if q == FT:
            esc = done[h.complex[done]]
            st.queue[esc] = CX
            st.entered[esc] = t
            st.remaining[esc] = WORK[CX][1]
            st.escalated[esc] = True
            tot["escalations"] += len(esc)
            if rec:
                st.events["assignments"] += [(int(i), t, "complex", "escalation") for i in esc]
            done = done[~h.complex[done]]
        again = done[st.reopened[done]]
        st.status[again] = CLOSED
        first_time = done[~st.reopened[done]]
        st.status[first_time] = PENDING
        st.ready_day[first_time] = t
        p_leak = _sig(h.leak_logit[first_time] + 0.7 * (q == FT) + 0.4 * (h.complex[first_time] & (q == STD)))
        st.leak[first_time] = u_leak[first_time] < p_leak
        st.proposed[first_time] = np.round(h.true_cost[first_time] * (1 + h.overpay_share[first_time] * st.leak[first_time]), 2)
        if rec:
            st.events["claim_events"] += [(int(i), t, "ready", "") for i in first_time]
            st.events["claim_events"] += [(int(i), t, "closed", "after reopening") for i in again]
    for q in range(3):  # adjuster-days of work ready to be done (past the minimum wait), in days of the queue's capacity
        backlog.append(st.remaining[(st.status == OPEN) & (st.queue == q) & (st.entered + MIN_DAYS[q] <= t + 1)].sum() / CAPACITY[q])
    tot["backlog_spread"] += float(max(backlog) - min(backlog))
    if rec:
        st.events["snapshots"] += [(t, QUEUES[q], round(float(b), 3)) for q, b in enumerate(backlog)]
    tot["days"] += 1
    # new claims reported today, triaged tomorrow morning
    new = (st.status == FUTURE) & (c.report_day == t)
    st.status[new] = NEW
    if rec:
        st.events["claim_events"] += [(int(i), t, "reported", "") for i in np.flatnonzero(new)]


def run(w: World, st: State, days: range, policy: Policy, seed: int) -> State:
    for t in days:
        step(w, st, t, policy, seed)
    return st
