"""Access attack suite for the healthcare domain: requests a careless or compromised agent might make,
and the gateway check that must stop each. Also checks that no agent-exposed product contains a
synthetic patient's name, record number, encounter id, e-mail address or phone number, and that a
tampered audit log fails verification. All data is synthetic and PHI-free.
"""

from __future__ import annotations

from adl.core import guardrails
from adl.core.access import AccessDenied, DataGateway
from adl.domains.healthcare.pipeline import ENC_RE, MRN_RE
from adl.domains.retail.attacks import Attempt, tamper_detected  # the attempt record and the tamper check are domain-neutral

A, N, S = "agent:fall-prevention-assistant", "agent:ward-copilot-north", "agent:ward-copilot-south"
Q, F, EQ = "agent:patient-safety-analyst", "agent:nursing-finance", "agent:health-equity"
FP = "fall_prevention"
ATTEMPTS = [
    Attempt("unknown identity", "agent:intruder", "query", {"product": "fall_risk_worklist", "purpose": FP}, "unknown_identity"),
    Attempt("restricted patient identity", A, "query", {"product": "patients", "purpose": FP}, "not_a_data_product"),
    Attempt("synthetic group table", A, "query", {"product": "demographics", "purpose": FP}, "not_a_data_product"),
    Attempt("raw observations", A, "query", {"product": "nursing_observations", "purpose": FP}, "not_a_data_product"),
    Attempt("insurance eligibility", A, "query", {"product": "fall_risk_worklist", "purpose": "insurance_eligibility"}, "prohibited_purpose"),
    Attempt("staff performance", Q, "query", {"product": "ward_daily", "purpose": "employee_performance_management"}, "prohibited_purpose"),
    Attempt(
        "medication decisions", A, "query", {"product": "fall_risk_worklist", "purpose": "medication_or_diagnosis_decisions"}, "prohibited_purpose"
    ),
    Attempt("analyst reads patient rows", Q, "query", {"product": "fall_risk_worklist", "purpose": "quality_improvement"}, "product_not_granted"),
    Attempt(
        "assistant reads the fairness monitor", A, "query", {"product": "fairness_monitor", "purpose": "equity_monitoring"}, "product_not_granted"
    ),
    Attempt("denied column", N, "query", {"product": "fall_risk_worklist", "purpose": FP, "columns": ["age_band"]}, "column_denied"),
    Attempt(
        "filter on a masked key",
        N,
        "query",
        {"product": "fall_risk_worklist", "purpose": FP, "filters": [["encounter_key", "eq", "PT-00000000"]]},
        "column_masked",
    ),
    Attempt(
        "other site's ward", N, "query", {"product": "fall_risk_worklist", "purpose": FP, "filters": [["ward_id", "eq", "W10"]]}, "outside_row_scope"
    ),
    Attempt(
        "injection in a filter value",
        A,
        "query",
        {"product": "fall_risk_worklist", "purpose": FP, "filters": [["bed", "eq", "W01-B01' OR '1'='1"]]},
        "bad_filter_value",
    ),
    Attempt("knowledge not granted", F, "knowledge", {"question": "sitter policy", "purpose": "value_reporting"}, "product_not_granted"),
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
    """Rows in every agent-exposed product containing an e-mail address, a phone number, a record number,
    an encounter id or a synthetic patient's name."""
    names = {r["patient_name"] for r in gw.store.sql("SELECT patient_name FROM silver.patients")}
    leaks = {}
    for product in sorted(gw.products):
        if product not in gw.store.tables("gold"):
            continue
        n = 0
        for r in gw.store.sql(f"SELECT * FROM gold.{product}"):
            text = " ".join(str(v) for v in r.values() if isinstance(v, str))
            if guardrails.redact(text)[1] or MRN_RE.search(text) or ENC_RE.search(text) or any(name in text for name in names):
                n += 1
        leaks[product] = n
    return leaks


__all__ = ["ATTEMPTS", "pii_leaks", "run", "tamper_detected"]
