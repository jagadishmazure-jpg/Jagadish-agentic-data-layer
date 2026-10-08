# infra/terraform

One Terraform stack per cloud, each with offline plan tests (mocked providers), tflint config, environment variables and backend keys. Run `adl iac` for a summary.

| File | What it does |
|---|---|
| `azure/` | ADLS Gen2, Log Analytics, Key Vault, AI Search, managed identities, private networking, optional Fabric capacity |
| `gcp/` | Cloud Storage, BigQuery datasets, service accounts, workload identity federation |
| `aws/` | S3 with KMS, Glue, Athena, GitHub OIDC provider, pipeline and agents roles |
