"""Real PostgreSQL approval, crash, checkpoint reconciliation, and verification."""

import os
import uuid

import pytest

from partitionguard.adapters import open_adapter, quote
from partitionguard.engine import Engine, EngineError, InjectedFailure
from partitionguard.ledger import Ledger
from partitionguard.models import Bounds, Check, Column, Contract, MigrationSpec, Operation
from partitionguard.planning import plan as build_plan


@pytest.mark.parametrize("failure_mode", ["after_checkpoint", "after_commit_before_checkpoint"])
def test_postgres_approved_migration_recovers_and_rechecks(tmp_path, monkeypatch, failure_mode):
    connection = os.environ.get("PARTITIONGUARD_TEST_POSTGRES")
    if not connection:
        pytest.skip("Set PARTITIONGUARD_TEST_POSTGRES for a real PostgreSQL engine test")
    table = "pg_engine_" + uuid.uuid4().hex
    ledger_path = tmp_path / "ledger.sqlite"
    current = Contract(
        product="events",
        owner="integration-test",
        version="1",
        table=table,
        columns=(
            Column(name="id", data_type="INTEGER", nullable=False),
            Column(name="day", data_type="DATE", nullable=False),
            Column(name="code", data_type="STRING"),
        ),
        primary_key=("id",),
        partition_by=("day",),
    )
    proposed = current.model_copy(
        update={
            "version": "2",
            "columns": current.columns + (Column(name="code_v2", data_type="INTEGER"),),
        }
    )
    operation = Operation(source="code", target="code_v2", data_type="INTEGER")
    recipe = MigrationSpec(
        bounds=Bounds(column="day", start="2026-01-01", end="2026-01-03"),
        operations=(operation,),
        checks=(Check(kind="sql", sql="SELECT SUM(code_v2) = 30 FROM {scope}"),),
    )
    plan = build_plan(current, proposed, adapter="postgres", migration=recipe)
    assert not plan.blockers
    with open_adapter("postgres", connection, read_only=False) as adapter:
        adapter.execute(f"CREATE TABLE {quote(table)} (id INTEGER, day DATE, code TEXT)")
        adapter.execute(
            f"INSERT INTO {quote(table)} VALUES (1, '2026-01-01', '10'), (2, '2026-01-02', '20'), (3, '2026-01-03', 'out_of_scope')"
        )
    try:
        with Ledger(ledger_path) as ledger:
            with open_adapter("postgres", connection) as adapter:
                engine = Engine(adapter, ledger)
                assert engine.dry_run(plan)["status"] == "PREPARED"
                assert "code_v2" not in adapter.schema(table)
                with pytest.raises(EngineError, match="digest"):
                    engine.approve(plan, "wrong-plan", operator="reviewer")
                assert engine.approve(plan, plan.id, operator="reviewer")["status"] == "APPROVED"
            with open_adapter("postgres", connection, read_only=False) as adapter:
                engine = Engine(adapter, ledger)
                with pytest.raises(EngineError, match="maintenance"):
                    engine.apply(plan, operator="executor")
                if failure_mode == "after_checkpoint":
                    with pytest.raises(InjectedFailure):
                        engine.apply(
                            plan, operator="executor", maintenance_window=True, fail_after=1
                        )
                else:
                    checkpoint = ledger.checkpoint

                    def crash_after_commit(plan_id, action_id, details):
                        if action_id == "backfill:code_v2:2026-01-01":
                            raise RuntimeError("simulated process loss at commit boundary")
                        return checkpoint(plan_id, action_id, details)

                    with monkeypatch.context() as patch:
                        patch.setattr(ledger, "checkpoint", crash_after_commit)
                        with pytest.raises(RuntimeError, match="commit boundary"):
                            engine.apply(plan, operator="executor", maintenance_window=True)
                assert ledger.get(plan.id)["status"] == "FAILED"
                assert adapter.fetch(f"SELECT code_v2 FROM {quote(table)} ORDER BY id") == [
                    (10,),
                    (None,),
                    (None,),
                ]
                with pytest.raises(EngineError, match="resume"):
                    engine.apply(plan, operator="executor", maintenance_window=True)
        # Close both client and ledger to prove recovery is persisted state.
        with Ledger(ledger_path) as ledger:
            with open_adapter("postgres", connection, read_only=False) as adapter:
                engine = Engine(adapter, ledger)
                recovered = engine.apply(
                    plan, operator="recovery-operator", resume=True, maintenance_window=True
                )
                assert recovered["status"] == "COMPLETE"
                assert recovered["verification"]["passed"] is True
                assert adapter.fetch(
                    f"SELECT id, code, code_v2 FROM {quote(table)} ORDER BY id"
                ) == [(1, "10", 10), (2, "20", 20), (3, "out_of_scope", None)]
                assert (
                    engine.apply(plan, operator="executor", maintenance_window=True)["status"]
                    == "COMPLETE"
                )
                adapter.execute(f"UPDATE {quote(table)} SET code_v2 = 999 WHERE id = 1")
                assert engine.verify(plan)["passed"] is False
                assert ledger.get(plan.id)["status"] == "VERIFICATION_FAILED"
                assert (
                    engine.apply(
                        plan, operator="recovery-operator", resume=True, maintenance_window=True
                    )["status"]
                    == "COMPLETE"
                )
                assert any(event["kind"] == "EXECUTION_FAILED" for event in ledger.events(plan.id))
    finally:
        with open_adapter("postgres", connection, read_only=False) as adapter:
            adapter.execute(f"DROP TABLE IF EXISTS {quote(table)}")
