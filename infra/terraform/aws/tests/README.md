# infra/terraform/aws/tests

Offline plan tests: `terraform init -backend=false && terraform test`. No credentials, nothing created.

| File | What it does |
|---|---|
| `plan.tftest.hcl` | Plan-time assertions on the security properties and input validation |
