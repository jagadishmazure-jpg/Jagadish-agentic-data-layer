"""Cost per outcome and FOCUS 1.0 cost rows, shared by the domains built after retail.

`ai_cost` estimates what the agents and the platform cost over the measurement window from
config/pricing.yaml (assumed rates, not quotes) and the measured prompt size. `focus_rows` writes the
estimate as FOCUS 1.0 rows (the column subset used by the Jagadish-azure-finops repository), tagged
with the use case, the domain and the value-ledger id, so FinOps tooling can put cost next to value.
The retail domain has its own copy in adl.domains.retail.value, written first.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass

import yaml

from adl import ROOT

FOCUS_COLUMNS = [
    "BillingAccountId", "BillingCurrency", "BillingPeriodStart", "BillingPeriodEnd", "ChargePeriodStart", "ChargePeriodEnd",
    "ChargeCategory", "ChargeDescription", "SubAccountId", "SubAccountName", "ResourceId", "ResourceName", "ResourceType", "RegionId",
    "ServiceCategory", "ServiceName", "SkuId", "PricingCategory", "PricingQuantity", "PricingUnit", "ListUnitPrice", "ListCost",
    "ContractedCost", "BilledCost", "EffectiveCost", "ConsumedQuantity", "ConsumedUnit", "CommitmentDiscountId", "Tags",
]  # fmt: skip


@dataclass
class CostEstimate:
    lines: list[dict]
    total_usd: float
    prompt_tokens: int
    output_tokens: int
    briefs: int

    def per_1000_value(self, value_usd: float) -> float:
        return 1000 * self.total_usd / value_usd if value_usd > 0 else float("inf")


def ai_cost(prompt_tokens: int, output_tokens: int, briefs_per_day: int, days: int) -> CostEstimate:
    p = yaml.safe_load((ROOT / "config/pricing.yaml").read_text())
    briefs = briefs_per_day * days
    model = briefs * (prompt_tokens * p["model"]["input_per_million_tokens"] + output_tokens * p["model"]["output_per_million_tokens"]) / 1e6
    c = p["compute"]
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
            "cost_usd": days * c["seconds_per_day"] * (c["vcpu"] * c["vcpu_second"] + c["gib"] * c["gib_second"]),
        },
        {
            "service": "Azure AI Search",
            "category": "AI and Machine Learning",
            "what": p["search"]["name"],
            "quantity": days,
            "unit": "days",
            "cost_usd": p["search"]["per_month"] * days / 30,
        },
        {
            "service": "Storage",
            "category": "Storage",
            "what": p["storage"]["name"],
            "quantity": days,
            "unit": "days",
            "cost_usd": p["storage"]["per_month"] * days / 30,
        },
    ]
    return CostEstimate(lines, sum(x["cost_usd"] for x in lines), prompt_tokens, output_tokens, briefs)


def focus_rows(cost: CostEstimate, days: int, tags: dict[str, str], account: str, sub_account: str) -> list[dict]:
    """One FOCUS row per service per day over a synthetic billing period (placeholder dates, not real)."""
    rows = []
    domain = tags["domain"]
    for d in range(days):
        start = f"2030-02-{d + 1:02d}T00:00:00Z"
        end = f"2030-02-{d + 2:02d}T00:00:00Z" if d + 1 < days else "2030-03-01T00:00:00Z"
        for line in cost.lines:
            daily = line["cost_usd"] / days
            qty = line["quantity"] / days
            name = line["service"].lower().replace(" ", "-")
            rows.append(
                {
                    "BillingAccountId": account,
                    "BillingCurrency": "USD",
                    "BillingPeriodStart": "2030-02-01T00:00:00Z",
                    "BillingPeriodEnd": "2030-03-01T00:00:00Z",
                    "ChargePeriodStart": start,
                    "ChargePeriodEnd": end,
                    "ChargeCategory": "Usage",
                    "ChargeDescription": f"estimated: {line['what']}",
                    "SubAccountId": sub_account,
                    "SubAccountName": sub_account,
                    "ResourceId": f"/subscriptions/{sub_account}/resourceGroups/rg-adl-{domain}/providers/estimate/{name}",
                    "ResourceName": f"adl-{domain}-{name}",
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
