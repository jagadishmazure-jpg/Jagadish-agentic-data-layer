"""The agents' decision policy: forecast-driven replenishment, minimum-clearing markdowns, transfers.

The same class drives the forward value simulation and the proposals the agents make today, so the
value that is measured is the value of the logic that is proposed. Every number it uses comes from
the lake (the feature frame, gold.supplier_performance, the price-test elasticities and the markdown
response learned from history); none comes from the simulator's ground truth.

* Replenishment: order up to the forecast over the supplier's p90 lead time plus one review day, plus
  safety stock (z by perishability, from config/policy.yaml). For perishables the order is capped at
  what the forecast says can sell during the new lot's shelf life, and rounded to the nearest case
  instead of always up.
* Markdown: `adl.domains.retail.markdown.choose`.
* Transfers: a shortfall before tonight's order can arrive (up to three days) in one store is covered
  from a same-region store holding more than 1.2 times its own cover, for non-perishables and
  perishables with at least five days of shelf life. Transfers arrive the next morning.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import yaml

from adl import ROOT
from adl.domains.retail import markdown as MD
from adl.domains.retail.features import Frame
from adl.domains.retail.forecast import LEVEL_DAYS, MAX_H, Forecaster
from adl.domains.retail.world import CATS, World, round_to_pack


def load_policy(path: Path | None = None) -> dict:
    return yaml.safe_load((path or ROOT / "config/policy.yaml").read_text())


def order_quantity(
    fc: np.ndarray,
    lead: np.ndarray,
    cv: np.ndarray,
    z: np.ndarray,
    position: np.ndarray,
    perishable: np.ndarray,
    shelf: np.ndarray,
    pack: np.ndarray,
    cfg: dict,
) -> np.ndarray:
    """Units to order tonight per series. Shared by the simulation and today's proposals."""
    horizon = fc.shape[1]
    cover = np.minimum(lead + cfg["replenishment"]["review_days"], horizon)
    h = np.arange(horizon)[None, :]
    in_cover = h < cover[:, None]
    mu = (fc * in_cover).sum(1)
    sd = np.sqrt((((cv[:, None] * fc) ** 2) * in_cover).sum(1) + mu)
    q = mu + z * sd - position
    if cfg["replenishment"]["cap_perishable_to_shelf_life"]:
        life = (h >= lead[:, None]) & (h < (lead + shelf - 1)[:, None])
        q = np.where(perishable, np.minimum(q, (fc * life).sum(1)), q)
    mode = np.where(perishable, "round", "up")
    return np.where(q > 0, round_to_pack(q, pack, mode), 0.0)


def plan_transfers(groups: list[np.ndarray], short: np.ndarray, excess: np.ndarray, min_units: int) -> tuple[np.ndarray, np.ndarray]:
    """Greedy same-group matching: biggest shortfall first, from the biggest surplus first."""
    short, excess = short.copy(), excess.copy()
    out, inn = np.zeros(len(short)), np.zeros(len(short))
    for idx in groups:
        if len(idx) < 2:
            continue
        recv = [i for i in idx[np.argsort(-short[idx], kind="stable")] if short[i] >= min_units]
        give = [i for i in idx[np.argsort(-excess[idx], kind="stable")] if excess[i] >= min_units]
        for i in recv:
            for j in give:
                if i == j or excess[j] < min_units:
                    continue
                qty = np.floor(min(short[i], excess[j]))
                if qty < min_units:
                    continue
                out[j] += qty
                inn[i] += qty
                excess[j] -= qty
                short[i] -= qty
                if short[i] < min_units:
                    break
    return out, inn


class AgentPolicy:
    def __init__(
        self,
        world: World,
        frame: Frame,
        model: Forecaster,
        elasticity: dict[str, float],
        take30: dict[str, float],
        cfg: dict | None = None,
        replenish: bool = True,
        mark_down: bool = True,
        transfer: bool = True,
    ) -> None:
        self.cfg = cfg or load_policy()
        self.w, self.f, self.model = world, frame, model
        key_of = {k: i for i, k in enumerate(frame.keys)}
        stores = [s["store_id"] for s in world.stores]
        skus = [k["sku"] for k in world.skus]
        self.fidx = np.array([key_of[(stores[s], skus[k])] for s, k in zip(world.store_of, world.sku_of, strict=True)])
        self.lead = frame.lead_p90[self.fidx].astype(int)
        self.cv = model.cv[frame.cat[self.fidx]]
        self.e = np.array([elasticity[CATS[c]] for c in world.cat_of])
        self.t30 = np.array([take30[CATS[c]] for c in world.cat_of])
        z = self.cfg["replenishment"]["service_z"]
        self.z = np.where(world.perishable, z["perishable"], z["non_perishable"])
        self.replenish, self.mark_down, self.transfer = replenish, mark_down, transfer
        self.transfer = transfer and self.cfg["transfers"]["enabled"]
        self._fc: dict[int, np.ndarray] = {}
        self._groups: list[np.ndarray] | None = None

    # ------------------------------------------------------------------ forecasts in world order
    def level(self, st, t: int) -> np.ndarray:
        lo = max(0, t - LEVEL_DAYS + 1)
        u, so = st.sales[lo : t + 1], st.soldout[lo : t + 1]
        ok = ~so
        return np.where(ok.sum(0) > 0, (u * ok).sum(0) / np.maximum(ok.sum(0), 1), 0.0)

    def forecast(self, st, t: int) -> np.ndarray:
        """(S, MAX_H) forecasts for days t+1 .. t+MAX_H made with information up to the end of day t."""
        if t not in self._fc:
            lev_f = np.empty(self.w.n)
            lev_f[self.fidx] = self.level(st, t)
            self._fc = {t: self.model.predict(self.f, t, MAX_H, lev_f)[self.fidx]}
        return self._fc[t]

    # ------------------------------------------------------------------ the three levers
    def markdown(self, world, cal, st, t, near, price):
        if not self.mark_down:
            return np.full(world.n, MD.RULE_DISCOUNT)
        fc = self.forecast(st, t - 1)[:, 0]
        return MD.choose(near, fc, self.t30, self.e, self.cfg["markdown"]["max_discount"])

    def order(self, world, cal, st, t):
        if not self.replenish:
            from adl.domains.retail.world import LegacyPolicy

            return LegacyPolicy().order(world, cal, st, t)
        return order_quantity(self.forecast(st, t), self.lead, self.cv, self.z, st.position(), world.perishable, world.shelf, world.pack, self.cfg)

    def transfers(self, world, cal, st, t):
        z = np.zeros(world.n)
        if not self.transfer:
            return z, z.copy()
        fc = self.forecast(st, t)
        on_hand = st.inv.sum(1)
        until = np.minimum(self.lead, 3)  # days before an order placed tonight can arrive
        h = np.arange(MAX_H)[None, :]
        need_now = (fc * (h < until[:, None])).sum(1)
        arriving = (st.pipe[:, :MAX_H] * (h[:, : st.pipe.shape[1]] < until[:, None])).sum(1)
        short = np.maximum(need_now - (on_hand + arriving), 0.0)
        cover = np.minimum(self.lead + 2, MAX_H)
        need_cover = (fc * (np.arange(MAX_H)[None, :] < cover[:, None])).sum(1)
        excess = np.maximum(on_hand - 1.2 * need_cover, 0.0)
        eligible = (~world.perishable) | (world.shelf >= 5)
        if self._groups is None:
            self._groups = [
                np.nonzero((world.sku_of == k) & (world.region_of == r) & eligible)[0]
                for k in range(len(world.skus))
                for r in range(int(world.region_of.max()) + 1)
            ]
        return plan_transfers(self._groups, short, excess, self.cfg["transfers"]["min_units"])
