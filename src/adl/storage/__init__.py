"""Storage adapters behind one `TableStore` interface: local Delta + DuckDB, and four cloud adapters tested with fakes."""

from __future__ import annotations

from adl.storage.remote import FakeWarehouse, StaticTokenForTests


def fake_adapters() -> dict:
    """Every cloud adapter wired to a fake client, for conformance tests and `adl adapters`."""
    from adl.storage.aws import AwsStore
    from adl.storage.bigquery import BigQueryStore
    from adl.storage.databricks import DatabricksStore
    from adl.storage.onelake import OneLakeStore

    return {
        "fabric-onelake": OneLakeStore("wf-data", "retail", FakeWarehouse(), StaticTokenForTests()),
        "azure-databricks": DatabricksStore("stwfadl", "lake", "wrenfield", "wh-retail", FakeWarehouse(), StaticTokenForTests()),
        "gcp-bigquery": BigQueryStore("wf-adl-demo", "wf-adl-lake", "retail", FakeWarehouse(), StaticTokenForTests()),
        "aws-s3-glue-athena": AwsStore("wf-adl-lake", "retail", "adl-retail", FakeWarehouse(), StaticTokenForTests()),
    }
