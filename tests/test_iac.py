"""Infrastructure and workflow structure, checked offline (no Terraform, Bicep or cloud needed)."""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github/workflows"
TF = ROOT / "infra/terraform"
BICEP = ROOT / "infra/bicep"
STACKS = ("azure", "gcp", "aws")
SHA_PIN = re.compile(r"uses:\s*[\w./-]+@[0-9a-f]{40}\b")
# Azure built-in role definition ids (public and identical in every tenant), used in infra/bicep/modules/identity.bicep.
BUILT_IN_ROLES = {
    "ba92f5b4-2d11-453d-a403-e96b0029c9fe": "Storage Blob Data Contributor",
    "2a2b9908-6ea1-4ae2-8e65-a410df84e7d1": "Storage Blob Data Reader",
    "4633458b-17de-408a-b874-0445c86b69e6": "Key Vault Secrets User",
    "1407120a-92aa-4202-b7e9-c0e197c71c8f": "Search Index Data Reader",
}


def tf(stack: str) -> str:
    return "\n".join(p.read_text() for p in sorted((TF / stack).glob("*.tf")))


def bicep() -> str:
    return "\n".join(p.read_text() for p in sorted(BICEP.rglob("*.bicep")))


def wf(name):
    return yaml.safe_load((WF / name).read_text())


# ---------------------------------------------------------------- every stack
@pytest.mark.parametrize("stack", STACKS)
def test_stack_has_the_standard_files(stack):
    for f in ("versions.tf", "providers.tf", "backend.tf", "variables.tf", "main.tf", "outputs.tf", ".tflint.hcl", "tests/plan.tftest.hcl", "envs/dev.tfvars", "envs/prod.tfvars"):
        assert (TF / stack / f).exists(), f


@pytest.mark.parametrize("stack", STACKS)
def test_no_secrets_in_any_stack(stack):
    text = tf(stack).lower()
    for word in ("client_secret", "access_key =", "secret_key", "private_key", "aws_iam_access_key", "google_service_account_key", "sas_token", "account_key"):
        assert word not in text, word


@pytest.mark.parametrize("stack", STACKS)
def test_environment_is_validated(stack):
    assert re.search(r'contains\(\["dev", "prod"\], var.environment\)', (TF / stack / "variables.tf").read_text())


@pytest.mark.parametrize("stack", STACKS)
def test_terraform_tests_use_mocked_providers(stack):
    t = (TF / stack / "tests/plan.tftest.hcl").read_text()
    assert "mock_provider" in t and 'run "dev_defaults"' in t and "expect_failures" in t


# ---------------------------------------------------------------- Azure
def test_azure_is_entra_only():
    t = tf("azure")
    assert "shared_access_key_enabled         = false" in t
    assert re.search(r"local_authentication_enabled\s*=\s*false", t.split('resource "azurerm_search_service"')[1])
    assert re.search(r"local_authentication_enabled\s*=\s*false", t.split('resource "azurerm_log_analytics_workspace"')[1])
    assert "storage_use_azuread = true" in (TF / "azure/providers.tf").read_text()
    assert "use_azuread_auth = true" in (TF / "azure/backend.tf").read_text()


def test_azure_roles_are_least_privilege():
    roles = re.findall(r'role_definition_name\s*=\s*"([^"]+)"', tf("azure"))
    assert sorted(roles) == ["Key Vault Secrets User", "Search Index Data Reader", "Storage Blob Data Contributor", "Storage Blob Data Reader"]
    agents = tf("azure").split('resource "azurerm_role_assignment" "agents_gold"')[1].split("}")[0]
    assert 'azurerm_storage_container.layer["gold"].id' in agents


def test_azure_private_networking_and_fabric_are_opt_in():
    v = (TF / "azure/variables.tf").read_text()
    for var in ("private_networking", "enable_fabric_capacity"):
        assert re.search(rf'variable "{var}"[\s\S]*?default\s*=\s*false', v), var
    assert re.search(r"private_networking\s*=\s*true", (TF / "azure/envs/prod.tfvars").read_text())


def test_bicep_mirrors_the_terraform_controls():
    b = bicep()
    assert "allowSharedKeyAccess: false" in b and "disableLocalAuth: true" in b and "features: { disableLocalAuth: true }" in b
    guids = set(re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", b))
    assert guids == set(BUILT_IN_ROLES)
    assert "param privateNetworking bool = false" in b and "param enableFabricCapacity bool = false" in b


# ---------------------------------------------------------------- Google Cloud
def test_gcp_bucket_is_never_public():
    t = tf("gcp")
    assert t.count('public_access_prevention    = "enforced"') == 2 and "uniform_bucket_level_access = true" in t


def test_gcp_agents_read_gold_only():
    t = tf("gcp")
    agents = [b for b in t.split("resource ") if "google_service_account.agents.email" in b and "iam_member" in b]
    assert any('"roles/bigquery.dataViewer"' in b and 'layer["gold"]' in b for b in agents)
    assert not any("dataEditor" in b or "objectAdmin" in b for b in agents)


def test_gcp_federation_pins_repository_and_environment():
    t = tf("gcp")
    assert "assertion.sub == 'repo:${local.github_owner}/${local.github_name}:environment:${var.environment}'" in t


# ---------------------------------------------------------------- AWS
def test_aws_buckets_block_public_access_and_use_kms():
    t = tf("aws")
    assert "block_public_policy     = true" in t and "restrict_public_buckets = true" in t
    assert 'sse_algorithm     = "aws:kms"' in t and "enable_key_rotation     = true" in t
    assert '"aws:SecureTransport" = "false"' in t


def test_aws_trust_pins_repository_and_environment():
    t = tf("aws")
    assert 'values   = ["repo:${var.github_repository}:environment:${var.environment}"]' in t
    assert 'values   = ["sts.amazonaws.com"]' in t


def test_aws_agents_read_gold_only():
    agents = tf("aws").split('data "aws_iam_policy_document" "agents"')[1].split('resource "aws_iam_role_policy" "agents"')[0]
    assert "/gold/*" in agents and "s3:PutObject" in agents.split("AthenaResults")[1]
    assert "s3:PutObject" not in agents.split("AthenaResults")[0] and "s3:DeleteObject" not in agents


# ---------------------------------------------------------------- checkov
def test_every_checkov_skip_has_a_reason():
    lines = [ln for ln in (ROOT / ".checkov.yaml").read_text().splitlines() if ln.strip().startswith("- CKV")]
    assert lines and all("#" in ln and len(ln.split("#", 1)[1].strip()) > 20 for ln in lines)


# ---------------------------------------------------------------- workflows
@pytest.mark.parametrize("name", sorted(p.name for p in WF.glob("*.yml")))
def test_workflow_hardening(name):
    d = wf(name)
    assert d["permissions"] == {"contents": "read"}, "top-level permissions must be read-only"
    text = (WF / name).read_text()
    for line in text.splitlines():
        if "uses:" in line and not line.strip().startswith("#"):
            assert SHA_PIN.search(line), f"{name}: action not pinned to a commit SHA: {line.strip()}"
    for bad in ("client-secret", "AZURE_CLIENT_SECRET", "credentials_json", "aws-secret-access-key", "aws-access-key-id"):
        assert bad not in text, bad


def test_ci_runs_tests_gate_and_drift_checks():
    text = (WF / "ci.yml").read_text()
    for step in ("pytest -q", "adl gate", "render_docs.py --check", "gitleaks", "sbom-action", "bicep build", "ruff format --check"):
        assert step in text, step


def test_infra_covers_every_stack():
    jobs = wf("infra.yml")["jobs"]
    for job in ("terraform", "tflint", "checkov"):
        assert jobs[job]["strategy"]["matrix"]["stack"] == list(STACKS)
    for cloud in STACKS:
        assert jobs[f"plan-{cloud}"]["permissions"]["id-token"] == "write"


def test_deploy_is_gated_and_uses_oidc_for_every_cloud():
    jobs = wf("deploy.yml")["jobs"]
    for name in ("deploy-dev", "deploy-prod"):
        assert "vars.DEPLOY_ENABLED == 'true'" in jobs[name]["if"]
        assert jobs[name]["permissions"]["id-token"] == "write"
        uses = " ".join(s.get("uses", "") for s in jobs[name]["steps"])
        for action in ("azure/login", "google-github-actions/auth", "aws-actions/configure-aws-credentials"):
            assert action in uses
    assert jobs["deploy-dev"]["environment"] == "dev" and jobs["deploy-prod"]["environment"] == "prod"
    assert "deploy-dev" in jobs["deploy-prod"]["needs"]
    assert "if" not in jobs["preflight"]


def test_teardown_needs_gate_and_confirmation():
    job = wf("teardown.yml")["jobs"]["teardown"]
    assert "vars.DEPLOY_ENABLED == 'true'" in job["if"] and "inputs.confirm == inputs.environment" in job["if"]


def test_codeql_scans_python_and_actions():
    text = (WF / "codeql.yml").read_text()
    assert "python" in text and "actions" in text and "security-events: write" in text


def test_dependabot_covers_every_ecosystem_and_stack():
    d = yaml.safe_load((ROOT / ".github/dependabot.yml").read_text())
    eco = {u["package-ecosystem"]: u for u in d["updates"]}
    assert set(eco) >= {"pip", "github-actions", "terraform"}
    assert eco["terraform"]["directories"] == [f"/infra/terraform/{s}" for s in STACKS]


def test_deploy_script_has_every_subcommand_and_cloud():
    text = (ROOT / ".github/scripts/deploy.sh").read_text()
    for fn in ("provision()", "smoke()", "destroy()", "azure)", "gcp)", "aws)"):
        assert fn in text
