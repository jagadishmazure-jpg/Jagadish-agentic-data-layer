"""Access attack suite for the insurance domain: requests a careless or compromised agent might make,
and the gateway check that must stop each. Also checks that no agent-exposed product contains a
claimant's name, e-mail address or phone number, and that a tampered audit log fails verification.
"""

from __future__ import annotations

from adl.core import guardrails
from adl.core.access import AccessDenied, DataGateway
from adl.domains.retail.attacks import Attempt, tamper_detected  # the attempt record and the tamper check are domain-neutral

A, E, WE, F, C = "agent:claims-assistant", "agent:office-copilot-east", "agent:office-copilot-west", "agent:claims-finance", "agent:claims-compliance"
ATTEMPTS = [
    Attempt("unknown identity", "agent:intruder", "query", {"product": "claims_triage", "purpose": "claims_handling"}, "unknown_identity"),
    Attempt("restricted claims table", A, "query", {"product": "claims", "purpose": "claims_handling"}, "not_a_data_product"),
    Attempt("postcode proxy groups", A, "query", {"product": "postcode_groups", "purpose": "claims_handling"}, "not_a_data_product"),
    Attempt(
        "claim denial without review",
        A,
        "query",
        {"product": "leakage_signals", "purpose": "claim_denial_without_human_review"},
        "prohibited_purpose",
    ),
    Attempt(
        "underwriting by protected characteristic",
        A,
        "query",
        {"product": "claims_triage", "purpose": "underwriting_by_protected_characteristic"},
        "prohibited_purpose",
    ),
    Attempt(
        "assistant reads the fairness monitor", A, "query", {"product": "fairness_monitor", "purpose": "fairness_monitoring"}, "product_not_granted"
    ),
    Attempt("compliance reads claim scores", C, "query", {"product": "claims_triage", "purpose": "fairness_monitoring"}, "product_not_granted"),
    Attempt("purpose not granted", E, "query", {"product": "claims_daily", "purpose": "performance_reporting"}, "purpose_not_granted"),
    Attempt(
        "denied column",
        E,
        "query",
        {"product": "leakage_signals", "purpose": "claims_quality_review", "columns": ["expected_overpayment_usd"]},
        "column_denied",
    ),
    Attempt(
        "other region's office",
        E,
        "query",
        {"product": "claims_triage", "purpose": "claims_handling", "filters": [["office_id", "eq", "F05"]]},
        "outside_row_scope",
    ),
    Attempt(
        "other region by name",
        WE,
        "query",
        {"product": "leakage_signals", "purpose": "claims_quality_review", "filters": [["region", "eq", "east"]]},
        "outside_row_scope",
    ),
    Attempt(
        "injection in a filter value",
        A,
        "query",
        {"product": "claims_triage", "purpose": "claims_handling", "filters": [["claim_id", "eq", "CLM-000001' OR '1'='1"]]},
        "bad_filter_value",
    ),
    Attempt("row cap", WE, "query", {"product": "claims_daily", "purpose": "claims_handling", "limit": 5000}, "limit_above_cap"),
    Attempt("knowledge not granted", F, "knowledge", {"question": "leakage review policy", "purpose": "value_reporting"}, "product_not_granted"),
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
    """Count rows in every agent-exposed product containing an e-mail address, a phone number or a claimant's name."""
    names = {r["claimant_name"] for r in gw.store.sql("SELECT claimant_name FROM silver.claims")}
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
