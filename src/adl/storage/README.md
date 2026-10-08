# src/adl/storage

Storage and query adapters behind one interface. Only the local adapter has run end to end.

| File | What it does |
|---|---|
| `__init__.py` | `fake_adapters()`: every cloud adapter wired to the fake warehouse |
| `base.py` | `TableStore` protocol, layer and name checks |
| `local.py` | Delta Lake files queried with DuckDB (default) |
| `remote.py` | Shared remote-adapter base, token provider, fake warehouse, parameter conversion |
| `onelake.py` | Microsoft Fabric OneLake with the SQL analytics endpoint (written, not run) |
| `databricks.py` | Azure Databricks on ADLS Gen2 with the Statement Execution API (written, not run) |
| `bigquery.py` | BigQuery on Cloud Storage with load jobs and typed parameters (written, not run) |
| `aws.py` | S3 with Glue and Athena (written, not run) |
