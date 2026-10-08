# .github/workflows

GitHub Actions workflows. Every action is pinned to a commit SHA and every workflow defaults to read-only permissions. Details: [docs/infra/workflows.md](../../docs/infra/workflows.md).

| File | What it does |
|---|---|
| `ci.yml` | Offline CI: ruff, pytest, `adl gate`, docs drift check, FOCUS export, Bicep build, SBOM, gitleaks |
| `codeql.yml` | CodeQL for Python and GitHub Actions on push, pull request and weekly |
| `infra.yml` | Terraform fmt/validate/test, tflint and checkov for each stack; optional OIDC plans on pull requests |
| `deploy.yml` | Deploy to dev then prod per cloud with OIDC; skipped unless `DEPLOY_ENABLED` is true (it is not) |
| `teardown.yml` | Manual destroy of one environment; gated and needs the environment name typed again |
