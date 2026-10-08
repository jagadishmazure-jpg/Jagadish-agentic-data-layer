"""The simulated lender: Quillmere Home Loans (fictional) and its locked loan pipeline.

One daily engine (`step`) plays both the 180-day history and the 28-day forward simulation, so the
data the models learn from and the world the value is measured in follow the same rules:

* Applications arrive every day and lock a rate a few days later for 30, 45 or 60 days. Each lock
  opens document conditions the borrower must clear, and processing work that moves faster once the
  documents are in.
* A borrower can withdraw on any day. The hazard rises when the market rate falls below the locked
  rate (the borrower can shop elsewhere), when nobody has reached them for a while, and with hidden
  traits (rate shopping, responsiveness) that the lender never observes directly.
* A reached call keeps the borrower engaged for a few days. A document chase speeds up the return of
  outstanding documents. A lock extension buys seven more days for a fee the lender absorbs. A lock
  that expires is relocked at the market rate: if the market has fallen the lender's hedge loses the
  difference; if it has risen some borrowers walk away.
* A small share of applications is declined by underwriting (credit). Agents cannot influence that.

Hidden traits live in `State.ground_truth`. They drive behaviour here and in the evaluation harness
(`adl.domains.mortgage.simulate`) but are never written to bronze, never reach a data product and are
never read by product code (tests/test_models.py checks this). Policies receive a `View` with only
what the lender can observe.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

DAYS_HISTORY = 180  # observed history: day 0 .. 179
HORIZON = 28
DAYS_TOTAL = DAYS_HISTORY + HORIZON
REGIONS = {"north": ("B01", "B02", "B03"), "south": ("B04", "B05", "B06")}
BRANCH_NAMES = {"B01": "Alder Row", "B02": "Millbrook", "B03": "Fenwick Hill", "B04": "Saltmarsh", "B05": "Orchard Gate", "B06": "Pellham Quay"}
BRANCH_REGION = {b: r for r, bs in REGIONS.items() for b in bs}
LOAN_OFFICERS = {f"LO{i + 1:02d}": b for i, b in enumerate(b for b in sorted(BRANCH_NAMES) for _ in range(2))}
PRODUCTS = ("conv30", "conv15", "fha", "va", "jumbo")
PRODUCT_P = (0.48, 0.12, 0.18, 0.10, 0.12)
SPREAD = {"conv30": 0.0, "conv15": -0.55, "fha": -0.2, "va": -0.3, "jumbo": 0.15}  # rate points over the market rate
GAIN_ON_SALE = {"conv30": 0.0150, "conv15": 0.0130, "fha": 0.0210, "va": 0.0200, "jumbo": 0.0100}  # share of the loan amount
AMOUNT = {"conv30": 340_000, "conv15": 260_000, "fha": 255_000, "va": 310_000, "jumbo": 920_000}
EXTRA_WORK = {"conv30": 0, "conv15": 0, "fha": 4, "va": 4, "jumbo": 6}
CHANNELS = ("retail", "broker", "direct")
CHANNEL_P = (0.5, 0.3, 0.2)
CHANNEL_SHOP = (1.0, 1.7, 1.3)
PURPOSES = ("purchase", "refinance")
PURPOSE_P = (0.62, 0.38)
PURPOSE_SHOP = (0.7, 1.6)
LOCK_TERMS = (30, 45, 60)
LOCK_P = (0.45, 0.40, 0.15)
STAGES = ("processing", "underwriting", "conditional_approval", "clear_to_close")
STAGE_AT = (0.0, 0.3, 0.6, 0.9)  # share of the work done when each stage starts; clear to close also needs every condition cleared
TERMINAL = ("closed", "withdrawn", "expired", "denied")
FALLOUT = ("withdrawn", "expired", "denied")
CONDITION_TYPES = ("income", "assets", "employment", "appraisal", "insurance", "title", "explanation_letter")
APPS_PER_DAY = 22
CALL_CAPACITY = 60  # outbound calls the loan officers can make per day, company-wide
CHASE_CAPACITY = 60  # document chases the processors can send per day
EXTENSION_DAYS = 7
EXTENSION_FEE = 0.00125  # share of the loan amount per 7-day extension, absorbed by the lender
RELOCK_DAYS = 15
HEDGE_DURATION = 4.0  # hedge loss per dollar of loan per 1.00 rate point the market sits below the locked rate
CALL_COST = 14.0  # loaded cost of one outbound call (USD)
CHASE_COST = 5.0  # loaded cost of one document chase (USD)
TYPICAL_WORK = 30  # processing days for a typical file once documents are in (used by policies as a planning figure)

PENDING, ACTIVE, CLOSED, WITHDRAWN, EXPIRED, DENIED = range(6)
STATUS_NAME = {CLOSED: "closed", WITHDRAWN: "withdrawn", EXPIRED: "expired", DENIED: "denied"}


@dataclass
class Apps:
    """Static application attributes the lender knows (index i is application APP-{i+1:05d})."""

    app_day: np.ndarray
    lock_day: np.ndarray
    term: np.ndarray
    branch: np.ndarray  # index into sorted(BRANCH_NAMES)
    lo: np.ndarray  # index into sorted(LOAN_OFFICERS)
    product: np.ndarray
    channel: np.ndarray
    purpose: np.ndarray
    amount: np.ndarray
    ltv: np.ndarray
    n_cond: np.ndarray
    cond_types: list[tuple[str, ...]]
    rate_noise: np.ndarray

    @property
    def n(self) -> int:
        return len(self.app_day)

    def ids(self) -> list[str]:
        return [f"APP-{i + 1:05d}" for i in range(self.n)]


@dataclass
class Hidden:
    """Ground truth the lender never observes."""

    responsiveness: np.ndarray  # chance per day of returning an outstanding document, before any chase
    shop: np.ndarray  # rate-shopping propensity multiplier
    work: np.ndarray  # processing days needed once documents are in


def _draw_apps(rng: np.random.Generator, first_day: int, last_day: int) -> tuple[Apps, Hidden]:
    days = []
    for d in range(first_day, last_day):
        days += [d] * int(rng.poisson(APPS_PER_DAY))
    n = len(days)
    app_day = np.array(days, int)
    product = rng.choice(len(PRODUCTS), n, p=PRODUCT_P)
    channel = rng.choice(len(CHANNELS), n, p=CHANNEL_P)
    purpose = rng.choice(len(PURPOSES), n, p=PURPOSE_P)
    branch = rng.integers(0, len(BRANCH_NAMES), n)
    lo = branch * 2 + rng.integers(0, 2, n)
    amount = np.array([AMOUNT[PRODUCTS[p]] for p in product]) * rng.lognormal(0, 0.25, n)
    n_cond = np.clip(rng.poisson(3.0, n) + 1, 1, 7)
    cond_types = [tuple(rng.choice(CONDITION_TYPES, k, replace=False)) for k in n_cond]
    apps = Apps(
        app_day=app_day,
        lock_day=app_day + rng.integers(0, 6, n),
        term=rng.choice(LOCK_TERMS, n, p=LOCK_P),
        branch=branch,
        lo=lo,
        product=product,
        channel=channel,
        purpose=purpose,
        amount=np.round(amount, -3),
        ltv=np.round(np.clip(rng.normal(78, 9, n), 40, 97), 1),
        n_cond=n_cond,
        cond_types=cond_types,
        rate_noise=np.round(rng.normal(0, 0.08, n), 3),
    )
    shop = np.array(CHANNEL_SHOP)[channel] * np.array(PURPOSE_SHOP)[purpose] * rng.lognormal(0, 0.5, n)
    work = rng.integers(16, 34, n) + np.array([EXTRA_WORK[PRODUCTS[p]] for p in product])
    hidden = Hidden(np.clip(rng.beta(3, 3, n), 0.08, 0.95), shop, work)
    return apps, hidden


def _concat(a: Apps, b: Apps) -> Apps:
    return Apps(
        **{
            k: (getattr(a, k) + getattr(b, k)) if k == "cond_types" else np.concatenate([getattr(a, k), getattr(b, k)])
            for k in Apps.__dataclass_fields__
        }
    )


def market_path(rng: np.random.Generator, start: float, days: int, drift: np.ndarray | None = None) -> np.ndarray:
    steps = rng.normal(0, 0.035, days) + (drift if drift is not None else 0.0)
    return np.round(start + np.cumsum(steps), 4)


@dataclass
class World:
    apps: Apps
    hidden: Hidden
    rates: np.ndarray  # 30-year market rate (percent) per day; forward days filled per replication

    @property
    def n(self) -> int:
        return self.apps.n


def make_world(seed: int = 11) -> World:
    rng = np.random.default_rng(seed)
    apps, hidden = _draw_apps(rng, 0, DAYS_HISTORY)
    drift = np.zeros(DAYS_HISTORY)
    drift[95:140] = -0.009  # a rally: rates fall for six weeks, borrowers shop
    drift[140:165] = 0.004
    rates = np.full(DAYS_TOTAL, np.nan)
    rates[:DAYS_HISTORY] = market_path(rng, 6.80, DAYS_HISTORY, drift)
    return World(apps, hidden, rates)


def extend_world(w: World, seed: int) -> World:
    """The world for one forward replication: new applications and a new market path for days 180..207."""
    rng = np.random.default_rng(seed)
    apps, hidden = _draw_apps(rng, DAYS_HISTORY, DAYS_TOTAL)
    rates = w.rates.copy()
    rates[DAYS_HISTORY:] = market_path(rng, w.rates[DAYS_HISTORY - 1], HORIZON)
    hid = Hidden(*(np.concatenate([getattr(w.hidden, k), getattr(hidden, k)]) for k in Hidden.__dataclass_fields__))
    return World(_concat(w.apps, apps), hid, rates)


@dataclass
class State:
    status: np.ndarray
    locked_rate: np.ndarray
    expiry: np.ndarray
    extensions: np.ndarray
    relocks: np.ndarray
    docs_out: np.ndarray
    stage: np.ndarray
    stage_since: np.ndarray
    last_contact: np.ndarray
    retained_until: np.ndarray
    chase_until: np.ndarray
    progress: np.ndarray
    unanswered: np.ndarray  # (n, days): calls that did not reach the borrower
    end_day: np.ndarray  # day of the terminal event
    ground_truth: Hidden
    record: bool = False
    events: dict[str, list] = field(default_factory=dict)
    totals: dict[str, float] = field(default_factory=dict)

    def copy(self) -> State:
        return State(
            *(getattr(self, k).copy() for k in list(State.__dataclass_fields__)[:14]),
            self.ground_truth,
            False,
            {},
            dict.fromkeys(self.totals, 0.0),
        )


TOTALS = (
    "gain_on_sale_usd",
    "hedge_loss_usd",
    "extension_cost_usd",
    "outreach_cost_usd",
    "closed",
    "fallout",
    "withdrawn",
    "expired",
    "denied",
    "extensions",
    "calls",
    "chases",
    "lock_to_close_days",
)


def new_state(w: World, record: bool) -> State:
    n = w.n
    z = np.zeros(n, int)
    return State(
        status=np.full(n, PENDING),
        locked_rate=np.zeros(n),
        expiry=z.copy(),
        extensions=z.copy(),
        relocks=z.copy(),
        docs_out=z.copy(),
        stage=z.copy(),
        stage_since=z.copy(),
        last_contact=z.copy(),
        retained_until=np.full(n, -1),
        chase_until=np.full(n, -1),
        progress=np.zeros(n),
        unanswered=np.zeros((n, DAYS_TOTAL), np.int8),
        end_day=np.full(n, -1),
        ground_truth=w.hidden,
        record=record,
        events={k: [] for k in ("locks", "stages", "conditions", "contacts")},
        totals=dict.fromkeys(TOTALS, 0.0),
    )


def resize(st: State, w: World) -> State:
    """Grow a state to a world with more applications (new ones start pending)."""
    extra = w.n - len(st.status)
    if extra <= 0:
        return st
    fresh = new_state(w, False)
    for k in list(State.__dataclass_fields__)[:14]:
        a, b = getattr(st, k), getattr(fresh, k)
        setattr(st, k, np.concatenate([a, b[len(a) :]]))
    st.ground_truth = w.hidden
    return st


@dataclass(frozen=True)
class View:
    """What the lender can observe on the morning of day t (everything through day t-1)."""

    t: int
    idx: np.ndarray  # active applications
    market_rate: float  # yesterday's close
    locked_rate: np.ndarray
    expiry: np.ndarray
    extensions: np.ndarray
    docs_out: np.ndarray
    n_cond: np.ndarray
    stage: np.ndarray
    stage_since: np.ndarray
    last_contact: np.ndarray
    unanswered_7d: np.ndarray
    lock_day: np.ndarray
    channel: np.ndarray
    purpose: np.ndarray
    product: np.ndarray
    amount: np.ndarray


def view(w: World, st: State, t: int) -> View:
    idx = np.flatnonzero(st.status == ACTIVE)
    a = w.apps
    return View(
        t,
        idx,
        float(w.rates[t - 1]),
        st.locked_rate[idx],
        st.expiry[idx],
        st.extensions[idx],
        st.docs_out[idx],
        a.n_cond[idx],
        st.stage[idx],
        st.stage_since[idx],
        st.last_contact[idx],
        st.unanswered[idx, max(t - 7, 0) : t].sum(1).astype(int),
        a.lock_day[idx],
        a.channel[idx],
        a.purpose[idx],
        a.product[idx],
        a.amount[idx],
    )


@dataclass
class Decision:
    calls: np.ndarray  # application indices
    chases: np.ndarray
    extend: np.ndarray


class Policy:
    name = "policy"

    def decide(self, v: View) -> Decision:  # pragma: no cover - interface
        raise NotImplementedError


class CurrentRules(Policy):
    """How Quillmere works today: call and chase whoever is within 10 days of lock expiry, closest
    first, up to capacity; extend every lock that reaches expiry unclosed (up to three times)."""

    name = "current rules"

    def decide(self, v: View) -> Decision:
        dte = v.expiry - v.t
        near = dte <= 10
        order = np.lexsort((v.idx, dte))
        calls = [i for i in order if near[i]][:CALL_CAPACITY]
        chases = [i for i in order if near[i] and v.docs_out[i] > 0][:CHASE_CAPACITY]
        extend = np.flatnonzero((dte == 0) & (v.extensions < 3))
        return Decision(v.idx[calls], v.idx[chases], v.idx[extend])


def _rng(seed: int, t: int) -> np.random.Generator:
    return np.random.default_rng([seed, t])


def step(w: World, st: State, t: int, policy: Policy, seed: int) -> None:
    """Play day t: morning decisions, then the day's events, then new locks at the end of the day."""
    a, h = w.apps, st.ground_truth
    n = w.n
    rng = _rng(seed, t)
    u_reach, u_doc, u_prog, u_den, u_wd, u_exp = (rng.random(n) for _ in range(6))
    rec = st.record
    tot = st.totals
    if t > 0 and (st.status == ACTIVE).any():
        d = policy.decide(view(w, st, t))
        ext = d.extend[(st.status[d.extend] == ACTIVE) & (st.expiry[d.extend] == t)]
        st.expiry[ext] += EXTENSION_DAYS
        st.extensions[ext] += 1
        fee = np.round(a.amount[ext] * EXTENSION_FEE, 2)
        tot["extension_cost_usd"] += float(fee.sum())
        tot["extensions"] += len(ext)
        if rec:
            st.events["locks"] += [
                (int(i), "extension", t, int(st.expiry[i]), float(st.locked_rate[i]), float(f)) for i, f in zip(ext, fee, strict=True)
            ]
        chases = d.chases[st.status[d.chases] == ACTIVE]
        st.chase_until[chases] = t + 2
        tot["chases"] += len(chases)
        tot["outreach_cost_usd"] += CHASE_COST * len(chases)
        calls = d.calls[st.status[d.calls] == ACTIVE]
        reached = u_reach[calls] < 0.3 + 0.6 * h.responsiveness[calls]
        st.last_contact[calls[reached]] = t
        st.retained_until[calls[reached]] = t + 5
        st.unanswered[calls[~reached], t] += 1
        tot["calls"] += len(calls)
        tot["outreach_cost_usd"] += CALL_COST * len(calls)
        if rec:
            st.events["contacts"] += [(int(i), t, "document_chase", "sent") for i in chases]
            st.events["contacts"] += [(int(i), t, "call", "reached" if r else "no_answer") for i, r in zip(calls, reached, strict=True)]

    act = st.status == ACTIVE
    # documents
    p_doc = np.minimum(0.12 + 0.35 * h.responsiveness + 0.3 * (st.chase_until >= t), 0.95)
    got = act & (st.docs_out > 0) & (u_doc < p_doc)
    if rec:
        st.events["conditions"] += [(int(i), int(a.n_cond[i] - st.docs_out[i]), t) for i in np.flatnonzero(got)]
    st.docs_out[got] -= 1
    # processing work
    st.progress[act] += u_prog[act] < np.where(st.docs_out[act] == 0, 0.95, 0.45)
    frac = st.progress / np.maximum(h.work, 1)
    new_stage = np.searchsorted(np.array(STAGE_AT), frac, side="right") - 1
    new_stage = np.where((new_stage == 3) & (st.docs_out > 0), 2, new_stage)
    moved = act & (new_stage > st.stage)
    st.stage[moved] = new_stage[moved]
    st.stage_since[moved] = t
    if rec:
        st.events["stages"] += [(int(i), t, STAGES[st.stage[i]]) for i in np.flatnonzero(moved)]
    # outcomes
    closed = act & (st.progress >= h.work) & (st.docs_out == 0)
    gap = st.locked_rate - w.rates[t]
    denied = act & ~closed & (u_den < 0.0006)
    hazard = (
        0.0022
        * h.shop
        * np.exp(4.5 * np.clip(gap, -0.5, 0.8))
        * (1 + 0.05 * np.minimum(t - st.last_contact, 21))
        * np.where(st.retained_until >= t, 0.3, 1.0)
    )
    withdrawn = act & ~closed & ~denied & (u_wd < hazard)
    expiring = act & ~closed & ~denied & ~withdrawn & (st.expiry == t)
    rise = w.rates[t] - st.locked_rate
    walk = expiring & (u_exp < np.clip(2.5 * rise, 0, 0.9))
    relock = expiring & ~walk
    gos = a.amount * np.array([GAIN_ON_SALE[p] for p in PRODUCTS])[a.product]
    tot["gain_on_sale_usd"] += float(gos[closed].sum())
    tot["lock_to_close_days"] += float((t - a.lock_day[closed]).sum())
    hedge = a.amount * np.maximum(gap, 0) * HEDGE_DURATION / 100
    tot["hedge_loss_usd"] += float(hedge[withdrawn].sum() + hedge[relock].sum())
    for mask, code in ((closed, CLOSED), (withdrawn, WITHDRAWN), (walk, EXPIRED), (denied, DENIED)):
        st.status[mask] = code
        st.end_day[mask] = t
        tot[STATUS_NAME[code]] += int(mask.sum())
        if rec:
            st.events["stages"] += [(int(i), t, STATUS_NAME[code]) for i in np.flatnonzero(mask)]
    tot["fallout"] += int(withdrawn.sum() + walk.sum() + denied.sum())
    st.locked_rate[relock] = np.round(w.rates[t] + np.array([SPREAD[p] for p in PRODUCTS])[a.product[relock]] + a.rate_noise[relock], 3)
    st.expiry[relock] = t + RELOCK_DAYS
    st.relocks[relock] += 1
    if rec:
        st.events["locks"] += [(int(i), "relock", t, int(st.expiry[i]), float(st.locked_rate[i]), 0.0) for i in np.flatnonzero(relock)]
    # new locks at the end of the day
    new = (st.status == PENDING) & (a.lock_day == t)
    st.status[new] = ACTIVE
    st.locked_rate[new] = np.round(w.rates[t] + np.array([SPREAD[p] for p in PRODUCTS])[a.product[new]] + a.rate_noise[new], 3)
    st.expiry[new] = t + a.term[new]
    st.docs_out[new] = a.n_cond[new]
    st.stage[new] = 0
    st.stage_since[new] = t
    st.last_contact[new] = t
    if rec:
        st.events["locks"] += [(int(i), "lock", t, int(st.expiry[i]), float(st.locked_rate[i]), 0.0) for i in np.flatnonzero(new)]
        st.events["stages"] += [(int(i), t, "processing") for i in np.flatnonzero(new)]


def run(w: World, st: State, days: range, policy: Policy, seed: int) -> State:
    for t in days:
        step(w, st, t, policy, seed)
    return st
