"""Azure Databricks adapter: Delta on ADLS Gen2 governed by Unity Catalog, SQL through the Statement Execution API.

* Files: `abfss://<container>@<account>.dfs.core.windows.net/<layer>/<table>` (an external location).
* Tables: `` `<catalog>`.`<layer>`.`<table>` `` in Unity Catalog; one schema per layer.
* Query: `POST /api/2.0/sql/statements` on a SQL warehouse with named parameters (`:p0`), so values are
  never concatenated into SQL.
* Auth: an Entra ID token for the Azure Databricks resource (the well-known application id below) from a
  managed identity added as a service principal in the workspace; storage access through a Unity Catalog
  storage credential backed by an access connector's managed identity. No personal access tokens.
"""

from __future__ import annotations

from adl.storage.base import AuthSpec
from adl.storage.remote import RemoteStore, positional_to_named

AZURE_DATABRICKS_APP_ID = "2ff814a6-3304-4ab8-85cb-cd0e6f879c1d"  # first-party Azure Databricks resource, the same in every tenant


class DatabricksStore(RemoteStore):
    name = "azure-databricks"
    table_format = "delta"
    scope = f"{AZURE_DATABRICKS_APP_ID}/.default"

    def __init__(self, account: str, container: str, catalog: str, warehouse_id: str, client, tokens) -> None:
        super().__init__(client, tokens)
        self.account, self.container, self.catalog, self.warehouse_id = account, container, catalog, warehouse_id

    def uri(self, layer: str, table: str) -> str:
        self._check(layer, table)
        return f"abfss://{self.container}@{self.account}.dfs.core.windows.net/{layer}/{table}"

    def qualified(self, layer: str, table: str) -> str:
        self._check(layer, table)
        return f"`{self.catalog}`.`{layer}`.`{table}`"

    def _query_request(self, sql, params):
        named, names = positional_to_named(sql)
        body = {
            "warehouse_id": self.warehouse_id,
            "statement": named,
            "parameters": [{"name": n, "value": str(v)} for n, v in zip(names, params, strict=True)],
            "wait_timeout": "30s",
            "disposition": "INLINE",
            "format": "JSON_ARRAY",
        }
        return {"op": "query", "method": "POST", "path": "/api/2.0/sql/statements", "body": body}, sql, params

    def auth(self) -> AuthSpec:
        return AuthSpec(
            "managed identity (Entra ID) as a Databricks service principal",
            "user-assigned managed identity; storage via a Unity Catalog storage credential (access connector)",
            (self.scope,),
            notes="No personal access tokens; GitHub Actions uses OIDC federation.",
        )
