import os
import uuid
from datetime import date

import pytest

from partitionguard.adapters import open_adapter, quote
from partitionguard.models import Bounds, Operation


@pytest.fixture(params=["duckdb", "postgres"])
def database(request, tmp_path):
    name = request.param
    connection = str(tmp_path / "adapter.duckdb")
    if name == "postgres":
        connection = os.environ.get("PARTITIONGUARD_TEST_POSTGRES")
        if not connection:
            pytest.skip("Set PARTITIONGUARD_TEST_POSTGRES for a real PostgreSQL integration test")
    table = "pg_test_" + uuid.uuid4().hex
    with open_adapter(name, connection, read_only=False) as adapter:
        adapter.execute(f"CREATE TABLE {quote(table)} (id INTEGER, day DATE, code TEXT)")
        p = adapter.placeholder
        for row in [
            (1, date(2026, 1, 1), "10"),
            (2, date(2026, 1, 2), "20"),
            (3, date(2026, 1, 3), "30"),
        ]:
            adapter.execute(f"INSERT INTO {quote(table)} VALUES ({p}, {p}, {p})", row)
    yield name, connection, table
    with open_adapter(name, connection, read_only=False) as adapter:
        adapter.execute(f"DROP TABLE IF EXISTS {quote(table)}")


def test_default_read_only_is_enforced(database):
    name, connection, table = database
    with open_adapter(name, connection) as adapter:
        assert adapter.schema(table) == {"id": "INTEGER", "day": "DATE", "code": "STRING"}
        assert adapter.fetch(f"SELECT COUNT(*) FROM {quote(table)}") == [(3,)]
        with pytest.raises(PermissionError):
            adapter.execute(f"DELETE FROM {quote(table)}")
        with pytest.raises(ValueError):
            adapter.fetch(f"DELETE FROM {quote(table)} RETURNING id")
        with pytest.raises(ValueError):
            adapter.fetch(f"SELECT 1; DELETE FROM {quote(table)}")


def test_partition_update_is_bounded_and_idempotent(database):
    name, connection, table = database
    bounds = Bounds(column="day", start="2026-01-01", end="2026-01-03")
    operation = Operation(source="code", target="code_v2", data_type="INTEGER")
    with open_adapter(name, connection, read_only=False) as adapter:
        with adapter.lock(table):
            with adapter.transaction():
                adapter.add_column(table, operation)
            for _ in range(2):
                with adapter.transaction():
                    adapter.backfill(table, operation, bounds, "2026-01-01")
        assert adapter.fetch(f"SELECT code_v2 FROM {quote(table)} ORDER BY id") == [
            (10,),
            (None,),
            (None,),
        ]
        adapter.add_column(table, operation)
        with pytest.raises(ValueError, match="incompatible"):
            adapter.add_column(
                table, Operation(source="code", target="code_v2", data_type="BIGINT")
            )
        with pytest.raises(ValueError, match="outside"):
            adapter.backfill(table, operation, bounds, "2026-01-03")


def test_transaction_rolls_back_on_interrupt(database):
    name, connection, table = database
    operation = Operation(source="code", target="replacement", data_type="INTEGER")
    with open_adapter(name, connection, read_only=False) as adapter:
        with pytest.raises(KeyboardInterrupt):
            with adapter.transaction():
                adapter.add_column(table, operation)
                raise KeyboardInterrupt()
        assert "replacement" not in adapter.schema(table)


def test_mutation_rechecks_partition_cap(database):
    name, connection, table = database
    operation = Operation(source="code", target="replacement", data_type="INTEGER")
    bounds = Bounds(column="day", start="2026-01-01", end="2026-01-02", max_rows_per_partition=1)
    with open_adapter(name, connection, read_only=False) as adapter:
        adapter.add_column(table, operation)
        adapter.execute(f"INSERT INTO {quote(table)} SELECT * FROM {quote(table)} WHERE id = 1")
        with pytest.raises(ValueError, match="row cap"):
            adapter.backfill(table, operation, bounds, "2026-01-01")
        assert adapter.fetch(
            f"SELECT COUNT(*) FROM {quote(table)} WHERE replacement IS NOT NULL"
        ) == [(0,)]


@pytest.mark.parametrize("bad", ["1.5", " 1", "1 ", "1e2", "１２"])
def test_fractional_or_noncanonical_integer_strings_are_rejected(database, bad):
    name, connection, table = database
    operation = Operation(source="code", target="replacement", data_type="INTEGER")
    bounds = Bounds(column="day", start="2026-01-01", end="2026-01-02")
    with open_adapter(name, connection, read_only=False) as adapter:
        adapter.add_column(table, operation)
        adapter.execute(
            f"UPDATE {quote(table)} SET code = {adapter.placeholder} WHERE id = 1", (bad,)
        )
        with pytest.raises(ValueError, match="ASCII"):
            adapter.backfill(table, operation, bounds, "2026-01-01")


def test_target_lock_rejects_another_adapter(database):
    name, connection, table = database
    with open_adapter(name, connection, read_only=False) as one:
        with open_adapter(name, connection, read_only=False) as two:
            assert one.identity == two.identity
            with one.lock(table):
                with pytest.raises(RuntimeError, match="lock"):
                    with two.lock(table):
                        pytest.fail("Second lock must never be admitted")
            with two.lock(table):
                pass


def test_views_are_rejected_before_execution(database):
    name, connection, table = database
    view = table + "_view"
    with open_adapter(name, connection, read_only=False) as adapter:
        adapter.execute(f"CREATE VIEW {quote(view)} AS SELECT * FROM {quote(table)}")
        try:
            with pytest.raises(ValueError, match="table"):
                adapter.schema(view)
        finally:
            adapter.execute(f"DROP VIEW {quote(view)}")


def test_row_security_cannot_hide_source_rows(database):
    name, connection, table = database
    if name != "postgres":
        pytest.skip("PostgreSQL row security")
    with open_adapter(name, connection, read_only=False) as adapter:
        adapter.execute(f"ALTER TABLE {quote(table)} ENABLE ROW LEVEL SECURITY")
        with pytest.raises(ValueError, match="row security"):
            adapter.schema(table)


def test_rewrite_rules_cannot_hide_update_effects(database):
    name, connection, table = database
    if name != "postgres":
        pytest.skip("PostgreSQL rewrite rules")
    with open_adapter(name, connection, read_only=False) as adapter:
        adapter.execute(
            f"CREATE RULE block_updates AS ON UPDATE TO {quote(table)} DO INSTEAD NOTHING"
        )
        with pytest.raises(ValueError, match="rewrite rules"):
            adapter.schema(table)


@pytest.mark.parametrize("identifier", ["x; DROP TABLE x", '"x"', "x--", "x y", "x.*", ""])
def test_sql_fragments_are_not_identifiers(identifier):
    with pytest.raises(ValueError):
        quote(identifier)


def test_missing_read_only_database_is_not_created(tmp_path):
    target = tmp_path / "missing.duckdb"
    with pytest.raises(Exception):
        open_adapter("duckdb", str(target))
    assert not target.exists()
