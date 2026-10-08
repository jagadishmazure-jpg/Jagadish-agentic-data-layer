"""Access attack suite: requests a careless or compromised agent might make, and the code that must stop each.

Run by `adl access` and the tests. Also checks that no gold data product contains a loyalty member's
name, email or phone, and that a tampered audit log fails verification.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass

from adl.core import guardrails
from adl.core.access import AccessDenied, DataGateway


@dataclass(frozen=True)
class Attempt:
    name: str
    identity: str
    call: str  # query | metric | knowledge | describe
    args: dict
    expect: str


ATTEMPTS = [
    Attempt("unknown identity", "agent:intruder", "query", {"product": "sales_daily", "purpose": "replenishment"}, "unknown_identity"),
    Attempt("raw silver table", "agent:replenishment", "query", {"product": "pos_sales", "purpose": "replenishment"}, "not_a_data_product"),
    Attempt("restricted PII table", "agent:insights", "query", {"product": "loyalty_pii", "purpose": "performance_reporting"}, "not_a_data_product"),
    Attempt(
        "SQL in the product name",
        "agent:replenishment",
        "query",
        {"product": "sales_daily; DROP TABLE x", "purpose": "replenishment"},
        "not_a_data_product",
    ),
    Attempt("product not granted", "agent:markdown", "query", {"product": "value_ledger", "purpose": "markdown_optimisation"}, "product_not_granted"),
    Attempt(
        "prohibited purpose",
        "agent:insights",
        "query",
        {"product": "customer_segments", "purpose": "individual_customer_profiling"},
        "prohibited_purpose",
    ),
    Attempt(
        "purpose not granted", "agent:store-copilot-north", "query", {"product": "sales_daily", "purpose": "replenishment"}, "purpose_not_granted"
    ),
    Attempt(
        "denied column",
        "agent:store-copilot-north",
        "query",
        {"product": "sales_daily", "purpose": "store_operations", "columns": ["cogs_usd"]},
        "column_denied",
    ),
    Attempt(
        "other region's store",
        "agent:store-copilot-north",
        "query",
        {"product": "inventory_position", "purpose": "store_operations", "filters": [["store_id", "eq", "S06"]]},
        "outside_row_scope",
    ),
    Attempt(
        "injection in a filter value",
        "agent:replenishment",
        "query",
        {"product": "sales_daily", "purpose": "replenishment", "filters": [["sku", "eq", "SKU-PR01' OR '1'='1"]]},
        "bad_filter_value",
    ),
    Attempt(
        "row cap", "agent:store-copilot-south", "query", {"product": "sales_daily", "purpose": "store_operations", "limit": 5000}, "limit_above_cap"
    ),
    Attempt(
        "unknown filter operator",
        "agent:replenishment",
        "query",
        {"product": "sales_daily", "purpose": "replenishment", "filters": [["units", "like", 1]]},
        "bad_operator",
    ),
    Attempt(
        "knowledge for a prohibited purpose",
        "agent:insights",
        "knowledge",
        {"question": "loyalty members near Harbour", "purpose": "performance_reporting"},
        "product_not_granted",
    ),
    Attempt(
        "metric on another region",
        "agent:store-copilot-south",
        "metric",
        {"metrics": ["revenue_usd"], "purpose": "store_operations", "filters": [["region", "eq", "north'--"]]},
        "bad_filter_value",
    ),
]


def run(gw: DataGateway) -> list[dict]:
    out = []
    for a in ATTEMPTS:
        code = "allowed"
        try:
            if a.call == "query":
                gw.query(a.identity, **a.args)
            elif a.call == "metric":
                gw.metric(a.identity, **a.args)
            elif a.call == "knowledge":
                gw.search_knowledge(a.identity, **a.args)
        except AccessDenied as e:
            code = e.code
        out.append({"attempt": a.name, "identity": a.identity, "expected": a.expect, "got": code, "stopped": code == a.expect})
    return out


def pii_leaks(gw: DataGateway) -> dict[str, int]:
    """Count rows in every agent-exposed product containing an email, phone number or a loyalty member's name."""
    names = {r["full_name"] for r in gw.store.sql("SELECT full_name FROM silver.loyalty_pii")}
    leaks = {}
    for product in sorted(gw.products):
        n = 0
        if product not in gw.store.tables("gold"):
            continue
        for r in gw.store.sql(f"SELECT * FROM gold.{product}"):
            text = " ".join(str(v) for v in r.values() if isinstance(v, str))
            if guardrails.redact(text)[1] or any(name in text for name in names):
                n += 1
        leaks[product] = n
    return leaks


def tamper_detected(gw: DataGateway) -> tuple[bool, str]:
    log = copy.deepcopy(gw.audit)
    if not log.records:
        return False, "empty log"
    log.records[len(log.records) // 2]["data"]["rows"] = 999
    ok, msg = log.verify()
    return (not ok), msg
