"""The CLI: every command runs and the release gate passes."""

import pytest

from adl.cli import gate_checks, main

COMMANDS = [
    "domains", "contracts", "run", "quality", "lineage", "metrics", "value-case", "forecast", "stockout", "elasticity", "markdown",
    "retrieval", "access", "agents", "injection", "value", "focus", "adapters", "iac", "mcp-demo", "a2a-demo",
]  # fmt: skip


@pytest.mark.parametrize("cmd", COMMANDS)
def test_command_runs(cmd, capsys):
    assert main([cmd]) == 0
    assert capsys.readouterr().out.strip()


def test_focus_writes_a_csv(tmp_path, capsys):
    out = tmp_path / "focus.csv"
    assert main(["focus", "--out", str(out)]) == 0
    assert out.read_text().startswith("BillingAccountId,")


def test_value_reports_a_missed_target_honestly(capsys):
    main(["value"])
    out = capsys.readouterr().out
    assert "MISSED" in out or "met" in out
    assert "assumed rates" in out


def test_release_gate_passes():
    failed = [c for c in gate_checks() if not c[1]]
    assert not failed, failed
