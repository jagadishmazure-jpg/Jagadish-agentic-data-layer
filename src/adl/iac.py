"""Static summary of the infrastructure definitions: what each stack declares and which controls it sets.

This reads the Terraform, Bicep and workflow files as text; it does not run Terraform or touch any
cloud. The authoritative checks are `terraform validate/test`, tflint, checkov and `bicep build` in
.github/workflows/infra.yml and ci.yml, plus tests/test_iac.py. `adl iac` prints the summary so the
docs can show real, regenerated numbers instead of hand-written ones.
"""

from __future__ import annotations

import re

import yaml

from adl import ROOT

TF = ROOT / "infra/terraform"
STACKS = ("azure", "gcp", "aws")
SHA_PIN = re.compile(r"uses:\s*[\w./-]+@[0-9a-f]{40}\b")

CONTROLS: dict[str, dict[str, str]] = {
    "azure": {
        "storage shared keys off": r"shared_access_key_enabled\s*=\s*false",
        "storage TLS 1.2 and HTTPS only": r'min_tls_version\s*=\s*"TLS1_2"[\s\S]*https_traffic_only_enabled\s*=\s*true',
        "AI Search and Log Analytics local auth off": r"local_authentication_enabled\s*=\s*false[\s\S]*local_authentication_enabled\s*=\s*false",
        "Key Vault RBAC and purge protection": r"rbac_authorization_enabled\s*=\s*true\s*\n\s*purge_protection_enabled\s*=\s*true",
        "private endpoints when private_networking": r'resource "azurerm_private_endpoint"',
        "agents read the gold container only": r'agents_gold"[\s\S]*?layer\["gold"\]\.id[\s\S]*?Storage Blob Data Reader',
    },
    "gcp": {
        "public access prevention enforced": r'public_access_prevention\s*=\s*"enforced"',
        "uniform bucket-level access": r"uniform_bucket_level_access\s*=\s*true",
        "federation pinned to repository and environment": r"assertion\.sub == 'repo:\$\{local\.github_owner\}/\$\{local\.github_name\}:environment:\$\{var\.environment\}'",
        "agents read the gold dataset only": r'agents_gold"[\s\S]*?layer\["gold"\][\s\S]*?roles/bigquery\.dataViewer',
    },
    "aws": {
        "public access block on every bucket": r"restrict_public_buckets\s*=\s*true",
        "SSE-KMS with key rotation": r'enable_key_rotation\s*=\s*true[\s\S]*sse_algorithm\s*=\s*"aws:kms"',
        "TLS-only bucket policy": r'"aws:SecureTransport"\s*=\s*"false"',
        "OIDC trust pinned to repository and environment": r"repo:\$\{var\.github_repository\}:environment:\$\{var\.environment\}",
        "Athena workgroup configuration enforced": r"enforce_workgroup_configuration\s*=\s*true",
    },
}
BICEP_CONTROLS = {
    "storage shared keys off": r"allowSharedKeyAccess:\s*false",
    "AI Search local auth off": r"disableLocalAuth:\s*true",
    "Log Analytics local auth off": r"features:\s*\{\s*disableLocalAuth:\s*true",
    "Key Vault RBAC and purge protection": r"enableRbacAuthorization:\s*true\s*\n\s*enablePurgeProtection:\s*true",
    "storage TLS 1.2": r"minimumTlsVersion:\s*'TLS1_2'",
}


def _text(paths) -> str:
    return "\n".join(p.read_text() for p in sorted(paths))


def terraform() -> list[dict]:
    out = []
    for stack in STACKS:
        d = TF / stack
        text = _text(d.glob("*.tf"))
        tests = (d / "tests/plan.tftest.hcl").read_text()
        controls = {name: bool(re.search(rx, text)) for name, rx in CONTROLS[stack].items()}
        out.append(
            {
                "stack": stack,
                "resources": len(re.findall(r'^resource "', text, re.M)),
                "data": len(re.findall(r'^data "', text, re.M)),
                "variables": len(re.findall(r'^variable "', text, re.M)),
                "outputs": len(re.findall(r'^output "', text, re.M)),
                "test_runs": len(re.findall(r'^run "', tests, re.M)),
                "controls": controls,
            }
        )
    return out


def bicep() -> dict:
    files = sorted((ROOT / "infra/bicep").rglob("*.bicep"))
    text = _text(files)
    return {
        "files": len(files),
        "resources": len(re.findall(r"^resource \w+ '", text, re.M)),
        "modules": len(re.findall(r"^module \w+ '", text, re.M)),
        "controls": {name: bool(re.search(rx, text)) for name, rx in BICEP_CONTROLS.items()},
    }


def workflows() -> list[dict]:
    out = []
    for p in sorted((ROOT / ".github/workflows").glob("*.yml")):
        text = p.read_text()
        d = yaml.safe_load(text)
        on = d.get(True, d.get("on"))  # PyYAML reads the bare key `on` as True
        uses = [ln for ln in text.splitlines() if "uses:" in ln and not ln.strip().startswith("#")]
        jobs = d["jobs"]
        out.append(
            {
                "workflow": p.name,
                "triggers": sorted(on) if isinstance(on, dict) else [on],
                "read_only": d.get("permissions") == {"contents": "read"},
                "actions": len(uses),
                "pinned": sum(1 for ln in uses if SHA_PIN.search(ln)),
                "jobs": len(jobs),
                "gated": sum(1 for j in jobs.values() if "vars.DEPLOY_ENABLED == 'true'" in str(j.get("if", ""))),
                "oidc": sum(1 for j in jobs.values() if (j.get("permissions") or {}).get("id-token") == "write"),
            }
        )
    return out


def checkov_skips() -> int:
    return sum(1 for ln in (ROOT / ".checkov.yaml").read_text().splitlines() if ln.strip().startswith("- CKV"))


def controls_ok() -> bool:
    return all(all(s["controls"].values()) for s in terraform()) and all(bicep()["controls"].values())
