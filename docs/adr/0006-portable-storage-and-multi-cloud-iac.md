# ADR 0006: One storage interface, three clouds in IaC, deploy gated off

**Status:** Accepted

## Context

The article's third mistake is staying in pilots, often because each pilot is tied to one platform.
Adopters run on Azure, Google Cloud or AWS. A portfolio must not claim deployments it has not made.

## Decision

The pipeline, metrics layer and gateway use a `TableStore` interface. The local adapter (Delta Lake +
DuckDB) is the only one run end to end; Fabric OneLake, Azure Databricks, BigQuery and S3 + Glue +
Athena adapters are written and tested against a fake warehouse with the same conformance test.
Terraform exists for all three clouds and Bicep for Azure, all identity-only (no keys), tested with
mocked providers and scanned with tflint and checkov. Deployment workflows use OIDC and are gated by
`DEPLOY_ENABLED`, which is off.

## Consequences

* Adopters can see the exact URIs, SQL dialects and identity setup per platform.
* "Written, not run" is a visible label in the README for every cloud component.
* Real SDK behaviour may differ from the fakes; the first real run should start with the adapter
  conformance test.
