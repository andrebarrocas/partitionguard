"""Local Delta Lake execution with physical partition boundaries.

Every mutation is one Delta transaction. ``transaction`` groups exactly one
mutation for the engine interface; it cannot roll back an already committed
Delta action. A cooperative file lock and fail-fast optimistic commits require
the same maintenance-window assertion as the SQL adapters.
"""

from __future__ import annotations

import json
import re
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from partitionguard.models import digest, normalize_type

if TYPE_CHECKING:
    from partitionguard.models import Bounds, Operation


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SOURCE = "__partitionguard_delta_source"
_TO_DELTA = {
    "STRING": "string",
    "INTEGER": "integer",
    "BIGINT": "long",
    "DOUBLE": "double",
    "BOOLEAN": "boolean",
    "DATE": "date",
    "TIMESTAMP": "timestamp_ntz",
}
_FROM_DELTA = {v: k for k, v in _TO_DELTA.items()}


def _delta_type(value: str) -> str:
    return _TO_DELTA[normalize_type(value)]


def _canonical(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Nested Delta fields are not supported for execution")
    if value in _FROM_DELTA:
        return _FROM_DELTA[value]
    raise ValueError(f"Unsupported Delta physical type: {value}")


def _quote(identifier: str) -> str:
    parts = identifier.split(".")
    if not 1 <= len(parts) <= 2 or any(not _IDENTIFIER.fullmatch(p) for p in parts):
        raise ValueError(f"Unsupported SQL identifier: {identifier!r}")
    return ".".join(f'"{part}"' for part in parts)


class DeltaAdapter:
    """One local, date-partitioned Delta table; no object-store guarantees.

    ``configure_scope`` is mandatory before querying data. All query relations
    are backed by an Arrow dataset pruned to the selected physical partitions.
    Historical snapshots retain this boundary and are always read-only.
    """

    name = "delta"
    api_version = 1
    dialect = "duckdb"
    placeholder = "?"
    atomicity = "action"
    quote = staticmethod(_quote)

    def __init__(self, connection: str, read_only: bool = True, *, version: int | None = None):
        if "://" in str(connection):
            raise ValueError(
                "Delta v0.1 supports local paths only; remote coordination is not implemented"
            )
        if version is not None and not read_only:
            raise ValueError("Historical Delta snapshots are read-only")
        try:
            import duckdb
            from deltalake import DeltaTable
        except ImportError as exc:
            raise ImportError("Install partitionguard[delta] to use Delta Lake") from exc
        self.path = Path(connection).expanduser().resolve(strict=True)
        self.read_only = read_only
        self._historical = version is not None
        self._table = DeltaTable(str(self.path), version=version)
        self._table_id = self._table.metadata().id
        self.identity = digest(
            {"adapter": self.name, "path": str(self.path), "table_id": self._table_id},
        )
        self._connection = duckdb.connect(config={"enable_external_access": False})
        self._logical_table: str | None = None
        self._bounds: Bounds | None = None
        self._in_transaction = False
        self._mutations = 0
        self._lock_handle = None
        self._closed = False

    @property
    def version(self) -> int:
        return self._table.version()

    def _assert_open(self) -> None:
        if self._closed:
            raise RuntimeError("Delta adapter is closed")

    def _assert_latest(self) -> None:
        self._assert_open()
        if self._historical:
            return
        from deltalake import DeltaTable

        latest = DeltaTable(str(self.path))
        if latest.metadata().id != self._table_id or latest.version() != self.version:
            raise RuntimeError(
                "Delta table changed concurrently; reopen, inspect drift, and resume explicitly"
            )

    def _bind_table(self, table: str) -> None:
        _quote(table)
        if _SOURCE in table.split("."):
            raise ValueError("Reserved internal Delta relation name")
        if self._logical_table is not None and table != self._logical_table:
            raise ValueError("A Delta connection can access only its configured logical table")
        self._logical_table = table

    def schema(self, table: str) -> dict[str, str]:
        self._bind_table(table)
        self._assert_latest()
        return {
            field["name"]: _canonical(field["type"])
            for field in json.loads(self._table.schema().to_json())["fields"]
        }

    def sql_type(self, value: str) -> str:
        canonical = normalize_type(value)
        return {"STRING": "VARCHAR"}.get(canonical, canonical)

    def configure_scope(self, table: str, bounds: Bounds) -> None:
        self._bind_table(table)
        self._assert_latest()
        # Evaluating partitions invokes the model's hard bounds validation.
        tuple(bounds.partitions())
        if bounds.column not in self._table.metadata().partition_columns:
            raise ValueError(
                "Delta bounds must use a physical partition column; full-table rewrites are refused"
            )
        if self.schema(table).get(bounds.column) != "DATE":
            raise ValueError("Delta execution requires a physical DATE partition column")
        if self._bounds is not None and self._bounds != bounds:
            raise ValueError("Cannot change the approved scope of an open Delta adapter")
        self._bounds = bounds
        self._refresh_relation()

    def _refresh_relation(self) -> None:
        if self._bounds is None or self._logical_table is None:
            raise ValueError("Configure bounded Delta scope before reading data")
        bounds = self._bounds
        dataset = self._table.to_pyarrow_dataset(
            file_pruning_predicate=[
                (bounds.column, ">=", bounds.start.isoformat()),
                (bounds.column, "<", bounds.end.isoformat()),
            ]
        )
        self._connection.register(_SOURCE, dataset)
        parts = self._logical_table.split(".")
        if len(parts) == 2:
            self._connection.execute(f"CREATE SCHEMA IF NOT EXISTS {_quote(parts[0])}")
        self._connection.execute(
            f"CREATE OR REPLACE VIEW {_quote(self._logical_table)} AS SELECT * FROM {_quote(_SOURCE)}"
        )

    def fetch(self, sql: str, parameters=()) -> list[tuple]:
        self._assert_latest()
        if self._bounds is None:
            raise ValueError("Configure bounded Delta scope before reading data")
        import duckdb

        statements = self._connection.extract_statements(sql)
        if (
            len(statements) != 1
            or statements[0].type != duckdb.StatementType.SELECT
            or not re.match(r"\s*(SELECT|WITH)\b", sql, re.IGNORECASE)
        ):
            raise ValueError("Delta fetch accepts a single read-only SELECT statement")
        result = self._connection.execute(sql, parameters).fetchall()
        self._assert_latest()
        return result

    def execute(self, sql: str, parameters=()) -> None:
        if self.read_only:
            raise PermissionError("Delta adapter is read-only")
        raise ValueError("Delta does not expose arbitrary SQL writes; use typed migration actions")

    @contextmanager
    def lock(self, table: str):
        self._bind_table(table)
        if self.read_only:
            raise PermissionError(
                "Cannot acquire a migration writer lock through a read-only Delta adapter"
            )
        if self._lock_handle is not None:
            raise RuntimeError("Nested Delta migration locks are not supported")
        import fcntl

        handle = open(self.path / ".partitionguard.lock", "a+b")
        try:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError(
                    "Another PartitionGuard process holds the Delta writer lock"
                ) from exc
            self._lock_handle = handle
            self._assert_latest()
            yield
        finally:
            if self._lock_handle is handle:
                self._lock_handle = None
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    @contextmanager
    def transaction(self):
        self._assert_latest()
        if self._in_transaction:
            raise RuntimeError("Nested Delta action transactions are not supported")
        self._in_transaction = True
        self._mutations = 0
        try:
            yield
        finally:
            # Delta methods commit atomically before returning. An exception
            # after that commit never implies that the action was rolled back.
            self._in_transaction = False
            self._mutations = 0

    def _before_mutation(self, table: str) -> None:
        self._bind_table(table)
        self._assert_latest()
        if self.read_only:
            raise PermissionError("Delta adapter is read-only")
        if self._bounds is None:
            raise ValueError("Configure bounded Delta scope before mutations")
        if self._lock_handle is None or not self._in_transaction:
            raise RuntimeError("Delta mutations require a writer lock and an action transaction")
        if self._mutations:
            raise RuntimeError(
                "Delta action transactions support one mutation; no multi-action rollback exists"
            )
        self._mutations += 1

    def _commit_options(self) -> dict:
        from deltalake import CommitProperties, PostCommitHookProperties

        return {
            "commit_properties": CommitProperties(
                custom_metadata={
                    "partitionguard": "bounded-migration",
                    "partitionguard.atomicity": "action",
                },
                max_commit_retries=0,
            ),
            "post_commithook_properties": PostCommitHookProperties(cleanup_expired_logs=False),
        }

    def _after_mutation(self) -> None:
        self._assert_latest()
        self._refresh_relation()

    def validate_operation(self, table: str, operation: Operation) -> None:
        columns = self.schema(table)
        if operation.source not in columns:
            raise ValueError("Delta operation source column does not exist")
        source = columns[operation.source]
        target = normalize_type(operation.data_type)
        if (
            operation.source == operation.target
            or operation.target in self._table.metadata().partition_columns
        ):
            raise ValueError("Delta backfills cannot overwrite source or partition columns")
        allowed_casts = {
            ("STRING", "INTEGER"),
            ("STRING", "BIGINT"),
            ("INTEGER", "BIGINT"),
            ("INTEGER", "STRING"),
            ("BIGINT", "STRING"),
        }
        if operation.transform == "copy" and source != target:
            raise ValueError("Delta copy requires identical source and target types")
        if (
            operation.transform == "cast"
            and source != target
            and (source, target) not in allowed_casts
        ):
            raise ValueError(f"Unsupported Delta conversion: {source} to {target}")
        if target == "TIMESTAMP" and "timestampNtz" not in (
            self._table.protocol().writer_features or []
        ):
            raise ValueError(
                "Delta timestamp addition requires an existing timestampNtz protocol feature; upgrades are refused"
            )

    def add_column(self, table: str, operation: Operation) -> None:
        self._before_mutation(table)
        self.validate_operation(table, operation)
        from deltalake.schema import Field, PrimitiveType

        _quote(operation.target)
        expected = normalize_type(operation.data_type)
        existing = self.schema(table).get(operation.target)
        if existing is not None:
            if existing != expected:
                raise ValueError("Existing Delta target column has an incompatible type")
            return
        self._table.alter.add_columns(
            Field(operation.target, PrimitiveType(_delta_type(expected)), nullable=True),
            **self._commit_options(),
        )
        self._after_mutation()

    def backfill(self, table: str, operation: Operation, bounds: Bounds, partition: str) -> None:
        self._before_mutation(table)
        self.validate_operation(table, operation)
        if bounds != self._bounds or partition not in set(bounds.partitions()):
            raise ValueError("Delta backfill partition is outside the configured bounds")
        columns = self.schema(table)
        if operation.source not in columns or operation.target not in columns:
            raise ValueError("Delta backfill requires existing source and target columns")
        if (
            operation.source == operation.target
            or operation.target in self._table.metadata().partition_columns
        ):
            raise ValueError("Delta backfills cannot overwrite source or partition columns")
        expected = normalize_type(operation.data_type)
        if columns[operation.target] != expected:
            raise ValueError("Delta target type differs from the approved operation")
        rows = self.fetch(
            f"SELECT 1 FROM {_quote(table)} WHERE {_quote(bounds.column)} = ? "
            f"LIMIT {bounds.max_rows_per_partition + 1}",
            (partition,),
        )
        if len(rows) > bounds.max_rows_per_partition:
            raise ValueError("Partition exceeds the approved row cap")
        if columns[operation.source] == "STRING" and expected in {"INTEGER", "BIGINT"}:
            invalid = self.fetch(
                f"SELECT 1 FROM {_quote(table)} WHERE {_quote(bounds.column)} = ? "
                f"AND {_quote(operation.source)} IS NOT NULL "
                f"AND NOT regexp_full_match({_quote(operation.source)}, '[+-]?[0-9]+') LIMIT 1",
                (partition,),
            )
            if invalid:
                raise ValueError(
                    "Integer conversion requires ASCII signed digits without whitespace, fractions, or exponents"
                )
        source = _quote(operation.source)
        target = _quote(operation.target)
        if operation.transform == "copy":
            if columns[operation.source] != expected:
                raise ValueError(
                    "Delta copy requires identical source and target types; choose an explicit cast"
                )
            expression = source
        elif operation.transform == "cast":
            expression = f"CAST({source} AS {self.sql_type(expected)})"
        else:
            raise ValueError("Unsupported Delta transform")
        # Physical DATE partition equality limits both logical rows and the
        # files Delta may rewrite. The distinct predicate makes replay a no-op.
        predicate = (
            f"{_quote(bounds.column)} = DATE '{partition}' AND "
            f"{target} IS DISTINCT FROM {expression}"
        )
        self._table.update(
            updates={operation.target: expression},
            predicate=predicate,
            error_on_type_mismatch=True,
            **self._commit_options(),
        )
        self._after_mutation()

    def history(self, limit: int | None = None) -> list[dict]:
        self._assert_latest()
        if limit is not None and limit < 1:
            raise ValueError("History limit must be positive")
        return self._table.history(limit=limit)

    def snapshot(self, version: int) -> DeltaAdapter:
        self._assert_latest()
        if version < 0:
            raise ValueError("Delta version must be nonnegative")
        result = DeltaAdapter(str(self.path), read_only=True, version=version)
        if result._table_id != self._table_id:
            result.close()
            raise RuntimeError("Historical snapshot belongs to a different Delta table identity")
        if self._bounds is not None and self._logical_table is not None:
            try:
                result.configure_scope(self._logical_table, self._bounds)
            except BaseException:
                result.close()
                raise
        return result

    def evidence(self) -> dict:
        self._assert_latest()
        return {
            "version": self.version,
            "table_id": self._table_id,
            "atomicity": self.atomicity,
            "historical": self._historical,
        }

    def close(self) -> None:
        if not self._closed:
            self._connection.close()
            self._closed = True

    def __enter__(self) -> DeltaAdapter:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
