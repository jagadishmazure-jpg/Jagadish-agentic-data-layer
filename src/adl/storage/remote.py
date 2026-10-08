"""Shared shape of the cloud adapters, and the fake client they are tested with.

A cloud adapter never holds a secret. It asks a `TokenProvider` for a short-lived token (managed
identity, workload identity federation or an assumed IAM role), builds the platform's own URI, table
name and SQL parameter style, and hands the request to a client. Real clients wrap the platform SDK
and are imported lazily (install the matching extra). `FakeWarehouse` records every request and runs
the SQL on DuckDB, so the same conformance tests run against every adapter offline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol

import duckdb
import pyarrow as pa

from adl.storage.base import AuthSpec, WriteResult, check_layer, check_name


class TokenProvider(Protocol):
    def token(self, scope: str) -> str: ...


@dataclass
class StaticTokenForTests:
    """Returns a placeholder token; real deployments use DefaultAzureCredential, google.auth.default or boto3's role chain."""

    value: str = "test-token"
    asked: list[str] = field(default_factory=list)

    def token(self, scope: str) -> str:
        self.asked.append(scope)
        return self.value


@dataclass
class FakeWarehouse:
    """In-memory stand-in for a lakehouse, warehouse or query engine."""

    tables: dict[str, pa.Table] = field(default_factory=dict)
    requests: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.con = duckdb.connect(":memory:")
        self.con.execute("SET threads = 1")
        self.alias: dict[str, str] = {}

    def put(self, request: dict[str, Any], qualified: str, data: pa.Table) -> None:
        self.requests.append(request)
        self.tables[request["uri"]] = data
        name = f"t{len(self.alias)}" if qualified not in self.alias else self.alias[qualified]
        self.alias[qualified] = name
        self.con.register(name, data)

    def get(self, uri: str) -> pa.Table:
        self.requests.append({"op": "read", "uri": uri})
        return self.tables[uri]

    def query(self, request: dict[str, Any], sql: str, params: list[Any]) -> list[dict[str, Any]]:
        self.requests.append(request)
        for q, name in sorted(self.alias.items(), key=lambda kv: -len(kv[0])):
            sql = sql.replace(q, name)
        cur = self.con.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def positional_to_named(sql: str) -> tuple[str, list[str]]:
    """`?` placeholders -> `:p0, :p1, ...` (Databricks SQL Statement API style), skipping quoted strings."""
    names: list[str] = []

    def sub(m: re.Match) -> str:
        if m.group(0) != "?":
            return m.group(0)
        names.append(f"p{len(names)}")
        return f":{names[-1]}"

    return re.sub(r"'(?:[^']|'')*'|\?", sub, sql), names


class RemoteStore:
    """Common behaviour: name checks, request recording and WriteResult building."""

    name = "remote"
    table_format = "delta"
    scope = ""

    def __init__(self, client, tokens: TokenProvider) -> None:
        self.client = client
        self.tokens = tokens

    def uri(self, layer: str, table: str) -> str:  # pragma: no cover - overridden
        raise NotImplementedError

    def qualified(self, layer: str, table: str) -> str:  # pragma: no cover - overridden
        raise NotImplementedError

    def _check(self, layer: str, table: str) -> None:
        check_layer(layer)
        check_name(table)

    def _write_request(self, layer: str, table: str, data: pa.Table, mode: str) -> dict[str, Any]:
        return {
            "op": "write",
            "uri": self.uri(layer, table),
            "table": self.qualified(layer, table),
            "rows": data.num_rows,
            "mode": mode,
            "format": self.table_format,
        }

    def write(self, layer: str, table: str, data: pa.Table, mode: str = "overwrite") -> WriteResult:
        self._check(layer, table)
        if mode not in ("overwrite", "append"):
            raise ValueError("mode must be overwrite or append")
        req = self._write_request(layer, table, data, mode)
        req["token_scope"] = self.scope
        self.tokens.token(self.scope)
        self.client.put(req, self.qualified(layer, table), data)
        return WriteResult(req["uri"], data.num_rows, mode, requests=[req])

    def read(self, layer: str, table: str) -> pa.Table:
        self._check(layer, table)
        self.tokens.token(self.scope)
        return self.client.get(self.uri(layer, table))

    def tables(self, layer: str) -> list[str]:
        check_layer(layer)
        probe = self.uri(layer, "probe")
        prefix = probe[: probe.rindex("probe")]
        return sorted({u[len(prefix) :].split("/")[0] for u in self.client.tables if u.startswith(prefix)})

    def _query_request(self, sql: str, params: list[Any]) -> tuple[dict[str, Any], str, list[Any]]:
        return {"op": "query", "sql": sql, "parameters": params}, sql, params

    def sql(self, query: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        self.tokens.token(self.scope)
        req, sql, p = self._query_request(query, list(params or []))
        return self.client.query(req, sql, p)

    def auth(self) -> AuthSpec:  # pragma: no cover - overridden
        raise NotImplementedError
