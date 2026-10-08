"""Microsoft Fabric OneLake adapter: Delta tables in a schema-enabled lakehouse, SQL through its SQL analytics endpoint.

* Files: `abfss://<workspace>@onelake.dfs.fabric.microsoft.com/<lakehouse>.Lakehouse/Tables/<layer>/<table>`,
  written as Delta with `deltalake` (`storage_options` carrying a bearer token, `use_fabric_endpoint`).
* Query: T-SQL against the SQL analytics endpoint, `[<lakehouse>].[<layer>].[<table>]`, `?` parameters (ODBC).
* Auth: a managed identity (or workload identity federation from GitHub) given the Contributor role on the
  workspace; tokens for `https://storage.azure.com/.default` and `https://database.windows.net/.default`.
  No keys or SAS tokens.
"""

from __future__ import annotations

from adl.storage.base import AuthSpec
from adl.storage.remote import RemoteStore


class OneLakeStore(RemoteStore):
    name = "fabric-onelake"
    table_format = "delta"
    scope = "https://storage.azure.com/.default"
    sql_scope = "https://database.windows.net/.default"

    def __init__(self, workspace: str, lakehouse: str, client, tokens) -> None:
        super().__init__(client, tokens)
        self.workspace, self.lakehouse = workspace, lakehouse

    def uri(self, layer: str, table: str) -> str:
        self._check(layer, table)
        return f"abfss://{self.workspace}@onelake.dfs.fabric.microsoft.com/{self.lakehouse}.Lakehouse/Tables/{layer}/{table}"

    def qualified(self, layer: str, table: str) -> str:
        self._check(layer, table)
        return f"[{self.lakehouse}].[{layer}].[{table}]"

    def _write_request(self, layer, table, data, mode):
        req = super()._write_request(layer, table, data, mode)
        req["storage_options"] = {"bearer_token": "<from managed identity>", "use_fabric_endpoint": "true"}
        return req

    @property
    def query_scope(self) -> str:
        return self.sql_scope

    def _query_request(self, sql, params):
        return (
            {"op": "query", "endpoint": "sql-analytics", "dialect": "tsql", "sql": sql, "parameters": params, "token_scope": self.sql_scope},
            sql,
            params,
        )

    def auth(self) -> AuthSpec:
        return AuthSpec(
            "managed identity (Entra ID)",
            "user-assigned managed identity with Contributor on the Fabric workspace",
            (self.scope, self.sql_scope),
            notes="GitHub Actions uses workload identity federation (azure/login with OIDC); no client secret.",
        )
