"""One storage and query interface for every platform.

The pipeline, the metrics layer and the gateway only ever talk to a `TableStore`. The local adapter
(Delta Lake files + DuckDB) is the default and the only one exercised end to end. The cloud adapters
(Fabric OneLake, Azure Databricks on ADLS Gen2, BigQuery on GCS, S3 + Glue + Athena) implement the same
methods, build the platform's own URIs, SQL dialect and requests, and authenticate without secrets
(managed identity, workload identity federation or an IAM role assumed through OIDC). They are tested
with fake clients; none has been run against a real cloud account.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import pyarrow as pa

LAYERS = ("bronze", "silver", "gold")


@dataclass(frozen=True)
class AuthSpec:
    """How an adapter proves who it is. `secretless` must be true for every adapter in this repo."""

    method: str
    identity: str
    scopes: tuple[str, ...] = ()
    secretless: bool = True
    notes: str = ""


@dataclass
class WriteResult:
    uri: str
    rows: int
    mode: str
    version: int | None = None
    requests: list[dict[str, Any]] = field(default_factory=list)


@runtime_checkable
class TableStore(Protocol):
    name: str
    table_format: str

    def uri(self, layer: str, table: str) -> str: ...

    def write(self, layer: str, table: str, data: pa.Table, mode: str = "overwrite") -> WriteResult: ...

    def read(self, layer: str, table: str) -> pa.Table: ...

    def tables(self, layer: str) -> list[str]: ...

    def sql(self, query: str, params: list[Any] | None = None) -> list[dict[str, Any]]: ...

    def qualified(self, layer: str, table: str) -> str: ...

    def auth(self) -> AuthSpec: ...


def check_layer(layer: str) -> None:
    if layer not in LAYERS:
        raise ValueError(f"unknown layer {layer!r}; expected one of {LAYERS}")


def check_name(name: str) -> None:
    if not name.replace("_", "").isalnum() or not name[0].isalpha():
        raise ValueError(f"illegal table name {name!r}")
