# infra/terraform/aws/envs

Per-environment inputs. Backend files hold only the state key; the state location comes from repository variables at init.

| File | What it does |
|---|---|
| `dev.backend.hcl` | State key for dev |
| `dev.tfvars` | dev variables |
| `prod.backend.hcl` | State key for prod |
| `prod.tfvars` | prod variables |
