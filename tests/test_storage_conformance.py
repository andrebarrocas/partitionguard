"""Reusable additive-storage conformance scenario for real adapter fixtures."""

import uuid
from datetime import date
from types import SimpleNamespace

import pytest
from infrastructure import owned_postgres as owned_postgres
from test_engine import migration_plan

from partitionguard.adapters import open_adapter, quote
from partitionguard.engine import Engine, EngineError, InjectedFailure
from partitionguard.ledger import Ledger

ROWS = [
    (1, date(2026, 1, 1), "10"),
    (2, date(2026, 1, 2), "20"),
    (3, date(2026, 1, 3), "30"),
    (4, date(2026, 2, 1), "outside"),
]
EXPECTED = [(1, "10", 10), (2, "20", 20), (3, "30", 30), (4, "outside", None)]


@pytest.fixture(params=["duckdb", "postgres", "delta"])
def conformance_target(request, tmp_path):
    backend = request.param
    connection = str(tmp_path / backend)
    table = "conformance_" + uuid.uuid4().hex
    if backend == "postgres":
        connection = request.getfixturevalue("owned_postgres").dsn
    if backend == "delta":
        import deltalake
        import pyarrow as pa

        deltalake.write_deltalake(
            connection,
            pa.table(
                {
                    "id": pa.array([row[0] for row in ROWS], pa.int32()),
                    "day": pa.array([row[1] for row in ROWS], pa.date32()),
                    "carrier": [row[2] for row in ROWS],
                }
            ),
            partition_by=["day"],
        )
    else:
        with open_adapter(backend, connection, read_only=False) as adapter:
            adapter.execute(f"CREATE TABLE {quote(table)} (id INTEGER, day DATE, carrier TEXT)")
            marks = ",".join([adapter.placeholder] * 3)
            for row in ROWS:
                adapter.execute(f"INSERT INTO {quote(table)} VALUES ({marks})", row)

    def factory(read_only):
        return open_adapter(backend, connection, read_only=read_only)

    def inspect():
        if backend == "delta":
            return sorted(
                (r["id"], r["carrier"], r["carrier_v2"])
                for r in deltalake.DeltaTable(connection).to_pyarrow_table().to_pylist()
            )
        with factory(True) as adapter:
            return adapter.fetch(f"SELECT id,carrier,carrier_v2 FROM {quote(table)} ORDER BY id")

    try:
        yield SimpleNamespace(
            factory=factory,
            inspect=inspect,
            connection=connection,
            plan=migration_plan(adapter=backend, table=table),
        )
    finally:
        if backend == "postgres":
            with factory(False) as adapter:
                adapter.execute(f"DROP TABLE {quote(table)}")


def exercise_storage(factory, inspect, value, ledger_path):
    """Exercise the same safety and recovery contract across adapter lifetimes."""
    with factory(True) as adapter, Ledger(ledger_path) as ledger:
        engine = Engine(adapter, ledger)
        prepared = engine.dry_run(value)
        assert prepared["status"] == "PREPARED"
        assert "carrier_v2" not in adapter.schema(value.current.table)
        with pytest.raises(EngineError, match="writable"):
            engine.apply(value, "operator", maintenance_window=True)
    with factory(False) as adapter, Ledger(ledger_path) as ledger:
        engine = Engine(adapter, ledger)
        with pytest.raises(EngineError, match="approved"):
            engine.apply(value, "operator", maintenance_window=True)
        engine.approve(value, value.id, "reviewer")
        with pytest.raises(InjectedFailure):
            engine.apply(value, "operator", maintenance_window=True, fail_after=1)
        interrupted = ledger.get(value.id)
        assert interrupted["status"] == "FAILED"
        assert len(interrupted["checkpoints"]) == 2
    with factory(False) as adapter, Ledger(ledger_path) as ledger:
        engine = Engine(adapter, ledger)
        with pytest.raises(EngineError, match="explicit resume"):
            engine.apply(value, "operator", maintenance_window=True)
        completed = engine.apply(value, "operator", resume=True, maintenance_window=True)
        assert completed["status"] == "COMPLETE"
        assert completed["verification"]["passed"] is True
        assert completed["baseline"] == prepared["baseline"]
        assert all(
            completed["checkpoints"][key] == checkpoint
            for key, checkpoint in interrupted["checkpoints"].items()
        )
        again = engine.apply(value, "operator", maintenance_window=True)
        assert again["checkpoints"] == completed["checkpoints"]
        assert ledger.verify_integrity(value.id)
    assert inspect() == EXPECTED


def test_builtin_storage_conformance(conformance_target, tmp_path):
    target = conformance_target
    exercise_storage(target.factory, target.inspect, target.plan, tmp_path / "conformance.sqlite")
