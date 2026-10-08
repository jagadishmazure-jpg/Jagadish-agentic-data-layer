"""The doc renderer: output blocks and code excerpts."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("render_docs", ROOT / "scripts/render_docs.py")
rd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rd)


def test_output_block_is_filled_from_the_cli():
    out = rd.render("<!-- output: domains -->\nstale\n<!-- /output -->\n")
    assert "Wrenfield Grocers" in out and "Quillmere Home Loans" in out and "stale" not in out


def test_python_excerpt_is_the_current_function():
    lang, body = rd.excerpt("src/adl/domains/retail/agents.py::validate_brief")
    assert lang == "python" and body.startswith("def validate_brief")


def test_decorated_class_excerpt_starts_at_the_decorator():
    _, body = rd.excerpt("src/adl/core/access.py::Identity")
    assert body.startswith("@dataclass")


def test_constant_excerpt():
    _, body = rd.excerpt("src/adl/domains/retail/agents.py::LEDGER_LEVER")
    assert body.startswith("LEDGER_LEVER")


def test_hcl_block_excerpt_balances_braces():
    lang, body = rd.excerpt('infra/terraform/azure/main.tf::resource "azurerm_role_assignment" "agents_gold"')
    assert lang == "hcl" and body.count("{") == body.count("}")


def test_bicep_block_excerpt():
    lang, body = rd.excerpt("infra/bicep/modules/data.bicep::resource search")
    assert lang == "bicep" and body.rstrip().endswith("}")


def test_yaml_file_excerpt():
    lang, body = rd.excerpt("config/policy.yaml")
    assert lang == "yaml" and "approval:" in body


def test_unknown_name_fails():
    with pytest.raises(SystemExit):
        rd.excerpt("src/adl/core/access.py::nope")


def test_failing_command_fails_the_render():
    with pytest.raises(SystemExit):
        rd.run("lineage --dataset gold.no_such_product")
