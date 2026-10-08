"""AWS adapter: Parquet on S3 registered in the Glue Data Catalog, SQL through Athena.

* Files: `s3://<bucket>/<layer>/<table>/` (Parquet; Iceberg is the natural next step for updates).
* Tables: `"<prefix>_<layer>"."<table>"` in the Glue Data Catalog, one database per layer.
* Query: Athena `StartQueryExecution` with `ExecutionParameters` for the `?` placeholders, in a workgroup
  that enforces the result location and encryption.
* Auth: an IAM role assumed with `AssumeRoleWithWebIdentity` (GitHub OIDC) or the workload's own role.
  No access keys.
"""

from __future__ import annotations

from adl.storage.base import AuthSpec
from adl.storage.remote import RemoteStore


class AwsStore(RemoteStore):
    name = "aws-s3-glue-athena"
    table_format = "parquet (glue catalog)"
    scope = "sts:AssumeRoleWithWebIdentity"

    def __init__(self, bucket: str, prefix: str, workgroup: str, client, tokens, region: str = "us-east-1") -> None:
        super().__init__(client, tokens)
        self.bucket, self.prefix, self.workgroup, self.region = bucket, prefix, workgroup, region

    def uri(self, layer: str, table: str) -> str:
        self._check(layer, table)
        return f"s3://{self.bucket}/{layer}/{table}/"

    def qualified(self, layer: str, table: str) -> str:
        self._check(layer, table)
        return f'"{self.prefix}_{layer}"."{table}"'

    def _write_request(self, layer, table, data, mode):
        req = super()._write_request(layer, table, data, mode)
        req["glue_table"] = {
            "DatabaseName": f"{self.prefix}_{layer}",
            "TableInput": {
                "Name": table,
                "TableType": "EXTERNAL_TABLE",
                "StorageDescriptor": {"Location": req["uri"], "Columns": [{"Name": f.name, "Type": _glue_type(str(f.type))} for f in data.schema]},
                "Parameters": {"classification": "parquet"},
            },
        }
        return req

    def _query_request(self, sql, params):
        body = {
            "QueryString": sql,
            "WorkGroup": self.workgroup,
            "ExecutionParameters": [_athena_literal(v) for v in params],
        }
        return {"op": "query", "api": "athena:StartQueryExecution", "body": body}, sql, params

    def auth(self) -> AuthSpec:
        return AuthSpec(
            "IAM role via OIDC",
            "role assumed with AssumeRoleWithWebIdentity from GitHub, or the workload's role",
            (self.scope,),
            notes="No long-lived access keys; the trust policy pins this repository and branch.",
        )


def _glue_type(t: str) -> str:
    return {"int64": "bigint", "double": "double", "bool": "boolean", "string": "string", "large_string": "string"}.get(t, "string")


def _athena_literal(v) -> str:
    """Athena execution parameters are SQL literals: strings quoted with embedded quotes doubled."""
    if isinstance(v, str):
        return "'" + v.replace("'", "''") + "'"
    return str(v).lower() if isinstance(v, bool) else str(v)
