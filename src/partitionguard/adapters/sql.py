"""Small transactional storage adapters for generated, bounded SQL operations."""

from __future__ import annotations

import re
from contextlib import contextmanager
from datetime import date, timedelta
from hashlib import sha256
from pathlib import Path
from threading import Lock

from partitionguard.models import digest, normalize_type


def quote(identifier: str) -> str:
    """Quote an explicitly named identifier; reject expressions and SQL fragments."""
    parts = identifier.split(".")
    if not 1 <= len(parts) <= 3 or any(
        not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", part) for part in parts
    ):
        raise ValueError(f"Unsupported SQL identifier: {identifier!r}")
    return ".".join('"' + part + '"' for part in parts)


class SQLAdapter:
    api_version = 1
    quote = staticmethod(quote)
    _in_transaction = False

    def sql_type(self, value: str) -> str:
        canonical = normalize_type(value)
        return {"STRING": "TEXT", "DOUBLE": "DOUBLE PRECISION"}.get(canonical, canonical)

    def fetch(self, sql: str, parameters=()) -> list[tuple]:
        # Internal read interface, not a user-query sandbox. Custom assertions
        # have a separate, substantially narrower AST validation boundary.
        import sqlglot
        from sqlglot import exp

        statements = sqlglot.parse(sql, read=self.dialect)
        if len(statements) != 1 or not isinstance(statements[0], exp.Select):
            raise ValueError("fetch accepts one SELECT statement")
        if any(
            isinstance(node, (exp.DML, exp.DDL, exp.Into, exp.Lock))
            for node in statements[0].walk()
        ):
            raise ValueError("Mutating SELECT expressions are forbidden")
        return self._connection.execute(sql, parameters).fetchall()

    def execute(self, sql: str, parameters=()):
        if self.read_only:
            raise PermissionError("Adapter is read-only; explicit execution is required")
        return self._connection.execute(sql, parameters)

    @contextmanager
    def transaction(self):
        if self._in_transaction:
            raise RuntimeError("Nested adapter transactions are unsupported")
        self._in_transaction = True
        try:
            self._connection.execute("BEGIN")
            try:
                yield self
                self._connection.execute("COMMIT")
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise
        finally:
            self._in_transaction = False

    def add_column(self, table, operation):
        if self.read_only:
            raise PermissionError("Cannot add a column through a read-only adapter")
        current = self.schema(table)
        expected = normalize_type(operation.data_type)
        if operation.target in current:
            if current[operation.target] != expected:
                raise ValueError("Existing target column has an incompatible type")
            return
        self.execute(
            f"ALTER TABLE {quote(table)} ADD COLUMN {quote(operation.target)} "
            f"{self.sql_type(expected)}"
        )

    def validate_operation(self, table, operation):
        source = self.schema(table).get(operation.source)
        target = normalize_type(operation.data_type)
        pairs = {
            ("STRING", "INTEGER"),
            ("STRING", "BIGINT"),
            ("INTEGER", "BIGINT"),
            ("INTEGER", "STRING"),
            ("BIGINT", "STRING"),
        }
        if source is None or (source != target and (source, target) not in pairs):
            raise ValueError("Unsupported cast pair; no tested migration semantics")
        if operation.transform == "copy" and source != target:
            raise ValueError("Copy requires the same source and target type")

    def backfill(self, table, operation, bounds, partition):
        self.validate_operation(table, operation)
        if partition not in bounds.partitions():
            raise ValueError("Partition is outside the approved bounds")
        day = date.fromisoformat(partition)
        next_day = day + timedelta(days=1)
        scope = f"{quote(bounds.column)} >= {self.placeholder} AND {quote(bounds.column)} < {self.placeholder}"
        rows = self.fetch(
            f"SELECT {quote(operation.source)} FROM {quote(table)} WHERE {scope} LIMIT {bounds.max_rows_per_partition + 1}",
            (day, next_day),
        )
        if len(rows) > bounds.max_rows_per_partition:
            raise ValueError("Partition exceeds the approved row cap")
        if self.schema(table)[operation.source] == "STRING" and operation.data_type in (
            "INTEGER",
            "BIGINT",
        ):
            if any(
                value is not None and not re.fullmatch(r"[+-]?[0-9]+", value) for (value,) in rows
            ):
                raise ValueError(
                    "Integer conversion requires signed ASCII digits without whitespace"
                )
        source = quote(operation.source)
        if operation.transform == "cast":
            source = f"CAST({source} AS {self.sql_type(operation.data_type)})"
        elif operation.transform != "copy":
            raise ValueError("Unsupported transformation")
        self.execute(
            f"UPDATE {quote(table)} SET {quote(operation.target)} = {source} WHERE {scope}",
            (day, next_day),
        )

    def close(self):
        self._connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


_duck_locks: dict[str, Lock] = {}
_duck_locks_guard = Lock()


class DuckDBAdapter(SQLAdapter):
    name = "duckdb"
    dialect = "duckdb"
    placeholder = "?"

    def __init__(self, connection, read_only=True):
        import duckdb

        if str(connection).startswith(":"):
            raise ValueError("Use a durable DuckDB file; in-memory targets cannot be recovered")
        path = Path(connection).expanduser().resolve()
        self.read_only = read_only
        self.identity = digest({"adapter": self.name, "path": str(path)})
        self._connection = duckdb.connect(str(path), read_only=read_only)
        # Do not implicitly download extensions or allow assertions to access
        # external files, URLs, or Python replacement scans.
        self._connection.execute("SET enable_external_access = false")

    def sql_type(self, value):
        canonical = normalize_type(value)
        return {"STRING": "VARCHAR"}.get(canonical, canonical)

    def schema(self, table):
        parts = table.split(".")
        schema_name, table_name = parts if len(parts) == 2 else ("main", parts[0])
        kinds = self._connection.execute(
            "SELECT table_type FROM information_schema.tables WHERE table_schema = ? AND table_name = ?",
            (schema_name, table_name),
        ).fetchall()
        if kinds != [("BASE TABLE",)]:
            raise ValueError("DuckDB execution requires a local base table")
        rows = self._connection.execute(f"DESCRIBE SELECT * FROM {quote(table)}").fetchall()
        return {row[0]: normalize_type(row[1]) for row in rows}

    @contextmanager
    def lock(self, table):
        quote(table)
        # DuckDB excludes other writer processes; this also coordinates adapters
        # within the same Python process, where DuckDB permits several writers.
        with _duck_locks_guard:
            guard = _duck_locks.setdefault(self.identity, Lock())
        if not guard.acquire(blocking=False):
            raise RuntimeError("Another migration holds the DuckDB target lock")
        try:
            yield
        finally:
            guard.release()


class PostgresAdapter(SQLAdapter):
    name = "postgres"
    dialect = "postgres"
    placeholder = "%s"

    def __init__(self, connection, read_only=True):
        import psycopg

        self.read_only = read_only
        self._connection = psycopg.connect(connection, autocommit=True)
        self._connection.execute("SET client_encoding = 'UTF8'")
        if read_only:
            self._connection.execute("SET default_transaction_read_only = on")
        self._connection.execute("SET statement_timeout = '30s'")
        self._connection.execute("SET lock_timeout = '5s'")
        self._connection.execute("SET TIME ZONE 'UTC'")
        # pg_catalog is implicitly searched first, while unqualified DDL uses
        # public. Explicit pg_catalog first would try to create system tables.
        self._connection.execute("SET search_path = public")
        self.runtime_signature = {"server_version": self._connection.info.server_version}
        row = self._connection.execute(
            "SELECT current_database(), inet_server_addr()::text, inet_server_port()"
        ).fetchone()
        self.identity = digest(
            {
                "adapter": self.name,
                "database": row[0],
                "host": row[1] or self._connection.info.host,
                "port": row[2] or self._connection.info.port,
            }
        )

    def schema(self, table):
        metadata = self._connection.execute(
            "SELECT c.relkind, c.relrowsecurity, "
            "EXISTS (SELECT 1 FROM pg_catalog.pg_trigger t WHERE t.tgrelid = c.oid AND NOT t.tgisinternal), "
            "EXISTS (SELECT 1 FROM pg_catalog.pg_inherits i WHERE i.inhparent = c.oid OR i.inhrelid = c.oid), "
            "EXISTS (SELECT 1 FROM pg_catalog.pg_rewrite r WHERE r.ev_class = c.oid) "
            "FROM pg_catalog.pg_class c WHERE c.oid = pg_catalog.to_regclass(%s)",
            (quote(table),),
        ).fetchone()
        if metadata is None:
            raise ValueError("Target table does not exist")
        if metadata != ("r", False, False, False, False):
            raise ValueError(
                "PostgreSQL execution requires a plain table without row security, user triggers, rewrite rules, or inheritance"
            )
        rows = self._connection.execute(
            "SELECT a.attname, pg_catalog.format_type(a.atttypid, a.atttypmod) "
            "FROM pg_catalog.pg_attribute a "
            "WHERE a.attrelid = pg_catalog.to_regclass(%s) AND a.attnum > 0 "
            "AND NOT a.attisdropped ORDER BY a.attnum",
            (quote(table),),
        ).fetchall()
        if not rows:
            raise ValueError("Target table does not exist or has no supported columns")
        return {name: normalize_type(data_type) for name, data_type in rows}

    @contextmanager
    def lock(self, table):
        # A session lock survives per-batch transactions. Relation OID resolves
        # unqualified and qualified references to the same physical table.
        oid = self._connection.execute(
            "SELECT pg_catalog.to_regclass(%s)::oid", (quote(table),)
        ).fetchone()[0]
        if oid is None:
            raise ValueError("Target table does not exist")
        key = int.from_bytes(
            sha256(f"partitionguard:{oid}".encode()).digest()[:8], "big", signed=True
        )
        acquired = self._connection.execute("SELECT pg_try_advisory_lock(%s)", (key,)).fetchone()[0]
        if not acquired:
            raise RuntimeError("Another migration holds the PostgreSQL target lock")
        try:
            yield
        finally:
            self._connection.execute("SELECT pg_advisory_unlock(%s)", (key,))
