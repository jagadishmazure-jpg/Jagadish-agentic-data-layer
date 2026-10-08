"""Value: the Phase 0 value case, the value ledger and its cost per outcome, and the FOCUS cost export.

* `value_case` measures the KPI baselines from gold.sales_daily with the metrics layer, scaled to the
  28-day window, and states each target from config/value-case.yaml.
* `ledger` turns the forward simulation into value-ledger rows: one per lever and metric, with the
  baseline, the agent result, the paired difference and its 95% bootstrap interval.
* `ai_cost` estimates what the agents and the platform cost over the same window from
  config/pricing.yaml (assumed rates) and the measured prompt size, giving cost per $1,000 of value
  and per action.
* `focus_rows` writes that estimate as FOCUS 1.0 rows (the column subset used by the
  Jagadish-azure-finops repository), tagged with the use case and the ledger id, so FinOps tooling can
  put cost next to value.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass

import yaml

from adl import ROOT
from adl.core.semantic import SemanticLayer
from adl.domains.retail import simulate as SIM
from adl.domains.retail.world import DAYS_HISTORY, HORIZON

LEVERS = {"agent": "all levers", "replenishment only": "replenishment", "markdown only": "markdown"}
LEDGER_METRICS = ("net_value_usd", "lost_sales_usd", "markdown_usd", "waste_cost_usd")
FOCUS_COLUMNS = [
    "BillingAccountId", "BillingCurrency", "BillingPeriodStart", "BillingPeriodEnd", "ChargePeriodStart", "ChargePeriodEnd",
    "ChargeCategory", "ChargeDescription", "SubAccountId", "SubAccountName", "ResourceId", "ResourceName", "ResourceType", "RegionId",
    "ServiceCategory", "ServiceName", "SkuId", "PricingCategory", "PricingQuantity", "PricingUnit", "ListUnitPrice", "ListCost",
    "ContractedCost", "BilledCost", "EffectiveCost", "ConsumedQuantity", "ConsumedUnit", "CommitmentDiscountId", "Tags",
]  # fmt: skip


def load_case() -> dict:
    return yaml.safe_load((ROOT / "config/value-case.yaml").read_text())


def value_case(store, semantic: SemanticLayer) -> list[dict]:
    case = load_case()
    window = case["measurement_window_days"]
    names = [k["metric"] for k in case["kpis"]]
    base = semantic.query(store, names)[0]
    out = []
    for k in case["kpis"]:
        m = k["metric"]
        b = base[m] if m.endswith("_pct") else base[m] * window / DAYS_HISTORY
        out.append(
            {
                "metric": m,
                "baseline": b,
                "target_change_pct": k["target_change_pct"],
                "target": b * (1 + k["target_change_pct"] / 100),
                "why": k["why"],
            }
        )
    return out


@dataclass
class Ledger:
    rows: list[dict]
    comparison: SIM.Comparison

    def row(self, lever: str, metric: str) -> dict:
        return next(r for r in self.rows if r["lever"] == lever and r["metric"] == metric)


def ledger(cmp: SIM.Comparison) -> Ledger:
    rows = []
    n = 0
    for arm, lever in LEVERS.items():
        for m in LEDGER_METRICS:
            n += 1
            d, lo, hi = cmp.diff(arm, m)
            rows.append(
                {
                    "ledger_id": f"VL-RET-{n:03d}",
                    "lever": lever,
                    "metric": m,
                    "baseline_usd": round(cmp.mean("current rules", m), 2),
                    "agent_usd": round(cmp.mean(arm, m), 2),
                    "delta_usd": round(d, 2),
                    "ci_low_usd": round(lo, 2),
                    "ci_high_usd": round(hi, 2),
                    "replications": len(cmp.seeds),
                    "horizon_days": HORIZON,
                }
            )
    return Ledger(rows, cmp)


def kpi_results(cmp: SIM.Comparison) -> list[dict]:
    out = []
    for k in load_case()["kpis"]:
        m = k["metric"]
        base = cmp.mean("current rules", m)
        d, lo, hi = cmp.diff("agent", m)
        change = 100 * d / base if base else 0.0
        met = change <= k["target_change_pct"] if k["direction"] == "down" else change >= k["target_change_pct"]
        out.append(
            {
                "metric": m,
                "current_rules": base,
                "agent": base + d,
                "change_pct": change,
                "target_change_pct": k["target_change_pct"],
                "met": met,
                "ci": (lo, hi),
            }
        )
    return out


@dataclass
class CostEstimate:
    lines: list[dict]
    total_usd: float
    prompt_tokens: int
    output_tokens: int
    briefs: int

    def per_1000_value(self, value_usd: float) -> float:
        return 1000 * self.total_usd / value_usd if value_usd > 0 else float("inf")


def ai_cost(prompt_tokens: int, output_tokens: int, stores: int, days: int = HORIZON) -> CostEstimate:
    p = yaml.safe_load((ROOT / "config/pricing.yaml").read_text())
    briefs = stores * days * p["model"]["briefs_per_store_per_day"]
    model = briefs * (prompt_tokens * p["model"]["input_per_million_tokens"] + output_tokens * p["model"]["output_per_million_tokens"]) / 1e6
    c = p["compute"]
    compute = days * c["seconds_per_day"] * (c["vcpu"] * c["vcpu_second"] + c["gib"] * c["gib_second"])
    search = p["search"]["per_month"] * days / 30
    storage = p["storage"]["per_month"] * days / 30
    lines = [
        {
            "service": "Foundry Models",
            "category": "AI and Machine Learning",
            "what": p["model"]["name"],
            "quantity": briefs,
            "unit": "briefs",
            "cost_usd": model,
        },
        {
            "service": "Azure Container Apps",
            "category": "Compute",
            "what": c["name"],
            "quantity": days * c["seconds_per_day"],
            "unit": "vCPU-seconds",
            "cost_usd": compute,
        },
        {
            "service": "Azure AI Search",
            "category": "AI and Machine Learning",
            "what": p["search"]["name"],
            "quantity": days,
            "unit": "days",
            "cost_usd": search,
        },
        {"service": "Storage", "category": "Storage", "what": p["storage"]["name"], "quantity": days, "unit": "days", "cost_usd": storage},
    ]
    return CostEstimate(lines, sum(x["cost_usd"] for x in lines), prompt_tokens, output_tokens, briefs)


def focus_rows(cost: CostEstimate, ledger_id: str, days: int = HORIZON) -> list[dict]:
    """One FOCUS row per service per day over a synthetic billing period (placeholder dates, not real)."""
    tags = {"use-case": "retail-stockout-markdown", "domain": "retail", "value-ledger": ledger_id, "env": "simulation"}
    rows = []
    for d in range(days):
        start = f"2030-02-{d + 1:02d}T00:00:00Z"
        end = f"2030-02-{d + 2:02d}T00:00:00Z" if d + 1 < days else "2030-03-01T00:00:00Z"
        for line in cost.lines:
            daily = line["cost_usd"] / days
            qty = line["quantity"] / days
            name = line["service"].lower().replace(" ", "-")
            rows.append(
                {
                    "BillingAccountId": "ba-wrenfield-grocers",
                    "BillingCurrency": "USD",
                    "BillingPeriodStart": "2030-02-01T00:00:00Z",
                    "BillingPeriodEnd": "2030-03-01T00:00:00Z",
                    "ChargePeriodStart": start,
                    "ChargePeriodEnd": end,
                    "ChargeCategory": "Usage",
                    "ChargeDescription": f"estimated: {line['what']}",
                    "SubAccountId": "wf-data-products",
                    "SubAccountName": "wf-data-products",
                    "ResourceId": f"/subscriptions/wf-data-products/resourceGroups/rg-adl-retail/providers/estimate/{name}",
                    "ResourceName": f"adl-retail-{name}",
                    "ResourceType": line["service"],
                    "RegionId": "eastus2",
                    "ServiceCategory": line["category"],
                    "ServiceName": line["service"],
                    "SkuId": name,
                    "PricingCategory": "Standard",
                    "PricingQuantity": round(qty, 6),
                    "PricingUnit": line["unit"],
                    "ListUnitPrice": round(daily / qty, 8) if qty else 0.0,
                    "ListCost": round(daily, 6),
                    "ContractedCost": round(daily, 6),
                    "BilledCost": round(daily, 6),
                    "EffectiveCost": round(daily, 6),
                    "ConsumedQuantity": round(qty, 6),
                    "ConsumedUnit": line["unit"],
                    "CommitmentDiscountId": "",
                    "Tags": json.dumps(tags, sort_keys=True),
                }
            )
    return rows


def focus_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=FOCUS_COLUMNS, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()
