"""Google Cloud adapter: Parquet on Cloud Storage loaded into BigQuery, one dataset per layer.

* Files: `gs://<bucket>/<layer>/<table>/` (Parquet), loaded with a BigQuery load job
  (WRITE_TRUNCATE or WRITE_APPEND).
* Tables: `` `<project>.<prefix>_<layer>.<table>` ``.
* Query: GoogleSQL with positional `?` parameters (query parameters, never string formatting).
* Auth: a service account reached through workload identity federation (GitHub OIDC) or attached to the
  workload; `google.auth.default()` with the cloud-platform scope. No service account keys.
"""

from __future__ import annotations

from adl.storage.base import AuthSpec
from adl.storage.remote import RemoteStore


class BigQueryStore(RemoteStore):
    name = "gcp-bigquery"
    table_format = "bigquery (parquet load from gcs)"
    scope = "https://www.googleapis.com/auth/cloud-platform"

    def __init__(self, project: str, bucket: str, prefix: str, client, tokens, location: str = "US") -> None:
        super().__init__(client, tokens)
        self.project, self.bucket, self.prefix, self.location = project, bucket, prefix, location

    def uri(self, layer: str, table: str) -> str:
        self._check(layer, table)
        return f"gs://{self.bucket}/{layer}/{table}/"

    def qualified(self, layer: str, table: str) -> str:
        self._check(layer, table)
        return f"`{self.project}.{self.prefix}_{layer}.{table}`"

    def _write_request(self, layer, table, data, mode):
        req = super()._write_request(layer, table, data, mode)
        req["load_job"] = {
            "sourceUris": [req["uri"] + "*.parquet"],
            "sourceFormat": "PARQUET",
            "writeDisposition": "WRITE_TRUNCATE" if mode == "overwrite" else "WRITE_APPEND",
            "destinationTable": {"projectId": self.project, "datasetId": f"{self.prefix}_{layer}", "tableId": table},
        }
        return req

    def _query_request(self, sql, params):
        body = {
            "query": sql,
            "useLegacySql": False,
            "parameterMode": "POSITIONAL",
            "queryParameters": [{"parameterType": {"type": _bq_type(v)}, "parameterValue": {"value": str(v)}} for v in params],
            "location": self.location,
        }
        return {"op": "query", "method": "POST", "path": f"/bigquery/v2/projects/{self.project}/queries", "body": body}, sql, params

    def auth(self) -> AuthSpec:
        return AuthSpec(
            "workload identity federation",
            "service account impersonated from a GitHub OIDC token or attached to the workload",
            (self.scope,),
            notes="No service account key files; the pool trusts only this repository.",
        )


def _bq_type(v) -> str:
    if isinstance(v, bool):
        return "BOOL"
    if isinstance(v, int):
        return "INT64"
    if isinstance(v, float):
        return "FLOAT64"
    return "STRING"
