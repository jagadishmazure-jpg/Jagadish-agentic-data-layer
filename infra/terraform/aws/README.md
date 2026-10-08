# infra/terraform/aws

AWS stack. Details: [docs/infra/terraform-aws.md](../../../docs/infra/terraform-aws.md).

| File | What it does |
|---|---|
| `.terraform.lock.hcl` | Provider version lock (checksums), committed so every run uses the same providers |
| `.tflint.hcl` | tflint configuration with the provider ruleset |
| `backend.tf` | S3 backend (bucket supplied at init, key per environment from `envs/`) |
| `envs/` | dev and prod variables and backend state keys |
| `locals.tf` | Names, tags and derived values |
| `main.tf` | The resources |
| `outputs.tf` | Values the adapters and workflows need |
| `providers.tf` | Provider configuration |
| `tests/` | Offline plan tests with a mocked provider |
| `variables.tf` | Inputs with validation |
| `versions.tf` | Terraform and provider version constraints |
