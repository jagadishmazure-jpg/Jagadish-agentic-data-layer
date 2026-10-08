"""The simulated grocer: stores, products, suppliers, a planning calendar and the daily store engine.

Wrenfield Grocers is fictional: eight stores in two regions, 48 products in six categories and six
suppliers. One engine (`run`) plays a day of trading for every store-product series at once:
deliveries arrive, shoppers buy (first in, first out), near-date stock can be marked down, perishable
stock expires, and the replenishment policy places orders that arrive after a stochastic lead time.

The same engine produces the 140-day history (driven by the grocer's current rules, `LegacyPolicy`)
that lands in the bronze layer, and the forward value simulation (`adl.domains.retail.simulate`) that
compares the current rules with the agent's policy on identical shopper and supplier randomness
(common random numbers), so a difference between two runs is caused by the policy alone.

True demand, the true elasticities and lost sales are known here because the world is synthetic. They
are written only to `ground_truth` and are never read by the pipeline, the models or the agents.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

SEED = 20260518
DAYS_HISTORY = 140  # observed history: day 0 .. 139
HORIZON = 28  # forward value simulation: day 140 .. 167
DAYS_TOTAL = DAYS_HISTORY + HORIZON
PLAN_DAYS = DAYS_TOTAL + 14  # the calendar (prices, promotions, weather, events) is known 14 days further ahead
AGES = 12  # age buckets for stock; perishables expire before the last bucket
DOW = np.array([0.90, 0.85, 0.90, 1.00, 1.15, 1.30, 1.00])  # day 0 is a Monday-like day
GAMMA_SHAPE = 6.0  # shopper demand is gamma-mixed: coefficient of variation about 0.41 per store-day
MD_POOL = 0.5  # share of shoppers who would buy extra near-date stock if it is cheap enough
TRANSFER_COST = 0.20  # handling cost per unit moved between stores
ROTATION = 0.5  # share of full-price shoppers who take the oldest unit at the front; the rest reach for the freshest

CATEGORIES: dict[str, dict] = {
    # shelf: days a unit can be sold (None = does not expire in the horizon); elasticity is the true
    # own-price elasticity; temp is the demand change per degree of temperature anomaly; event is the
    # lift on local event days; margin is the regular gross margin; pack is the supplier case size
    "produce": {"shelf": 5, "elasticity": -1.6, "temp": 0.030, "event": 0.15, "margin": 0.38, "pack": 6},
    "dairy": {"shelf": 9, "elasticity": -1.2, "temp": 0.010, "event": 0.10, "margin": 0.30, "pack": 12},
    "bakery": {"shelf": 3, "elasticity": -1.8, "temp": 0.000, "event": 0.30, "margin": 0.55, "pack": 6},
    "meat": {"shelf": 6, "elasticity": -1.4, "temp": 0.025, "event": 0.35, "margin": 0.28, "pack": 4},
    "frozen": {"shelf": None, "elasticity": -1.3, "temp": 0.040, "event": 0.20, "margin": 0.33, "pack": 12},
    "pantry": {"shelf": None, "elasticity": -1.1, "temp": -0.010, "event": 0.05, "margin": 0.31, "pack": 12},
}
CATS = list(CATEGORIES)

PRODUCTS: dict[str, list[tuple[str, float]]] = {
    "produce": [("Gala apples 1kg", 3.49), ("Bananas bunch", 1.99), ("Baby spinach 200g", 2.79), ("Vine tomatoes 500g", 2.99),
                ("Strawberries 400g", 4.49), ("Avocados 2 pack", 3.29), ("Carrots 1kg", 1.49), ("Romaine hearts 3 pack", 2.59)],
    "dairy": [("Whole milk 2L", 2.89), ("Greek yoghurt 500g", 3.99), ("Cheddar 400g", 5.49), ("Butter 250g", 3.79),
              ("Oat drink 1L", 2.49), ("Free range eggs 12", 4.29), ("Cottage cheese 300g", 2.99), ("Single cream 300ml", 1.89)],
    "bakery": [("Sourdough loaf", 3.99), ("Seeded batch loaf", 2.49), ("Croissants 4 pack", 3.49), ("Bagels 5 pack", 2.79),
               ("Cinnamon buns 2 pack", 3.29), ("Baguette", 1.59), ("Wholemeal rolls 6", 2.19), ("Blueberry muffins 4", 3.69)],
    "meat": [("Chicken breast 600g", 6.49), ("Beef mince 500g", 5.29), ("Pork sausages 8", 3.99), ("Salmon fillets 2", 6.99),
             ("Lamb chops 4", 8.49), ("Chicken thighs 1kg", 5.79), ("Smoked bacon 300g", 3.49), ("Turkey slices 200g", 2.99)],
    "frozen": [("Garden peas 1kg", 1.99), ("Margherita pizza", 3.49), ("Vanilla ice cream 1L", 3.99), ("Fish fingers 10", 2.99),
               ("Oven chips 1.5kg", 2.79), ("Mixed berries 500g", 3.29), ("Vegetable dumplings", 3.79), ("Chicken nuggets 700g", 4.19)],
    "pantry": [("Basmati rice 1kg", 2.49), ("Penne pasta 500g", 1.19), ("Chopped tomatoes 400g", 0.89), ("Olive oil 500ml", 5.99),
               ("Ground coffee 227g", 4.49), ("Breakfast oats 1kg", 1.79), ("Peanut butter 340g", 2.69), ("Baked beans 415g", 0.99)],
}  # fmt: skip

STORES = [  # store id, name, region, size factor
    ("S01", "Wrenfield Harbour", "north", 1.35), ("S02", "Wrenfield Millbrook", "north", 1.00),
    ("S03", "Wrenfield Ashby Cross", "north", 0.80), ("S04", "Wrenfield Kestrel Park", "north", 0.70),
    ("S05", "Wrenfield Southgate", "south", 1.25), ("S06", "Wrenfield Riverside", "south", 1.05),
    ("S07", "Wrenfield Oakhurst", "south", 0.85), ("S08", "Wrenfield Fenwick", "south", 0.75),
]  # fmt: skip
REGIONS = ["north", "south"]

SUPPLIERS = {  # id: (name, categories, nominal lead days, delay probability, short-ship probability)
    "SUP-RF": ("Ridgeway Farms", ["produce"], 1, 0.05, 0.02),
    "SUP-CP": ("Copperleaf Produce", ["produce"], 2, 0.30, 0.10),
    "SUP-MD": ("Meadowgate Dairy", ["dairy"], 2, 0.10, 0.05),
    "SUP-HB": ("Hearthstone Bakery", ["bakery"], 1, 0.05, 0.02),
    "SUP-AR": ("Alder Ridge Meats", ["meat"], 2, 0.15, 0.05),
    "SUP-NF": ("Northpoint Foods", ["frozen", "pantry"], 4, 0.35, 0.12),
}


@dataclass
class World:
    """Static attributes, one entry per store-product series (store-major order)."""

    skus: list[dict]
    stores: list[dict]
    store_of: np.ndarray  # (S,) store index
    sku_of: np.ndarray  # (S,) product index
    region_of: np.ndarray  # (S,) region index
    cat_of: np.ndarray  # (S,) category index
    base: np.ndarray  # (S,) baseline units per day at list price
    list_price: np.ndarray
    unit_cost: np.ndarray
    shelf: np.ndarray  # (S,) sellable days; AGES for non-perishables
    perishable: np.ndarray  # (S,) bool
    elasticity: np.ndarray  # (S,) true elasticity (ground truth)
    lead: np.ndarray  # (S,) nominal lead days of the series' supplier
    p_delay: np.ndarray
    p_short: np.ndarray
    pack: np.ndarray
    supplier_of: list[str] = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.base)


@dataclass
class Calendar:
    """Plans and conditions known ahead of time (the weather is treated as a perfect forecast)."""

    promo: np.ndarray  # (T, S) bool
    promo_ratio: np.ndarray  # (T, S) price multiplier during a promotion
    reg_mult: np.ndarray  # (T, S) regular price multiplier (list price changes)
    temp: np.ndarray  # (T, regions) temperature anomaly, degrees C
    rain: np.ndarray  # (T, regions) bool
    event: np.ndarray  # (T, stores) bool, local event day
    promos: list[dict] = field(default_factory=list)
    price_changes: list[dict] = field(default_factory=list)
    price_tests: list[dict] = field(default_factory=list)


def build_world(seed: int = SEED) -> World:
    rng = np.random.default_rng(seed)
    skus = []
    for c, items in PRODUCTS.items():
        sups = [k for k, v in SUPPLIERS.items() if c in v[1]]
        for j, (name, price) in enumerate(items):
            sup = sups[j % len(sups)]
            skus.append(
                {
                    "sku": f"SKU-{c[:2].upper()}{j + 1:02d}",
                    "name": name,
                    "category": c,
                    "list_price": price,
                    "unit_cost": round(price * (1 - CATEGORIES[c]["margin"]), 2),
                    "supplier_id": sup,
                    "case_pack": CATEGORIES[c]["pack"],
                    "shelf_life_days": CATEGORIES[c]["shelf"] or 0,
                    "popularity": float(np.exp(rng.normal(0.0, 0.55))),
                }
            )
    stores = [{"store_id": s, "name": n, "region": r, "size": z} for s, n, r, z in STORES]
    s_idx, k_idx = np.meshgrid(np.arange(len(stores)), np.arange(len(skus)), indexing="ij")
    s_idx, k_idx = s_idx.ravel(), k_idx.ravel()
    cat = np.array([CATS.index(skus[k]["category"]) for k in k_idx])
    noise = np.exp(rng.normal(0.0, 0.25, size=len(s_idx)))
    base = np.array([9.0 * stores[s]["size"] * skus[k]["popularity"] for s, k in zip(s_idx, k_idx, strict=True)]) * noise
    shelf = np.array([CATEGORIES[CATS[c]]["shelf"] or AGES for c in cat])
    sup = [skus[k]["supplier_id"] for k in k_idx]
    return World(
        skus=skus,
        stores=stores,
        store_of=s_idx,
        sku_of=k_idx,
        region_of=np.array([REGIONS.index(stores[s]["region"]) for s in s_idx]),
        cat_of=cat,
        base=np.round(base, 3),
        list_price=np.array([skus[k]["list_price"] for k in k_idx]),
        unit_cost=np.array([skus[k]["unit_cost"] for k in k_idx]),
        shelf=shelf,
        perishable=shelf < AGES,
        elasticity=np.array([CATEGORIES[CATS[c]]["elasticity"] for c in cat]),
        lead=np.array([SUPPLIERS[x][2] for x in sup]),
        p_delay=np.array([SUPPLIERS[x][3] for x in sup]),
        p_short=np.array([SUPPLIERS[x][4] for x in sup]),
        pack=np.array([skus[k]["case_pack"] for k in k_idx]),
        supplier_of=sup,
    )


def build_calendar(world: World, seed: int = SEED) -> Calendar:
    rng = np.random.default_rng(seed + 1)
    t_n, n_sku = PLAN_DAYS, len(world.skus)
    promo_k = np.zeros((t_n, n_sku), bool)
    ratio_k = np.ones((t_n, n_sku))
    promos = []
    for k in range(n_sku):
        starts = sorted(rng.choice(np.arange(3, t_n - 7, 7), size=3, replace=False) + rng.integers(0, 3))
        for st in starts:
            r = float(np.round(rng.uniform(0.75, 0.85), 2))
            promo_k[st : st + 7, k] = True
            ratio_k[st : st + 7, k] = r
            promos.append({"sku": world.skus[k]["sku"], "start_day": int(st), "end_day": int(st + 6), "price_ratio": r, "display": True})
    reg_k = np.ones((t_n, n_sku))
    changes = []
    for k in range(n_sku):
        level = 1.0
        for d in sorted(rng.choice(np.arange(10, t_n - 10), size=2, replace=False)):
            level = float(np.round(level * rng.choice([0.92, 0.95, 1.04, 1.07]), 4))
            reg_k[d:, k] = level
            changes.append({"sku": world.skus[k]["sku"], "day": int(d), "multiplier": level})
    # price tests: the grocer's test-and-learn programme moves the shelf price of a product in a few
    # stores for a week. Without this variation the elasticity cannot be separated from promotion lift.
    tests = []
    reg = reg_k[:, world.sku_of].copy()
    n_store = len(world.stores)
    for k in range(n_sku):
        for st in sorted(rng.choice(np.arange(14, DAYS_HISTORY - 7, 7), size=3, replace=False) + int(rng.integers(0, 3))):
            st = int(st)
            mult = float(rng.choice([0.85, 0.90, 1.10]))
            for j in rng.choice(n_store, 4, replace=False):
                reg[st : st + 7, int(j) * n_sku + k] *= mult
                tests.append(
                    {
                        "store_id": world.stores[int(j)]["store_id"],
                        "sku": world.skus[k]["sku"],
                        "start_day": st,
                        "end_day": st + 6,
                        "multiplier": mult,
                    }
                )
    temp = np.zeros((t_n, len(REGIONS)))
    for t in range(1, t_n):
        temp[t] = 0.8 * temp[t - 1] + rng.normal(0.0, 1.5, len(REGIONS))
    rain = rng.random((t_n, len(REGIONS))) < 0.25
    event = rng.random((t_n, len(world.stores))) < 0.04
    k = world.sku_of
    return Calendar(promo_k[:, k], ratio_k[:, k], reg, np.round(temp, 2), rain, event, promos, changes, tests)


def shelf_price(world: World, cal: Calendar, t: int) -> np.ndarray:
    """Price on the shelf before any markdown: list price x regular multiplier x promotion."""
    return world.list_price * cal.reg_mult[t] * np.where(cal.promo[t], cal.promo_ratio[t], 1.0)


def expected_demand(world: World, cal: Calendar, t: int) -> np.ndarray:
    """True mean demand per series on day t (ground truth: the models never call this)."""
    ratio = cal.reg_mult[t] * np.where(cal.promo[t], cal.promo_ratio[t], 1.0)
    cat = world.cat_of
    temp_coef = np.array([CATEGORIES[c]["temp"] for c in CATS])[cat]
    event_lift = np.array([CATEGORIES[c]["event"] for c in CATS])[cat]
    lam = world.base * DOW[t % 7] * ratio**world.elasticity
    lam = lam * np.where(cal.promo[t], 1.25, 1.0)
    lam = lam * np.exp(temp_coef * cal.temp[t, world.region_of])
    lam = lam * np.where(cal.rain[t, world.region_of], 0.92, 1.0)
    lam = lam * np.where(cal.event[t, world.store_of], 1.0 + event_lift, 1.0)
    return lam


def cannibal_share(d: np.ndarray) -> np.ndarray:
    """Share of regular shoppers who switch to marked-down near-date stock at discount d."""
    return np.where(d > 0, 0.25 + d, 0.0)


@dataclass
class DayRecord:
    units_regular: np.ndarray
    units_md: np.ndarray
    revenue: np.ndarray
    md_amount: np.ndarray
    price: np.ndarray
    discount: np.ndarray
    waste: np.ndarray
    on_hand_end: np.ndarray
    lost_true: np.ndarray  # ground truth
    demand_true: np.ndarray  # ground truth
    received: np.ndarray
    order_qty: np.ndarray
    order_lead: np.ndarray
    order_received: np.ndarray
    transfer_out: np.ndarray
    transfer_in: np.ndarray
    near_expiry_next: np.ndarray  # units that will be on their last sellable day tomorrow


@dataclass
class State:
    inv: np.ndarray  # (S, AGES) units by age in days
    pipe: np.ndarray  # (S, P) units arriving at the start of day (today + 1 + k) after today's shift
    sales: np.ndarray  # (T, S) observed units sold (regular + markdown)
    soldout: np.ndarray  # (T, S) observed: no stock left at close
    on_hand: np.ndarray  # (T, S) observed closing stock
    md_flag: np.ndarray  # (T, S) observed: markdown applied
    records: list[DayRecord] = field(default_factory=list)

    @classmethod
    def initial(cls, world: World) -> State:
        inv = np.zeros((world.n, AGES))
        inv[:, 0] = np.ceil(world.base * (world.lead + 1.5))
        z = np.zeros((DAYS_TOTAL, world.n))
        return cls(inv, np.zeros((world.n, 8)), z.copy(), z.astype(bool), z.copy(), z.astype(bool))

    def copy(self) -> State:
        return State(self.inv.copy(), self.pipe.copy(), self.sales.copy(), self.soldout.copy(), self.on_hand.copy(), self.md_flag.copy(), [])

    def position(self) -> np.ndarray:
        return self.inv.sum(1) + self.pipe.sum(1)


def _fifo_remove(inv: np.ndarray, qty: np.ndarray, oldest_first: bool = True) -> np.ndarray:
    """Remove qty units per row from age buckets, oldest first (or youngest first); returns removed."""
    left = qty.astype(float).copy()
    ages = range(inv.shape[1] - 1, -1, -1) if oldest_first else range(inv.shape[1])
    for a in ages:
        take = np.minimum(inv[:, a], left)
        inv[:, a] -= take
        left -= take
    return qty - left


def step(world: World, cal: Calendar, st: State, t: int, policy, seed: int) -> DayRecord:
    """Play one trading day for every series. Randomness depends only on (seed, t), never on the policy.

    Full-price shoppers split between the oldest unit at the front (`ROTATION`) and the freshest at the
    back, which is why near-date stock goes to waste without a markdown. A markdown on near-date units
    attracts switchers (`cannibal_share`) plus extra shoppers drawn by the lower price (`MD_POOL`, true
    elasticity), and unmet full-price demand falls back to any marked-down units left."""
    rng = np.random.default_rng([seed, t])
    n = world.n
    g1, u1 = rng.gamma(GAMMA_SHAPE, 1 / GAMMA_SHAPE, n), rng.random(n)
    g2, u2 = rng.gamma(GAMMA_SHAPE, 1 / GAMMA_SHAPE, n), rng.random(n)
    u_delay, u_short, u_fill = rng.random(n), rng.random(n), rng.random(n)

    received = st.pipe[:, 0].copy()
    st.inv[:, 0] += received
    st.pipe[:, :-1] = st.pipe[:, 1:]
    st.pipe[:, -1] = 0

    price = shelf_price(world, cal, t)
    rows = np.arange(n)
    near_idx = world.shelf - 1
    near = np.where(world.perishable, st.inv[rows, np.minimum(near_idx, AGES - 1)], 0.0)
    d = np.where(near > 0, policy.markdown(world, cal, st, t, near, price), 0.0)
    d = np.clip(np.round(d, 2), 0.0, 0.5)

    lam = expected_demand(world, cal, t)
    demand = np.floor(lam * g1 + u1)
    extra = np.floor(lam * MD_POOL * ((1 - d) ** world.elasticity - 1) * g2 + u2)

    md_avail = np.where(d > 0, near, 0.0)
    cannibal = np.minimum(md_avail, np.floor(cannibal_share(d) * demand))
    md_sold = cannibal + np.minimum(md_avail - cannibal, extra)
    reg_need = demand - cannibal
    reg_inv = st.inv.copy()
    has_md = md_avail > 0
    reg_inv[rows[has_md], near_idx[has_md]] = 0.0
    front = np.floor(reg_need * ROTATION + u2 * 0.999)  # shoppers who take the oldest unit
    reg_sold = _fifo_remove(reg_inv, np.minimum(reg_inv.sum(1), front))
    reg_sold += _fifo_remove(reg_inv, np.minimum(reg_inv.sum(1), reg_need - front), oldest_first=False)
    unmet = reg_need - reg_sold
    top_up = np.minimum(md_avail - md_sold, unmet)
    md_sold += top_up
    unmet -= top_up
    st.inv = reg_inv
    st.inv[rows[has_md], near_idx[has_md]] = (md_avail - md_sold)[has_md]

    revenue = reg_sold * price + md_sold * price * (1 - d)
    md_amount = md_sold * price * d
    sold = reg_sold + md_sold
    st.sales[t], st.md_flag[t] = sold, md_sold > 0

    waste = np.where(world.perishable, st.inv[rows, np.minimum(near_idx, AGES - 1)], 0.0)
    st.inv[rows[world.perishable], near_idx[world.perishable]] = 0.0
    on_hand_end = st.inv.sum(1)
    st.soldout[t] = (on_hand_end + waste) == 0
    st.on_hand[t] = on_hand_end
    aged = np.zeros_like(st.inv)
    aged[:, 1:] = st.inv[:, :-1]
    aged[:, AGES - 1] += st.inv[:, AGES - 1]
    st.inv = aged

    t_out, t_in = policy.transfers(world, cal, st, t)
    if t_out.any():
        _fifo_remove(st.inv, t_out, oldest_first=False)
        st.pipe[:, 0] += t_in

    q = np.maximum(policy.order(world, cal, st, t), 0.0)
    lead = world.lead + (u_delay < world.p_delay) + (u_delay < world.p_delay / 4)
    fill = np.where(u_short < world.p_short, 0.6 + 0.3 * u_fill, 1.0)
    got = np.floor(q * fill)
    st.pipe[rows, lead - 1] += got
    near_next = np.where(world.perishable, st.inv[rows, np.minimum(near_idx, AGES - 1)], 0.0)
    rec = DayRecord(
        reg_sold, md_sold, revenue, md_amount, price, d, waste, on_hand_end, unmet, demand, received, q, lead, got, t_out, t_in, near_next
    )
    st.records.append(rec)
    return rec


def round_to_pack(q: np.ndarray, pack: np.ndarray, mode: np.ndarray | str = "up") -> np.ndarray:
    cases = q / pack
    if isinstance(mode, str):
        mode = np.full(q.shape, mode)
    out = np.where(mode == "up", np.ceil(cases - 1e-9), np.round(cases))
    return np.maximum(out, 0) * pack


class LegacyPolicy:
    """The grocer's current rules: trailing 7-day average sales, a fixed buffer and a flat 30% markdown.

    Weaknesses a store manager would recognise: sales are censored by stockouts, so the average sinks
    after a stockout; promotions, events and weather are ignored; and the flat markdown gives margin away
    on near-date stock that would have sold anyway."""

    name = "current rules"

    def markdown(self, world, cal, st, t, near, price):
        return np.full(world.n, 0.30)

    def transfers(self, world, cal, st, t):
        z = np.zeros(world.n)
        return z, z

    def order(self, world, cal, st, t):
        avg7 = st.sales[max(0, t - 6) : t + 1].mean(0)
        target = avg7 * (world.lead + 1) * 1.15 + 4
        need = target - st.position()
        return np.where(need > 0, round_to_pack(need, world.pack, "up"), 0.0)


def run(world: World, cal: Calendar, policy, days: range, seed: int, state: State | None = None) -> State:
    st = state if state is not None else State.initial(world)
    for t in days:
        step(world, cal, st, t, policy, seed)
    return st
