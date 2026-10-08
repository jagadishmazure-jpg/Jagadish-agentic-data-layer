"""Local adapter: Delta Lake tables on disk, queried with DuckDB. The default, and fully tested.

Tables live at `<root>/<layer>/<table>` as Delta tables (Parquet files plus a `_delta_log`), written
with the `deltalake` library, so the same files can be opened by Spark, Fabric or Databricks. DuckDB
reads them as Arrow and exposes each one as `<layer>.<table>`. DuckDB runs single-threaded so that
floating-point sums, and therefore every number in the docs, are identical on every run.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
from deltalake import DeltaTable, write_deltalake

from adl.storage.base import LAYERS, AuthSpec, WriteResult, check_layer, check_name


class LocalDeltaStore:
    name = "local"
    table_format = "delta"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.con = duckdb.connect(":memory:")
        self.con.execute("SET threads = 1")
        for layer in LAYERS:
            self.con.execute(f"CREATE SCHEMA IF NOT EXISTS {layer}")
        self._cache: dict[tuple[str, str], pa.Table] = {}

    def uri(self, layer: str, table: str) -> str:
        check_layer(layer)
        check_name(table)
        return str(self.root / layer / table)

    def qualified(self, layer: str, table: str) -> str:
        check_layer(layer)
        check_name(table)
        return f"{layer}.{table}"

    def write(self, layer: str, table: str, data: pa.Table, mode: str = "overwrite") -> WriteResult:
        path = self.uri(layer, table)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        write_deltalake(path, data, mode=mode, schema_mode="overwrite" if mode == "overwrite" else None)
        full = DeltaTable(path).to_pyarrow_table() if mode == "append" else data
        self._register(layer, table, full)
        return WriteResult(path, data.num_rows, mode, DeltaTable(path).version())

    def _register(self, layer: str, table: str, data: pa.Table) -> None:
        self._cache[(layer, table)] = data
        name = f"_arrow_{layer}_{table}"
        self.con.register(name, data)
        self.con.execute(f"CREATE OR REPLACE VIEW {layer}.{table} AS SELECT * FROM {name}")

    def stage(self, name: str, data: pa.Table) -> None:
        """Register an in-memory staging table for SQL (never written to the lake)."""
        check_name(name)
        self.con.register(name, data)

    def read(self, layer: str, table: str) -> pa.Table:
        key = (layer, table)
        if key not in self._cache:
            self._register(layer, table, DeltaTable(self.uri(layer, table)).to_pyarrow_table())
        return self._cache[key]

    def history(self, layer: str, table: str) -> list[dict[str, Any]]:
        return DeltaTable(self.uri(layer, table)).history()

    def tables(self, layer: str) -> list[str]:
        check_layer(layer)
        base = self.root / layer
        return sorted(p.name for p in base.iterdir() if (p / "_delta_log").exists()) if base.exists() else []

    def sql(self, query: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        cur = self.con.execute(query, params or [])
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]

    def arrow(self, query: str, params: list[Any] | None = None) -> pa.Table:
        return self.con.execute(query, params or []).to_arrow_table()

    def auth(self) -> AuthSpec:
        return AuthSpec("local filesystem", "the current OS user", (), True, "no network, no credentials")
