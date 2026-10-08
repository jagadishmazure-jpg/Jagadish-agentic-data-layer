"""Data contracts: the promise a data product makes to its consumers, as YAML, validated in CI.

A contract names the owner and steward, the schema (with PII flags), the primary key, row-level and
table-level quality checks, service levels (completeness and a freshness SLA), the data
classification and, for gold data products, the purposes it may and may not be used for. Two things
are enforced:

1. The contract file itself (`Contract` model): types, required fields per layer, checks that only
   reference real columns, PII columns that are never classified below `confidential`, and gold
   products that always state acceptable use and consumers.
2. The data against the contract (`adl.core.quality`): schema conformance, the checks and the SLOs.
   Row-level checks also produce the SQL predicate the pipeline uses to quarantine bad rows.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

CLASS_ORDER = ["public", "internal", "confidential", "restricted"]
ROW_CHECKS = {"not_null", "range", "accepted_values", "referential", "regex"}
TABLE_CHECKS = {"unique", "row_count_min", "freshness"}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Column(Strict):
    name: str
    type: Literal["string", "int", "float", "bool"]
    nullable: bool = True
    pii: bool = False
    description: str = ""


class Check(Strict):
    check: Literal["not_null", "range", "accepted_values", "referential", "regex", "unique", "row_count_min", "freshness"]
    columns: list[str] = Field(default_factory=list)
    column: str | None = None
    min: float | None = None
    max: float | None = None
    values: list[str] = Field(default_factory=list)
    ref: str | None = None  # "<contract id>.<column>"
    pattern: str | None = None
    value: float | None = None
    severity: Literal["error", "warn"] = "error"

    @property
    def targets(self) -> list[str]:
        return self.columns or ([self.column] if self.column else [])


class Owner(Strict):
    team: str
    steward: str
    contact: str


class SLO(Strict):
    completeness_pct: float = Field(ge=0, le=100)
    freshness_days: int = Field(ge=0)
    max_quarantine_pct: float = Field(default=1.0, ge=0, le=100)


class AcceptableUse(Strict):
    allowed_purposes: list[str]
    prohibited_purposes: list[str] = Field(default_factory=list)
    notes: str = ""


class Contract(Strict):
    id: str
    version: str
    domain: str
    layer: Literal["bronze", "silver", "gold"]
    table: str
    kind: Literal["source", "conformed", "data_product", "insight_product"]
    status: Literal["built", "planned"] = "built"
    description: str
    owner: Owner
    classification: Literal["public", "internal", "confidential", "restricted"]
    schema_: list[Column] = Field(alias="schema")
    primary_key: list[str]
    quality: list[Check] = Field(default_factory=list)
    slo: SLO
    acceptable_use: AcceptableUse | None = None
    consumers: list[str] = Field(default_factory=list)
    inputs: list[str] = Field(default_factory=list)
    agent_exposed: bool = False
    text_fields: list[str] = Field(default_factory=list)
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    @property
    def columns(self) -> list[str]:
        return [c.name for c in self.schema_]

    @property
    def pii_columns(self) -> list[str]:
        return [c.name for c in self.schema_ if c.pii]

    def column(self, name: str) -> Column:
        return next(c for c in self.schema_ if c.name == name)

    @model_validator(mode="after")
    def _consistent(self) -> Contract:
        cols = set(self.columns)
        if len(cols) != len(self.schema_):
            raise ValueError(f"{self.id}: duplicate column names")
        if not self.id.startswith(f"{self.domain}.{self.layer}."):
            raise ValueError(f"{self.id}: id must start with '{self.domain}.{self.layer}.'")
        if self.id.split(".")[-1] != self.table:
            raise ValueError(f"{self.id}: last id part must equal table {self.table}")
        missing = [k for k in self.primary_key if k not in cols]
        if missing:
            raise ValueError(f"{self.id}: primary key columns {missing} not in schema")
        for chk in self.quality:
            bad = [c for c in chk.targets if c not in cols]
            if bad:
                raise ValueError(f"{self.id}: check {chk.check} references unknown columns {bad}")
            if chk.check == "range" and chk.min is None and chk.max is None:
                raise ValueError(f"{self.id}: range check on {chk.column} needs min or max")
            if chk.check == "referential" and not chk.ref:
                raise ValueError(f"{self.id}: referential check needs ref")
            if chk.check in ("row_count_min", "freshness") and chk.value is None and chk.check == "row_count_min":
                raise ValueError(f"{self.id}: row_count_min needs value")
        if self.pii_columns and CLASS_ORDER.index(self.classification) < CLASS_ORDER.index("confidential"):
            raise ValueError(f"{self.id}: has PII columns {self.pii_columns} but is classified {self.classification}")
        if self.layer == "gold":
            if self.acceptable_use is None or not self.acceptable_use.allowed_purposes:
                raise ValueError(f"{self.id}: gold data products must state acceptable use")
            if not self.consumers:
                raise ValueError(f"{self.id}: gold data products must list consumers")
            overlap = set(self.acceptable_use.allowed_purposes) & set(self.acceptable_use.prohibited_purposes)
            if overlap:
                raise ValueError(f"{self.id}: purposes both allowed and prohibited: {sorted(overlap)}")
        if self.agent_exposed and (self.layer != "gold" or self.pii_columns or self.classification == "restricted"):
            raise ValueError(f"{self.id}: only gold products without PII and below restricted may be exposed to agents")
        for f in self.text_fields:
            if f not in cols:
                raise ValueError(f"{self.id}: text field {f} not in schema")
        return self


class ContractError(ValueError):
    pass


def load_contract(path: Path) -> Contract:
    try:
        return Contract.model_validate(yaml.safe_load(path.read_text()))
    except Exception as e:  # pydantic ValidationError or YAML error, reported with the file name
        raise ContractError(f"{path.name}: {e}") from e


def load_all(folder: Path) -> dict[str, Contract]:
    out: dict[str, Contract] = {}
    for p in sorted(folder.glob("*.yaml")):
        c = load_contract(p)
        if c.id in out:
            raise ContractError(f"duplicate contract id {c.id}")
        out[c.id] = c
    for c in out.values():
        for chk in c.quality:
            if chk.check == "referential":
                ref_id, _, ref_col = chk.ref.rpartition(".")
                if ref_id not in out or ref_col not in out[ref_id].columns:
                    raise ContractError(f"{c.id}: referential check points at unknown {chk.ref}")
        for i in c.inputs:
            if ".bronze." not in i and i not in out:  # bronze is schema-on-read: sources have no contract
                raise ContractError(f"{c.id}: input {i} has no contract")
    return out


def sql_type(t: str) -> str:
    return {"string": "VARCHAR", "int": "BIGINT", "float": "DOUBLE", "bool": "BOOLEAN"}[t]


def row_predicate(c: Contract, contracts: dict[str, Contract], qualified) -> str:
    """SQL boolean expression that is true for rows passing every row-level check (used for quarantine)."""
    parts: list[str] = []
    for col in c.schema_:
        if not col.nullable:
            parts.append(f'"{col.name}" IS NOT NULL')
    for chk in c.quality:
        if chk.check == "not_null":
            parts += [f'"{x}" IS NOT NULL' for x in chk.targets]
        elif chk.check == "range":
            col = f'"{chk.column}"'
            if chk.min is not None:
                parts.append(f"({col} IS NULL OR {col} >= {chk.min})")
            if chk.max is not None:
                parts.append(f"({col} IS NULL OR {col} <= {chk.max})")
        elif chk.check == "accepted_values":
            vals = ", ".join("'" + v.replace("'", "''") + "'" for v in chk.values)
            parts.append(f'("{chk.column}" IS NULL OR CAST("{chk.column}" AS VARCHAR) IN ({vals}))')
        elif chk.check == "regex":
            parts.append(f'("{chk.column}" IS NULL OR regexp_full_match("{chk.column}", \'{chk.pattern}\'))')
        elif chk.check == "referential":
            ref_id, _, ref_col = chk.ref.rpartition(".")
            ref = contracts[ref_id]
            parts.append(f'("{chk.column}" IS NULL OR "{chk.column}" IN (SELECT "{ref_col}" FROM {qualified(ref.layer, ref.table)}))')
    return " AND ".join(parts) or "TRUE"
