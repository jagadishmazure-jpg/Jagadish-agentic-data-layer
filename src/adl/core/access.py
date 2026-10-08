"""The data gateway: the only way an agent reads data, and the place every rule is enforced.

Agents ask for a data product by name, state a purpose, and optionally pick columns, typed filters and
a row limit. Before any SQL runs, the gateway checks, in order:

1. the identity is known;
2. the name is a registered data product (a gold contract with `agent_exposed: true`), so bronze,
   silver, restricted and PII tables are unreachable by construction;
3. the identity is granted the product;
4. the purpose is granted to the identity, allowed by the product's contract and not prohibited;
5. requested columns exist and are not denied to the identity (column-level security);
6. filters use declared columns, allowed operators and values of the right type and shape, and never
   reach outside the identity's row scope;
7. the row limit is within the identity's cap.

The identity's row scope (for example region = north) is then added to the query (row-level
security), values are bound as parameters, free-text fields are screened and returned quoted as
untrusted data, and the call is written to the hash-chained audit log whether it succeeded or not.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from adl.core import guardrails
from adl.core.audit import AuditLog
from adl.core.contracts import Contract
from adl.core.semantic import OPS, SemanticLayer

GLOBAL_ROW_CAP = 500
VALUE_RE = re.compile(r"^[A-Za-z0-9 _.\-]{1,64}$")


class AccessDenied(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Identity:
    id: str
    description: str
    products: tuple[str, ...]
    purposes: tuple[str, ...]
    rows: dict[str, tuple[str, ...]] = field(default_factory=dict)
    deny_columns: dict[str, tuple[str, ...]] = field(default_factory=dict)
    max_rows: int = 100


def load_identities(path: Path) -> tuple[dict[str, Identity], dict]:
    spec = yaml.safe_load(path.read_text())
    ids = {}
    for k, v in spec["identities"].items():
        ids[k] = Identity(
            k,
            v.get("description", ""),
            tuple(v["products"]),
            tuple(v["purposes"]),
            {kk: tuple(vv) for kk, vv in (v.get("rows") or {}).items()},
            {kk: tuple(vv) for kk, vv in (v.get("deny_columns") or {}).items()},
            int(v.get("max_rows", 100)),
        )
    return ids, spec.get("knowledge", {})


class DataGateway:
    def __init__(
        self,
        store,
        contracts: dict[str, Contract],
        identities: dict[str, Identity],
        semantic: SemanticLayer,
        audit: AuditLog,
        knowledge=None,
        knowledge_policy: dict | None = None,
    ) -> None:
        self.store = store
        self.products = {c.table: c for c in contracts.values() if c.layer == "gold" and c.agent_exposed}
        self.identities = identities
        self.semantic = semantic
        self.audit = audit
        self.knowledge = knowledge
        self.knowledge_policy = knowledge_policy or {"allowed_purposes": []}
        self.region_stores: dict[str, tuple[str, ...]] = {}
        for r in store.sql("SELECT region, store_id FROM silver.stores ORDER BY store_id"):
            self.region_stores[r["region"]] = (*self.region_stores.get(r["region"], ()), r["store_id"])

    # ---------------------------------------------------------------- checks
    def _identity(self, identity: str) -> Identity:
        if identity not in self.identities:
            raise AccessDenied("unknown_identity", identity)
        return self.identities[identity]

    def _product(self, ident: Identity, product: str) -> Contract:
        if product not in self.products:
            raise AccessDenied("not_a_data_product", f"{product!r} is not a registered data product")
        if product not in ident.products:
            raise AccessDenied("product_not_granted", f"{ident.id} is not granted {product}")
        return self.products[product]

    @staticmethod
    def _purpose(ident: Identity, purpose: str, allowed: list[str], prohibited: list[str]) -> None:
        if purpose in prohibited:
            raise AccessDenied("prohibited_purpose", purpose)
        if purpose not in ident.purposes:
            raise AccessDenied("purpose_not_granted", f"{ident.id} may not use data for {purpose}")
        if purpose not in allowed:
            raise AccessDenied("purpose_not_allowed_by_contract", purpose)

    def _scope(self, ident: Identity, c: Contract) -> list[tuple[str, list[str]]]:
        """Row-level filters implied by the identity's scope, expressed on this product's columns."""
        out = []
        regions = ident.rows.get("region")
        if regions:
            if "region" in c.columns:
                out.append(("region", list(regions)))
            if "store_id" in c.columns:
                out.append(("store_id", [s for r in regions for s in self.region_stores.get(r, ())]))
        return out

    def _check_value(self, c: Contract, col: str, value: Any) -> Any:
        t = c.column(col).type
        if t == "int" and not (isinstance(value, int) and not isinstance(value, bool)):
            raise AccessDenied("bad_filter_value", f"{col} needs an integer")
        if t == "float" and not isinstance(value, (int, float)):
            raise AccessDenied("bad_filter_value", f"{col} needs a number")
        if t == "bool" and not isinstance(value, bool):
            raise AccessDenied("bad_filter_value", f"{col} needs true or false")
        if t == "string" and not (isinstance(value, str) and VALUE_RE.match(value)):
            raise AccessDenied("bad_filter_value", f"{col} value is not a plain identifier")
        return value

    # ---------------------------------------------------------------- reads
    def list_products(self, identity: str) -> list[dict[str, Any]]:
        ident = self._identity(identity)
        return [
            {
                "name": n,
                "description": c.description,
                "owner": c.owner.team,
                "classification": c.classification,
                "purposes": c.acceptable_use.allowed_purposes,
            }
            for n, c in sorted(self.products.items())
            if n in ident.products
        ]

    def describe(self, identity: str, product: str) -> dict[str, Any]:
        ident = self._identity(identity)
        c = self._product(ident, product)
        denied = set(ident.deny_columns.get(product, ()))
        return {
            "name": product,
            "description": c.description,
            "columns": [{"name": x.name, "type": x.type, "description": x.description} for x in c.schema_ if x.name not in denied],
            "primary_key": c.primary_key,
            "allowed_purposes": c.acceptable_use.allowed_purposes,
            "prohibited_purposes": c.acceptable_use.prohibited_purposes,
            "freshness_sla_days": c.slo.freshness_days,
            "row_scope": {k: list(v) for k, v in ident.rows.items()} or "all rows",
        }

    def query(
        self, identity: str, product: str, purpose: str, columns: list[str] | None = None, filters: list[list[Any]] | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        request = {"product": product, "purpose": purpose, "columns": columns, "filters": filters, "limit": limit}
        try:
            ident = self._identity(identity)
            c = self._product(ident, product)
            self._purpose(ident, purpose, c.acceptable_use.allowed_purposes, c.acceptable_use.prohibited_purposes)
            denied = set(ident.deny_columns.get(product, ()))
            cols = columns or [x for x in c.columns if x not in denied]
            for col in cols:
                if col not in c.columns:
                    raise AccessDenied("unknown_column", col)
                if col in denied:
                    raise AccessDenied("column_denied", f"{ident.id} may not read {product}.{col}")
            cap = min(ident.max_rows, GLOBAL_ROW_CAP)
            if not isinstance(limit, int) or limit < 1 or limit > cap:
                raise AccessDenied("limit_above_cap", f"limit must be 1..{cap}")
            where, params = [], []
            scope = self._scope(ident, c)
            scope_cols = dict(scope)
            for f in filters or []:
                if not isinstance(f, (list, tuple)) or len(f) != 3:
                    raise AccessDenied("bad_filter", "filters are [column, operator, value]")
                col, op, value = f
                if col not in c.columns:
                    raise AccessDenied("unknown_column", str(col)[:40])
                if op not in OPS:
                    raise AccessDenied("bad_operator", str(op)[:10])
                values = list(value) if op == "in" else [value]
                if not values or len(values) > 50:
                    raise AccessDenied("bad_filter_value", "IN needs 1..50 values")
                values = [self._check_value(c, col, v) for v in values]
                if col in scope_cols and op in ("eq", "in") and any(v not in scope_cols[col] for v in values):
                    raise AccessDenied("outside_row_scope", f"{ident.id} is limited to {col} in {scope_cols[col]}")
                where.append(f'"{col}" IN ({", ".join("?" for _ in values)})' if op == "in" else f'"{col}" {OPS[op]} ?')
                params += values
            for col, allowed in scope:
                where.append(f'"{col}" IN ({", ".join("?" for _ in allowed)})')
                params += allowed
            sql = f"SELECT {', '.join(chr(34) + x + chr(34) for x in cols)} FROM {self.store.qualified('gold', product)}"
            if where:
                sql += " WHERE " + " AND ".join(where)
            sql += " ORDER BY " + ", ".join(f'"{k}"' for k in c.primary_key) + f" LIMIT {cap if limit > cap else limit}"
            rows = self.store.sql(sql, params)
            for r in rows:
                for tf in c.text_fields:
                    if tf in r and r[tf] is not None:
                        r["injection_flag"] = bool(r.get("injection_flag")) or guardrails.screen(r[tf]).flagged
                        r[tf] = guardrails.quote_untrusted(r[tf])
        except AccessDenied as e:
            self.audit.append(identity, "data.denied", {**request, "code": e.code, "detail": e.detail})
            raise
        self.audit.append(identity, "data.read", {**request, "rows": len(rows), "row_scope": {k: list(v) for k, v in scope}})
        return rows

    def metric(
        self, identity: str, metrics: list[str], purpose: str, by: list[str] | None = None, filters: list[list[Any]] | None = None
    ) -> list[dict[str, Any]]:
        request = {"metrics": metrics, "by": by, "filters": filters, "purpose": purpose}
        try:
            ident = self._identity(identity)
            c = self._product(ident, self.semantic.source.split(".")[-1])
            self._purpose(ident, purpose, c.acceptable_use.allowed_purposes, c.acceptable_use.prohibited_purposes)
            flt = []
            for f in filters or []:
                if not isinstance(f, (list, tuple)) or len(f) != 3:
                    raise AccessDenied("bad_filter", "filters are [dimension, operator, value]")
                values = list(f[2]) if f[1] == "in" else [f[2]]
                for v in values:
                    if isinstance(v, str) and not VALUE_RE.match(v):
                        raise AccessDenied("bad_filter_value", "not a plain identifier")
                flt.append(tuple(f))
            for col, allowed in self._scope(ident, c):
                flt.append((col, "in", allowed))
            try:
                rows = self.semantic.query(self.store, metrics, by, flt)
            except ValueError as e:
                raise AccessDenied("bad_metric_request", str(e)) from e
        except AccessDenied as e:
            self.audit.append(identity, "metric.denied", {**request, "code": e.code})
            raise
        self.audit.append(identity, "metric.read", {**request, "rows": len(rows)})
        return rows

    def search_knowledge(self, identity: str, question: str, purpose: str, k: int = 5) -> list[dict[str, Any]]:
        try:
            ident = self._identity(identity)
            if "knowledge" not in ident.products:
                raise AccessDenied("product_not_granted", f"{ident.id} is not granted knowledge")
            self._purpose(ident, purpose, self.knowledge_policy.get("allowed_purposes", []), [])
            if not isinstance(question, str) or not 3 <= len(question) <= 500:
                raise AccessDenied("bad_question", "question must be 3..500 characters")
            regions = ident.rows.get("region")
            hits = self.knowledge.search(question, k=min(k, 10), regions=regions)
        except AccessDenied as e:
            self.audit.append(identity, "knowledge.denied", {"purpose": purpose, "code": e.code})
            raise
        self.audit.append(identity, "knowledge.read", {"purpose": purpose, "hits": [h.doc_id for h in hits], "regions": list(regions or ())})
        return [{"doc_id": h.doc_id, "title": h.title, "score": h.score, "text": h.text, "injection_flag": h.injection_flag} for h in hits]
