"""Insight products: models turn gold data into new gold data products agents can use.

* `demand_forecast`: the next 14 days per store-product from the forecaster trained through day 139,
  with an 80% upper bound from the category error.
* `stockout_risk`: the probability of selling out within three days given stock and open orders.
* `markdown_candidates`: near-date stock with the recommended and the current-rule discount.

Each is written through `pipeline.write_gold`, so it is contract-checked and emits lineage like any
other product.
"""

from __future__ import annotations

import numpy as np
import pyarrow as pa

from adl.domains.retail import markdown as MD
from adl.domains.retail import pipeline as P
from adl.domains.retail import stockout as SO
from adl.domains.retail.features import Frame
from adl.domains.retail.forecast import Forecaster, levels
from adl.domains.retail.world import CATS, REGIONS

FORECAST_DAYS = 14
Z80 = 1.2816


def build(b: P.Build, f: Frame, model: Forecaster, elasticity: dict[str, float], take30: dict[str, float], policy: dict) -> dict[str, np.ndarray]:
    o = P.AS_OF
    lev = levels(f.units, f.soldout)[o]
    fc = model.predict(f, o, FORECAST_DAYS, lev)
    cv = model.cv[f.cat]
    rows = []
    for i, (store, sku) in enumerate(f.keys):
        for h in range(FORECAST_DAYS):
            rows.append(
                {
                    "store_id": store,
                    "sku": sku,
                    "day": o + 1 + h,
                    "horizon": h + 1,
                    "forecast_units": round(float(fc[i, h]), 3),
                    "p90_units": round(float(fc[i, h] * (1 + Z80 * cv[i])), 3),
                }
            )
    P.write_gold(b, "demand_forecast", pa.Table.from_pylist(rows), ["model.ridge_forecaster"])

    po = b.store.sql(
        "SELECT store_id, sku, order_day, expected_day, received_day, qty_ordered FROM silver.purchase_orders WHERE received_day IS NULL"
    )
    due = SO.due_by_day(po, f.keys, o)
    avail = f.on_hand[o] + due.sum(1)
    p, mu = SO.window_probability(fc[:, : SO.HORIZON], cv, f.on_hand[o], due)
    bands = SO.band(p)
    P.write_gold(
        b,
        "stockout_risk",
        pa.Table.from_pylist(
            [
                {
                    "store_id": s,
                    "region": REGIONS[f.region[i]],
                    "sku": k,
                    "horizon_days": SO.HORIZON,
                    "available_units": float(avail[i]),
                    "expected_demand": round(float(mu[i]), 3),
                    "probability": round(float(p[i]), 4),
                    "risk_band": str(bands[i]),
                }
                for i, (s, k) in enumerate(f.keys)
            ]
        ),
    )

    near = f.near[o]
    e = np.array([elasticity[CATS[c]] for c in f.cat])
    t30 = np.array([take30[CATS[c]] for c in f.cat])
    d = MD.choose(near, fc[:, 0], t30, e, policy["markdown"]["max_discount"])
    cleared = np.minimum(near, MD.take(d, t30, e) * fc[:, 0])
    rows = [
        {
            "store_id": s,
            "sku": k,
            "category": CATS[f.cat[i]],
            "near_expiry_units": int(near[i]),
            "forecast_units": round(float(fc[i, 0]), 3),
            "elasticity": round(float(e[i]), 3),
            "recommended_discount_pct": round(float(100 * d[i])),
            "current_rule_discount_pct": round(100 * MD.RULE_DISCOUNT),
            "expected_units_cleared": round(float(cleared[i]), 2),
        }
        for i, (s, k) in enumerate(f.keys)
        if near[i] > 0
    ]
    P.write_gold(b, "markdown_candidates", pa.Table.from_pylist(rows), ["model.markdown_policy"])
    return {"forecast": fc, "probability": p, "available": avail, "discount": d}
