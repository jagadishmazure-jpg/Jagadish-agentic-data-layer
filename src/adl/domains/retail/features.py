"""Feature frame: dense store-product x day arrays assembled from the lake for the models.

Training jobs run under a job identity that reads gold products and the conformed silver calendars
(prices, weather, events); agents never do this, they call the gateway. Series are ordered by
(store_id, sku). Missing store-product-days (rows the contracts quarantined) are NaN and are skipped,
not filled.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from adl.domains.retail.world import CATS, DAYS_HISTORY, PLAN_DAYS, REGIONS


@dataclass
class Frame:
    keys: list[tuple[str, str]]
    region: np.ndarray  # (S,) region index
    cat: np.ndarray  # (S,) category index
    list_price: np.ndarray
    unit_cost: np.ndarray
    pack: np.ndarray
    shelf: np.ndarray  # 0 = does not expire
    lead_p90: np.ndarray
    lead_mean: np.ndarray
    units: np.ndarray  # (T_hist, S) float, NaN where missing
    soldout: np.ndarray  # (T_hist, S) bool
    md_units: np.ndarray  # (T_hist, S)
    near: np.ndarray  # (T_hist, S) units on their last sellable day the next day
    on_hand: np.ndarray  # (T_hist, S)
    price_ratio: np.ndarray  # (T_all, S) shelf price / list price (promotions and list changes)
    promo: np.ndarray  # (T_all, S) bool
    temp: np.ndarray  # (T_all, S)
    rain: np.ndarray  # (T_all, S) bool
    event: np.ndarray  # (T_all, S) bool

    @property
    def n(self) -> int:
        return len(self.keys)


def _grid(rows, idx, value, shape, fill=np.nan, days=DAYS_HISTORY):
    out = np.full(shape, fill, dtype=float)
    for r in rows:
        k = (r["store_id"], r["sku"])
        if k in idx and r["day"] < days:
            out[r["day"], idx[k]] = r[value]
    return out


def build_frame(store) -> Frame:
    keys_rows = store.sql(
        "SELECT i.store_id, i.sku, i.region, i.category, p.list_price, p.unit_cost, p.case_pack, p.shelf_life_days, sp.lead_time_p90, sp.lead_time_mean "
        "FROM gold.inventory_position i JOIN silver.products p USING (sku) JOIN gold.supplier_performance sp ON sp.supplier_id = p.supplier_id "
        "ORDER BY i.store_id, i.sku"
    )
    keys = [(r["store_id"], r["sku"]) for r in keys_rows]
    idx = {k: i for i, k in enumerate(keys)}
    n = len(keys)
    hist = (DAYS_HISTORY, n)
    sales = store.sql("SELECT store_id, sku, day, units, CAST(sold_out AS INT) AS so, markdown_units, shelf_price FROM gold.sales_daily")
    inv = store.sql("SELECT store_id, sku, day, near_expiry_units, on_hand_end FROM silver.inventory")
    units = _grid(sales, idx, "units", hist)
    soldout = _grid(sales, idx, "so", hist, 0.0).astype(bool)
    md = _grid(sales, idx, "markdown_units", hist, 0.0)
    near = _grid(inv, idx, "near_expiry_units", hist, 0.0)
    on_hand = _grid(inv, idx, "on_hand_end", hist, 0.0)

    allt = (PLAN_DAYS, n)
    sku_cols = {}
    for i, (_, sku) in enumerate(keys):
        sku_cols.setdefault(sku, []).append(i)
    lp = {r["sku"]: r["list_price"] for r in keys_rows}
    ratio = np.ones(allt)
    for r in store.sql("SELECT sku, day, regular_price FROM silver.prices"):
        ratio[r["day"], sku_cols[r["sku"]]] = r["regular_price"] / lp[r["sku"]]
    promo = np.zeros(allt, bool)
    for r in store.sql("SELECT sku, start_day, end_day, price_ratio FROM gold.promo_plan"):
        for t in range(r["start_day"], min(r["end_day"], PLAN_DAYS - 1) + 1):
            promo[t, sku_cols[r["sku"]]] = True
            ratio[t, sku_cols[r["sku"]]] *= r["price_ratio"]
    # history: the shelf price the POS actually recorded (includes store price tests); future: the plan
    shelf = _grid(sales, idx, "shelf_price", hist)
    lp_s = np.array([r["list_price"] for r in keys_rows])
    observed = shelf / lp_s
    ratio[:DAYS_HISTORY] = np.where(np.isnan(observed), ratio[:DAYS_HISTORY], observed)
    region = np.array([REGIONS.index(r["region"]) for r in keys_rows])
    temp_r = np.zeros((PLAN_DAYS, len(REGIONS)))
    rain_r = np.zeros((PLAN_DAYS, len(REGIONS)), bool)
    for r in store.sql("SELECT region, day, temp_anomaly_c, rain FROM silver.weather"):
        temp_r[r["day"], REGIONS.index(r["region"])] = r["temp_anomaly_c"]
        rain_r[r["day"], REGIONS.index(r["region"])] = r["rain"]
    stores = sorted({k[0] for k in keys})
    ev_s = np.zeros((PLAN_DAYS, len(stores)), bool)
    for r in store.sql("SELECT store_id, day FROM silver.store_events"):
        ev_s[r["day"], stores.index(r["store_id"])] = True
    store_idx = np.array([stores.index(k[0]) for k in keys])
    return Frame(
        keys=keys,
        region=region,
        cat=np.array([CATS.index(r["category"]) for r in keys_rows]),
        list_price=np.array([r["list_price"] for r in keys_rows]),
        unit_cost=np.array([r["unit_cost"] for r in keys_rows]),
        pack=np.array([r["case_pack"] for r in keys_rows]),
        shelf=np.array([r["shelf_life_days"] for r in keys_rows]),
        lead_p90=np.array([r["lead_time_p90"] for r in keys_rows]),
        lead_mean=np.array([r["lead_time_mean"] for r in keys_rows]),
        units=units,
        soldout=soldout,
        md_units=md,
        near=near,
        on_hand=on_hand,
        price_ratio=ratio,
        promo=promo,
        temp=temp_r[:, region],
        rain=rain_r[:, region],
        event=ev_s[:, store_idx],
    )
