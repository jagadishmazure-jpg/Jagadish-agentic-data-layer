"""Metrics layer: one definition per KPI, compiled to parameterised SQL.

`metrics.yaml` maps metric names to SQL aggregate expressions and dimension names to columns. Callers
ask for metrics by name, grouped by allowed dimensions, with typed filters; they never write SQL.
Filter values are bound as parameters, never pasted into the query, and only declared dimensions can
be grouped or filtered. The gateway adds the caller's row-level filter before compiling, so an agent
sees the same definition as a dashboard but only its own rows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

OPS = {"eq": "=", "ne": "<>", "lt": "<", "le": "<=", "gt": ">", "ge": ">=", "in": "IN"}


@dataclass(frozen=True)
class Metric:
    name: str
    sql: str
    description: str
    unit: str
    owner: str


@dataclass
class SemanticLayer:
    source: str
    dimensions: dict[str, str]
    metrics: dict[str, Metric] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> SemanticLayer:
        spec = yaml.safe_load(path.read_text())
        ms = {k: Metric(k, v["sql"], v["description"], v["unit"], v["owner"]) for k, v in spec["metrics"].items()}
        return cls(spec["source"], spec["dimensions"], ms)

    def compile(
        self, metrics: list[str], by: list[str] | None = None, filters: list[tuple[str, str, Any]] | None = None, source: str | None = None
    ) -> tuple[str, list[Any]]:
        by = by or []
        unknown = [m for m in metrics if m not in self.metrics]
        if unknown or not metrics:
            raise ValueError(f"unknown metrics {unknown}" if unknown else "no metrics requested")
        bad = [d for d in by if d not in self.dimensions]
        if bad:
            raise ValueError(f"unknown dimensions {bad}")
        where, params = [], []
        for dim, op, value in filters or []:
            if dim not in self.dimensions:
                raise ValueError(f"cannot filter on {dim!r}")
            if op not in OPS:
                raise ValueError(f"operator {op!r} not allowed")
            expr = self.dimensions[dim]
            if op == "in":
                values = list(value)
                if not values:
                    raise ValueError("empty IN list")
                where.append(f"{expr} IN ({', '.join('?' for _ in values)})")
                params += values
            else:
                where.append(f"{expr} {OPS[op]} ?")
                params.append(value)
        sel = [f"{self.dimensions[d]} AS {d}" for d in by] + [f"{self.metrics[m].sql} AS {m}" for m in metrics]
        sql = f"SELECT {', '.join(sel)} FROM {source or self.source}"
        if where:
            sql += " WHERE " + " AND ".join(where)
        if by:
            sql += " GROUP BY " + ", ".join(self.dimensions[d] for d in by) + " ORDER BY " + ", ".join(self.dimensions[d] for d in by)
        return sql, params

    def query(
        self, store, metrics: list[str], by: list[str] | None = None, filters: list[tuple[str, str, Any]] | None = None
    ) -> list[dict[str, Any]]:
        sql, params = self.compile(metrics, by, filters)
        rows = store.sql(sql, params)
        return [{k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()} for r in rows]
