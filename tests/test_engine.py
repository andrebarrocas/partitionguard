from datetime import date

import pytest

from partitionguard.adapters import open_adapter
from partitionguard.engine import (
    Engine,
    EngineError,
    InjectedFailure,
    SourceDriftError,
    VerificationFailed,
)
from partitionguard.ledger import Ledger
from partitionguard.models import Bounds, Check, Column, Contract, MigrationSpec, Operation
from partitionguard.planning import plan


def migration_plan(checks=(), *, adapter="duckdb", table="shipments"):
    current = Contract(
        product="shipments",
        version="1",
        owner="data-platform",
        table=table,
        columns=(
            Column(name="id", data_type="INTEGER", nullable=False),
            Column(name="day", data_type="DATE", nullable=False),
            Column(name="carrier", data_type="STRING"),
        ),
        primary_key=("id",),
        partition_by=("day",),
    )
    proposed = current.model_copy(
        update={
            "version": "2",
            "columns": current.columns + (Column(name="carrier_v2", data_type="INTEGER"),),
        }
    )
    recipe = MigrationSpec(
        bounds=Bounds(column="day", start=date(2026, 1, 1), end=date(2026, 1, 4)),
        operations=(Operation(source="carrier", target="carrier_v2", data_type="INTEGER"),),
        checks=checks,
    )
    result = plan(current, proposed, adapter, recipe)
    assert result.blockers == ()
    return result


@pytest.fixture
def case(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    database = tmp_path / "warehouse.duckdb"
    connection = duckdb.connect(str(database))
    connection.execute("CREATE TABLE shipments(id INTEGER, day DATE, carrier VARCHAR)")
    connection.execute(
        "INSERT INTO shipments VALUES (1, '2026-01-01', '10'), (2, '2026-01-02', '20'), (3, '2026-01-03', '30'), (4, '2026-02-01', '40')"
    )
    connection.close()
    with open_adapter("duckdb", str(database), read_only=False) as adapter:
        with Ledger(tmp_path / "ledger.sqlite") as ledger:
            yield Engine(adapter, ledger), adapter, ledger, migration_plan()


def approved(case):
    engine, adapter, ledger, value = case
    engine.dry_run(value)
    engine.approve(value, value.id, "reviewer")
    return case


def test_dry_run_never_changes_schema_or_records(case):
    engine, adapter, ledger, value = case
    before = adapter.fetch("SELECT * FROM shipments ORDER BY id")
    result = engine.dry_run(value)
    assert result["status"] == "PREPARED"
    assert "carrier_v2" not in adapter.schema("shipments")
    assert adapter.fetch("SELECT * FROM shipments ORDER BY id") == before
    assert result["baseline"]["total_rows"] == 3
    assert ledger.get(value.id)["approved_digest"] is None


def test_approval_and_maintenance_window_are_required(case):
    engine, _, ledger, value = case
    with pytest.raises(EngineError, match="dry-run"):
        engine.approve(value, value.id, "reviewer")
    engine.dry_run(value)
    with pytest.raises(EngineError, match="full immutable"):
        engine.approve(value, value.id[:12], "reviewer")
    with pytest.raises(EngineError, match="not been approved"):
        engine.apply(value, "operator", maintenance_window=True)
    engine.approve(value, value.id, "reviewer")
    with pytest.raises(EngineError, match="maintenance-window"):
        engine.apply(value, "operator")
    assert ledger.get(value.id)["status"] == "APPROVED"


def test_apply_verifies_and_only_mutates_approved_scope(case):
    engine, adapter, ledger, value = approved(case)
    result = engine.apply(value, "operator", maintenance_window=True)
    assert result["status"] == "COMPLETE"
    assert result["verification"]["passed"]
    assert adapter.fetch("SELECT id, carrier, carrier_v2 FROM shipments ORDER BY id") == [
        (1, "10", 10),
        (2, "20", 20),
        (3, "30", 30),
        (4, "40", None),
    ]
    assert len(result["checkpoints"]) == 4
    assert ledger.verify_integrity(value.id)


def test_completed_apply_is_idempotent_and_reverifies(case, monkeypatch):
    engine, adapter, _, value = approved(case)
    engine.apply(value, "operator", maintenance_window=True)
    monkeypatch.setattr(
        adapter, "backfill", lambda *args: pytest.fail("Complete action must not rerun")
    )
    assert engine.apply(value, "operator", maintenance_window=True)["status"] == "COMPLETE"


def test_failure_resume_skips_reconciled_checkpoints(case, monkeypatch):
    engine, adapter, ledger, value = approved(case)
    with pytest.raises(InjectedFailure):
        engine.apply(value, "operator", maintenance_window=True, fail_after=1)
    failed = ledger.get(value.id)
    assert failed["status"] == "FAILED"
    assert len(failed["checkpoints"]) == 2
    with pytest.raises(EngineError, match="explicit resume"):
        engine.apply(value, "operator", maintenance_window=True)
    calls = []
    actual = adapter.backfill

    def track(table, operation, bounds, partition):
        calls.append(partition)
        return actual(table, operation, bounds, partition)

    monkeypatch.setattr(adapter, "backfill", track)
    complete = engine.apply(value, "operator", resume=True, maintenance_window=True)
    assert complete["status"] == "COMPLETE"
    assert complete["attempts"] == 2
    assert calls == ["2026-01-02", "2026-01-03"]
    assert any(e["kind"] == "EXECUTION_FAILED" for e in ledger.events(value.id))


def test_commit_before_checkpoint_crash_replays_safely(case, monkeypatch):
    engine, adapter, ledger, value = approved(case)
    actual = ledger.checkpoint
    crashed = False

    def crash_once(plan_id, action_id, detail):
        nonlocal crashed
        if action_id.startswith("backfill:") and not crashed:
            crashed = True
            raise KeyboardInterrupt("simulated process interruption after target commit")
        return actual(plan_id, action_id, detail)

    monkeypatch.setattr(ledger, "checkpoint", crash_once)
    with pytest.raises(KeyboardInterrupt):
        engine.apply(value, "operator", maintenance_window=True)
    assert adapter.fetch("SELECT carrier_v2 FROM shipments WHERE id=1") == [(10,)]
    assert "backfill:carrier_v2:2026-01-01" not in ledger.get(value.id)["checkpoints"]
    assert (
        engine.apply(value, "operator", resume=True, maintenance_window=True)["status"]
        == "COMPLETE"
    )
    assert adapter.fetch("SELECT COUNT(*) FROM shipments") == [(4,)]


def test_resume_repairs_changed_target_instead_of_trusting_checkpoint(case):
    engine, adapter, ledger, value = approved(case)
    with pytest.raises(InjectedFailure):
        engine.apply(value, "operator", maintenance_window=True, fail_after=1)
    adapter.execute("UPDATE shipments SET carrier_v2=999 WHERE id=1")
    assert (
        engine.apply(value, "operator", resume=True, maintenance_window=True)["status"]
        == "COMPLETE"
    )
    assert adapter.fetch("SELECT carrier_v2 FROM shipments WHERE id=1") == [(10,)]
    assert any(e["kind"] == "ACTION_REPLAY_REQUIRED" for e in ledger.events(value.id))


def test_source_value_drift_fails_even_when_row_count_unchanged(case):
    engine, adapter, ledger, value = approved(case)
    original = ledger.get(value.id)["baseline"]
    adapter.execute("UPDATE shipments SET carrier='11' WHERE id=1")
    with pytest.raises(SourceDriftError, match="cannot be replaced"):
        engine.apply(value, "operator", maintenance_window=True)
    assert ledger.get(value.id)["status"] == "FAILED"
    assert ledger.get(value.id)["baseline"] == original
    assert "carrier_v2" not in adapter.schema("shipments")


def test_source_drift_after_checkpoint_blocks_resume(case):
    engine, adapter, ledger, value = approved(case)
    with pytest.raises(InjectedFailure):
        engine.apply(value, "operator", maintenance_window=True, fail_after=1)
    adapter.execute("UPDATE shipments SET carrier='11' WHERE id=1")
    with pytest.raises(SourceDriftError):
        engine.apply(value, "operator", resume=True, maintenance_window=True)
    assert ledger.get(value.id)["status"] == "FAILED"


def test_preexisting_target_is_not_adopted(case):
    engine, adapter, ledger, value = case
    adapter.execute("ALTER TABLE shipments ADD COLUMN carrier_v2 INTEGER")
    with pytest.raises(EngineError, match="already exist"):
        engine.dry_run(value)
    assert ledger.get(value.id) is None


def test_replacement_created_after_dry_run_is_not_overwritten(case):
    engine, adapter, ledger, value = approved(case)
    adapter.execute("ALTER TABLE shipments ADD COLUMN carrier_v2 INTEGER")
    adapter.execute("UPDATE shipments SET carrier_v2=999")
    with pytest.raises(EngineError, match="already exist"):
        engine.apply(value, "operator", maintenance_window=True)
    assert adapter.fetch("SELECT carrier_v2 FROM shipments WHERE id=1") == [(999,)]
    # A failed admission must not turn a foreign target into a resumable action.
    with pytest.raises(EngineError, match="Cannot adopt"):
        engine.apply(value, "operator", resume=True, maintenance_window=True)
    assert adapter.fetch("SELECT carrier_v2 FROM shipments WHERE id=1") == [(999,)]


def test_failed_conversion_prevents_all_mutations(case):
    engine, adapter, ledger, value = case
    adapter.execute("UPDATE shipments SET carrier='invalid' WHERE id=3")
    with pytest.raises(EngineError, match="conversion preflight"):
        engine.dry_run(value)
    assert "carrier_v2" not in adapter.schema("shipments")
    assert ledger.get(value.id) is None


def test_policy_failure_never_becomes_complete(case):
    engine, adapter, ledger, _ = case
    value = migration_plan((Check(kind="range", column="carrier_v2", values=(0, 15)),))
    engine.dry_run(value)
    engine.approve(value, value.id, "reviewer")
    with pytest.raises(VerificationFailed):
        engine.apply(value, "operator", maintenance_window=True)
    result = ledger.get(value.id)
    assert result["status"] == "VERIFICATION_FAILED"
    assert result["verification"]["passed"] is False
    assert result["finished_at"] is not None


def test_verification_detects_corruption_after_completion(case):
    engine, adapter, ledger, value = approved(case)
    engine.apply(value, "operator", maintenance_window=True)
    adapter.execute("UPDATE shipments SET carrier_v2=1000 WHERE id=1")
    assert engine.verify(value)["passed"] is False
    assert ledger.get(value.id)["status"] == "VERIFICATION_FAILED"
    assert any(e["detail"].get("status") == "COMPLETE" for e in ledger.events(value.id))


def test_target_identity_is_bound_to_dry_run(case):
    engine, adapter, _, value = approved(case)
    adapter.identity = "another-database"
    with pytest.raises(EngineError, match="target does not match"):
        engine.apply(value, "operator", maintenance_window=True)


def test_forged_plan_cannot_remove_derived_safety_rules(case):
    engine, _, ledger, value = case
    forged = value.model_copy(update={"actions": ()})
    with pytest.raises(ValueError, match="inconsistent derived"):
        engine.dry_run(forged)
    assert ledger.history() == []


def test_driver_failure_evidence_does_not_store_raw_secret(case, monkeypatch):
    engine, adapter, ledger, value = approved(case)

    def fail(*args):
        raise RuntimeError("password=secret source-row=private")

    monkeypatch.setattr(adapter, "backfill", fail)
    with pytest.raises(RuntimeError):
        engine.apply(value, "operator", maintenance_window=True)
    assert "secret" not in str(ledger.events(value.id))
    assert "private" not in str(ledger.get(value.id))
