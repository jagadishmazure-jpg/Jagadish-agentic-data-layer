"""Access attack suite for the mortgage domain: requests a careless or compromised agent might make,
and the gateway check that must stop each. Also checks that no agent-exposed product contains a
borrower's name, e-mail address or phone number, and that a tampered audit log fails verification.
"""

from __future__ import annotations

from adl.core import guardrails
from adl.core.access import AccessDenied, DataGateway
from adl.domains.retail.attacks import Attempt, tamper_detected  # the attempt record and the tamper check are domain-neutral

A, N, S, F = "agent:mortgage-assistant", "agent:branch-copilot-north", "agent:branch-copilot-south", "agent:mortgage-finance"
ATTEMPTS = [
    Attempt("unknown identity", "agent:intruder", "query", {"product": "lock_position", "purpose": "pipeline_management"}, "unknown_identity"),
    Attempt("restricted applications table", A, "query", {"product": "applications", "purpose": "pipeline_management"}, "not_a_data_product"),
    Attempt("raw silver contacts", A, "query", {"product": "contacts", "purpose": "pipeline_management"}, "not_a_data_product"),
    Attempt("credit decisioning", A, "query", {"product": "fallout_risk", "purpose": "credit_decisioning"}, "prohibited_purpose"),
    Attempt(
        "pricing by protected characteristic",
        A,
        "query",
        {"product": "lock_position", "purpose": "pricing_by_protected_characteristic"},
        "prohibited_purpose",
    ),
    Attempt("product not granted", N, "query", {"product": "value_ledger", "purpose": "branch_operations"}, "product_not_granted"),
    Attempt("purpose not granted", N, "query", {"product": "lock_position", "purpose": "pipeline_management"}, "purpose_not_granted"),
    Attempt(
        "denied column", N, "query", {"product": "lock_position", "purpose": "branch_operations", "columns": ["loan_amount_usd"]}, "column_denied"
    ),
    Attempt(
        "other region's branch",
        N,
        "query",
        {"product": "fallout_risk", "purpose": "branch_operations", "filters": [["branch_id", "eq", "B05"]]},
        "outside_row_scope",
    ),
    Attempt(
        "other region by name",
        S,
        "query",
        {"product": "lock_position", "purpose": "branch_operations", "filters": [["region", "eq", "north"]]},
        "outside_row_scope",
    ),
    Attempt(
        "injection in a filter value",
        A,
        "query",
        {"product": "lock_position", "purpose": "pipeline_management", "filters": [["application_id", "eq", "APP-00001' OR '1'='1"]]},
        "bad_filter_value",
    ),
    Attempt("row cap", S, "query", {"product": "pipeline_daily", "purpose": "branch_operations", "limit": 5000}, "limit_above_cap"),
    Attempt("knowledge not granted", F, "knowledge", {"question": "lock extension policy", "purpose": "value_reporting"}, "product_not_granted"),
    Attempt(
        "metric with a smuggled value",
        S,
        "metric",
        {"metrics": ["pull_through_pct"], "purpose": "branch_operations", "filters": [["region", "eq", "north'--"]]},
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
    """Count rows in every agent-exposed product containing an e-mail address, a phone number or an applicant's name."""
    names = {r["applicant_name"] for r in gw.store.sql("SELECT applicant_name FROM silver.applications")}
    leaks = {}
    for product in sorted(gw.products):
        if product not in gw.store.tables("gold"):
            continue
        n = 0
        for r in gw.store.sql(f"SELECT * FROM gold.{product}"):
            text = " ".join(str(v) for v in r.values() if isinstance(v, str))
            if guardrails.redact(text)[1] or any(name in text for name in names):
                n += 1
        leaks[product] = n
    return leaks


__all__ = ["ATTEMPTS", "pii_leaks", "run", "tamper_detected"]
