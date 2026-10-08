"""Data contracts: every file validates, and the validator rejects the mistakes it is there to catch."""

import copy
from pathlib import Path

import pytest
import yaml

from adl import ROOT
from adl.core.contracts import Contract, ContractError, load_all, row_predicate
from adl.core.domain import REGISTRY, get

FILES = sorted((ROOT / "domains").glob("*/contracts/*.yaml"))
GOOD = yaml.safe_load((ROOT / "domains/retail/contracts/gold.stockout_risk.yaml").read_text())


@pytest.mark.parametrize("path", FILES, ids=lambda p: f"{p.parent.parent.name}/{p.stem}")
def test_contract_file_validates(path):
    c = Contract.model_validate(yaml.safe_load(path.read_text()))
    assert c.domain == path.parent.parent.name
    assert path.name == f"{c.layer}.{c.table}.yaml"
    assert c.owner.contact.endswith(".example")


def test_retail_has_fifteen_silver_and_ten_gold():
    cs = load_all(ROOT / "domains/retail/contracts")
    assert sum(c.layer == "silver" for c in cs.values()) == 15
    assert sum(c.layer == "gold" for c in cs.values()) == 10
    assert all(c.status == "built" for c in cs.values())


def test_mortgage_has_ten_silver_and_five_gold():
    cs = load_all(ROOT / "domains/mortgage/contracts")
    assert sum(c.layer == "silver" for c in cs.values()) == 10
    assert sum(c.layer == "gold" for c in cs.values()) == 5
    assert all(c.status == "built" for c in cs.values())
    assert cs["mortgage.silver.applications"].classification == "restricted"
    for c in cs.values():
        if c.layer == "gold" and c.table != "value_ledger":
            assert "credit_decisioning" in c.acceptable_use.prohibited_purposes


@pytest.mark.parametrize("name", ["insurance", "healthcare"])
def test_planned_domains_have_only_planned_contracts(name):
    d = get(name)
    assert d.status == "planned"
    assert all(c.status == "planned" for c in d.contracts().values())
    assert (d.folder / "README.md").exists()


def test_registry_built_domains():
    assert [d.name for d in REGISTRY.values() if d.status == "built"] == ["retail", "mortgage"]
    with pytest.raises(KeyError):
        get("banking")


def _bad(mutate):
    doc = copy.deepcopy(GOOD)
    mutate(doc)
    with pytest.raises(ValueError):
        Contract.model_validate(doc)


def test_pii_cannot_be_classified_internal():
    _bad(lambda d: d["schema"].append({"name": "email", "type": "string", "pii": True}) or d.update(agent_exposed=False))


def test_gold_needs_acceptable_use():
    _bad(lambda d: d.pop("acceptable_use"))


def test_gold_needs_consumers():
    _bad(lambda d: d.update(consumers=[]))


def test_purpose_cannot_be_allowed_and_prohibited():
    _bad(lambda d: d["acceptable_use"]["prohibited_purposes"].append("replenishment"))


def test_agent_exposed_product_cannot_hold_pii():
    _bad(lambda d: d.update(classification="confidential") or d["schema"].append({"name": "phone", "type": "string", "pii": True}))


def test_restricted_product_cannot_be_exposed():
    _bad(lambda d: d.update(classification="restricted"))


def test_check_must_reference_a_real_column():
    _bad(lambda d: d["quality"].append({"check": "not_null", "column": "nope"}))


def test_primary_key_must_exist():
    _bad(lambda d: d.update(primary_key=["nope"]))


def test_id_must_match_layer_and_table():
    _bad(lambda d: d.update(id="retail.silver.stockout_risk"))


def test_duplicate_columns_rejected():
    _bad(lambda d: d["schema"].append({"name": "sku", "type": "string"}))


def test_unknown_fields_rejected():
    _bad(lambda d: d.update(owner_email="x@y.example"))


def test_range_check_needs_a_bound():
    _bad(lambda d: d["quality"].append({"check": "range", "column": "probability"}))


def test_referential_check_to_unknown_contract_fails(tmp_path: Path):
    doc = copy.deepcopy(GOOD)
    doc["quality"].append({"check": "referential", "column": "sku", "ref": "retail.silver.nope.sku"})
    doc["inputs"] = []
    (tmp_path / "gold.stockout_risk.yaml").write_text(yaml.safe_dump(doc))
    with pytest.raises(ContractError):
        load_all(tmp_path)


def test_input_without_contract_fails(tmp_path: Path):
    doc = copy.deepcopy(GOOD)
    doc["quality"] = [q for q in doc["quality"] if q["check"] != "referential"]
    (tmp_path / "gold.stockout_risk.yaml").write_text(yaml.safe_dump(doc))
    with pytest.raises(ContractError, match="has no contract"):
        load_all(tmp_path)


def test_row_predicate_turns_checks_into_sql():
    cs = load_all(ROOT / "domains/retail/contracts")
    pred = row_predicate(cs["retail.gold.stockout_risk"], cs, lambda layer, t: f"{layer}.{t}")
    assert '"probability" >= 0' in pred and '"probability" <= 1' in pred
    assert "IN ('low', 'medium', 'high')" in pred
    assert "silver.stores" in pred


def test_loyalty_pii_is_restricted_and_never_exposed():
    c = load_all(ROOT / "domains/retail/contracts")["retail.silver.loyalty_pii"]
    assert c.classification == "restricted" and not c.agent_exposed and set(c.pii_columns) >= {"email", "phone", "full_name"}
