"""Validated internal objects. ODCS remains the public contract format."""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_TYPES = {
    "STRING": "STRING",
    "VARCHAR": "STRING",
    "TEXT": "STRING",
    "CHARACTER VARYING": "STRING",
    "INT": "INTEGER",
    "INT4": "INTEGER",
    "INTEGER": "INTEGER",
    "INT32": "INTEGER",
    "BIGINT": "BIGINT",
    "INT8": "BIGINT",
    "LONG": "BIGINT",
    "INT64": "BIGINT",
    "DOUBLE": "DOUBLE",
    "DOUBLE PRECISION": "DOUBLE",
    "FLOAT64": "DOUBLE",
    "NUMBER": "DOUBLE",
    "BOOL": "BOOLEAN",
    "BOOLEAN": "BOOLEAN",
    "DATE": "DATE",
    "TIMESTAMP": "TIMESTAMP",
    "TIMESTAMP WITHOUT TIME ZONE": "TIMESTAMP",
}


def normalize_type(value: str) -> str:
    normalized = " ".join(value.upper().split())
    if normalized not in _TYPES:
        raise ValueError(
            f"Unsupported physical type {value!r}; parameterized/nested types need an adapter"
        )
    return _TYPES[normalized]


def identifier(value: str, qualified: bool = False) -> str:
    parts = value.split(".") if qualified else [value]
    if len(parts) > 2 or not all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", p) for p in parts):
        raise ValueError(f"Unsupported identifier {value!r}; use simple SQL identifiers")
    return value


def canonical_json(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class Column(Model):
    name: str
    data_type: str
    nullable: bool = True
    description: str = ""
    enum: tuple[str | int | float | bool, ...] = ()

    _name = field_validator("name")(identifier)
    _type = field_validator("data_type")(normalize_type)


class Dependency(Model):
    name: str = Field(min_length=1)
    kind: Literal["product", "table", "sql_model", "transformation", "quality", "consumer"] = (
        "product"
    )
    upstream: tuple[str, ...] = ()
    columns: tuple[str, ...] = ()
    sla: bool = False


class Check(Model):
    kind: Literal[
        "unique", "not_null", "null_rate", "aggregate", "freshness", "sql", "range", "enum"
    ]
    column: str | None = None
    columns: tuple[str, ...] = ()
    reference: str | None = None
    threshold: float = Field(default=0, ge=0)
    absolute_tolerance: float = Field(default=0, ge=0)
    relative_tolerance: float = Field(default=0, ge=0)
    sql: str | None = None
    values: tuple[Any, ...] = ()

    @model_validator(mode="after")
    def valid(self):
        for name in (*self.columns, self.column, self.reference):
            if name is not None:
                identifier(name)
        if self.kind == "sql":
            if not self.sql or "{scope}" not in self.sql:
                raise ValueError("SQL checks require a single scalar SELECT using {scope}")
        elif self.kind == "unique":
            if not self.columns and not self.column:
                raise ValueError("Unique check requires columns")
        elif not self.column:
            raise ValueError(f"{self.kind} check requires column")
        if self.kind == "aggregate" and not self.reference:
            raise ValueError("Aggregate parity requires reference column")
        if self.kind == "null_rate" and self.threshold > 1:
            raise ValueError("Null-rate threshold is a fraction in [0, 1]")
        if self.kind == "range":
            if (
                len(self.values) != 2
                or any(
                    isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                    for v in self.values
                )
                or self.values[0] > self.values[1]
            ):
                raise ValueError("Range requires finite inclusive [lower, upper] values")
        if self.kind == "enum" and not self.values:
            raise ValueError("Enum requires at least one value")
        canonical_json(self.values)
        return self


class Contract(Model):
    product: str = Field(min_length=1)
    version: str = Field(min_length=1)
    owner: str = Field(min_length=1)
    table: str
    columns: tuple[Column, ...] = Field(min_length=1)
    primary_key: tuple[str, ...] = ()
    business_keys: tuple[str, ...] = ()
    partition_by: tuple[str, ...] = ()
    dependencies: tuple[Dependency, ...] = ()
    quality: tuple[Check, ...] = ()
    sla: dict[str, Any] = Field(default_factory=dict)
    retention: dict[str, Any] = Field(default_factory=dict)
    compatibility: Literal["backward", "strict", "none"] = "backward"
    metadata: dict[str, Any] = Field(default_factory=dict)
    unsupported: tuple[str, ...] = ()

    @field_validator("table")
    @classmethod
    def table_name(cls, v):
        return identifier(v, qualified=True)

    @model_validator(mode="after")
    def valid(self):
        names = [c.name for c in self.columns]
        if len(set(names)) != len(names):
            raise ValueError("Duplicate columns")
        dependency_names = [dependency.name for dependency in self.dependencies]
        if len(set(dependency_names)) != len(dependency_names):
            raise ValueError("Duplicate dependency node names")
        for keys in (self.primary_key, self.business_keys, self.partition_by):
            if len(set(keys)) != len(keys) or not set(keys) <= set(names):
                raise ValueError("Keys must be unique existing column names")
        for check in self.quality:
            used = set(check.columns) | {v for v in (check.column, check.reference) if v}
            if not used <= set(names):
                raise ValueError("Quality check references an absent column")
        return self

    @property
    def column_map(self) -> dict[str, Column]:
        return {c.name: c for c in self.columns}


class Bounds(Model):
    column: str
    start: date
    end: date
    max_partitions: int = Field(default=366, ge=1, le=10000, strict=True)
    max_rows_per_partition: int = Field(default=1_000_000, ge=1, strict=True)
    max_total_rows: int = Field(default=10_000_000, ge=1, strict=True)

    _column = field_validator("column")(identifier)

    @model_validator(mode="after")
    def valid(self):
        if not 0 < (self.end - self.start).days <= self.max_partitions:
            raise ValueError("Date range must be nonempty, half-open, and within max_partitions")
        return self

    def partitions(self) -> tuple[str, ...]:
        return tuple(
            (self.start + timedelta(days=i)).isoformat()
            for i in range((self.end - self.start).days)
        )


class Operation(Model):
    source: str
    target: str
    data_type: str
    transform: Literal["copy", "cast"] = "cast"

    _names = field_validator("source", "target")(identifier)
    _type = field_validator("data_type")(normalize_type)

    @model_validator(mode="after")
    def valid(self):
        if self.source == self.target:
            raise ValueError(
                "In-place rewrites are unsupported; choose an additive replacement column"
            )
        return self


class MigrationSpec(Model):
    bounds: Bounds
    operations: tuple[Operation, ...] = ()
    checks: tuple[Check, ...] = ()
    estimated_bytes: int | None = Field(default=None, ge=0)
    require_nonempty: bool = True
    maintenance_window: bool = True

    @model_validator(mode="after")
    def valid(self):
        targets = [o.target for o in self.operations]
        if len(set(targets)) != len(targets):
            raise ValueError("Each target column can have only one operation")
        if not self.maintenance_window:
            raise ValueError("Online writers are unsupported; maintenance_window must be true")
        return self


class Change(Model):
    kind: str
    path: str
    before: Any = None
    after: Any = None
    compatibility: Literal["SAFE", "WARNING", "BREAKING"]
    reason: str


class Impact(Model):
    direct: tuple[str, ...] = ()
    transitive: tuple[str, ...] = ()
    transformations: tuple[str, ...] = ()
    quality_checks: tuple[str, ...] = ()
    slas: tuple[str, ...] = ()
    unknown: tuple[str, ...] = ()


class Risk(Model):
    level: Literal["LOW", "MEDIUM", "HIGH", "BLOCKING"]
    reasons: tuple[str, ...] = ()


class Action(Model):
    id: str
    kind: str
    description: str
    partition: str | None = None
    operation: Operation | None = None


class Plan(Model):
    format_version: Literal[1] = 1
    adapter: str
    current: Contract
    proposed: Contract
    changes: tuple[Change, ...] = ()
    impact: Impact = Field(default_factory=Impact)
    risk: Risk
    migration: MigrationSpec | None = None
    actions: tuple[Action, ...] = ()
    blockers: tuple[str, ...] = ()

    @property
    def id(self) -> str:
        return digest(self)
