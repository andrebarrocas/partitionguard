import json
import signal
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest
from infrastructure import (
    owned_postgres as owned_postgres,
)
from infrastructure import (
    pg_case as pg_case,
)
from infrastructure import (
    resume_and_assert,
    start_worker,
    stop_worker,
    wait_marker,
)
from test_cli import demo as demo
from test_cli import invoke, make_plan
from test_engine import case as case
from test_engine import migration_plan

from partitionguard.adapters import open_adapter
from partitionguard.controls import PauseRequest
from partitionguard.engine import Engine, EngineError, VerificationFailed
from partitionguard.ledger import Ledger, LedgerError, LedgerIntegrityError
from partitionguard.models import Check, ExecutionPolicy, MigrationSpec, canonical_json
from partitionguard.planning import load_plan, save_plan
from partitionguard.planning import plan as build_plan
from partitionguard.reporting import build_report


def controlled(case, **options):
    engine, adapter, ledger, old = case
    policy = ExecutionPolicy(**options)
    value = build_plan(
        old.current,
        old.proposed,
        old.adapter,
        old.migration.model_copy(update={"execution": policy}),
    )
    engine.dry_run(value)
    engine.approve(value, value.id, "reviewer")
    return engine, adapter, ledger, value


def test_canary_requires_durable_exact_plan_continuation(case):
    engine, adapter, ledger, plan = controlled(case, canary_partitions=1)
    paused = engine.apply(plan, "operator", maintenance_window=True)
    assert paused["status"] == "CANARY_PAUSED"
    assert paused["verification"]["scope"] == "canary"
    assert paused["verification"]["passed"] is True
    assert len(paused["checkpoints"]) == 2
    assert adapter.fetch("SELECT carrier_v2 FROM shipments ORDER BY id") == [
        (10,),
        (None,),
        (None,),
        (None,),
    ]
    evidence = build_report(plan, ledger)
    assert evidence["latest_recorded_verification"] is None
    assert evidence["canary_verification"]["passed"] is True
    with pytest.raises(EngineError, match="continuation approval"):
        engine.apply(plan, "operator", resume=True, maintenance_window=True)
    with pytest.raises(EngineError, match="full plan digest"):
        engine.approve_continuation(plan, plan.id[:12], "reviewer")
    engine.approve_continuation(plan, plan.id, "reviewer-2")
    assert ledger.get(plan.id)["control"]["continuation_approved"] is True
    path = ledger.path
    ledger.close()
    with Ledger(path) as reopened:
        engine = Engine(adapter, reopened)
        with pytest.raises(EngineError, match="explicit resume"):
            engine.apply(plan, "operator", maintenance_window=True)
        complete = engine.apply(plan, "operator", resume=True, maintenance_window=True)
        assert complete["status"] == "COMPLETE" and complete["verification"]["passed"] is True
        assert complete["baseline"] == paused["baseline"]
        assert reopened.verify_integrity(plan.id)


def test_canary_corruption_invalidates_review_and_requires_new_approval(case):
    engine, adapter, ledger, plan = controlled(case, canary_partitions=1)
    first = engine.apply(plan, "operator", maintenance_window=True)
    adapter.execute("UPDATE shipments SET carrier_v2=999 WHERE id=1")
    with pytest.raises(VerificationFailed, match="Canary verification failed"):
        engine.approve_continuation(plan, plan.id, "reviewer")
    assert ledger.get(plan.id)["status"] == "VERIFICATION_FAILED"
    repaired = engine.apply(plan, "operator", resume=True, maintenance_window=True)
    assert repaired["status"] == "CANARY_PAUSED"
    assert repaired["control"]["canary_review_hash"] != first["control"]["canary_review_hash"]
    assert repaired["control"]["continuation_approved"] is False
    assert adapter.fetch("SELECT carrier_v2 FROM shipments WHERE id=2") == [(None,)]


def test_failed_canary_policy_cannot_reach_later_partitions(case):
    engine, adapter, ledger, old = case
    migration = old.migration.model_copy(
        update={
            "execution": ExecutionPolicy(
                canary_partitions=1,
                canary_checks=(Check(kind="sql", sql="SELECT COUNT(*) < 0 FROM {scope}"),),
            ),
        }
    )
    plan = build_plan(old.current, old.proposed, "duckdb", migration)
    engine.dry_run(plan)
    engine.approve(plan, plan.id, "reviewer")
    with pytest.raises(VerificationFailed, match="canary"):
        engine.apply(plan, "operator", maintenance_window=True)
    assert ledger.get(plan.id)["status"] == "VERIFICATION_FAILED"
    assert adapter.fetch("SELECT carrier_v2 FROM shipments WHERE id=2") == [(None,)]


@pytest.mark.parametrize("canary", [None, 1])
def test_duration_budget_pauses_after_commit_and_can_only_be_tightened(case, monkeypatch, canary):
    engine, adapter, ledger, plan = controlled(
        case, max_duration_seconds=1, canary_partitions=canary
    )
    clock = [0.0]
    monkeypatch.setattr("partitionguard.engine.time.monotonic", lambda: clock[0])
    backfill = adapter.backfill

    def slow(*args):
        result = backfill(*args)
        clock[0] += 2
        return result

    monkeypatch.setattr(adapter, "backfill", slow)
    paused = engine.apply(plan, "operator", maintenance_window=True, max_duration_seconds=100)
    assert paused["status"] == "PAUSED"
    assert paused["control"]["last_pause"]["reason"] == "duration_budget"
    assert len(paused["checkpoints"]) == 2
    starts = [e for e in ledger.events(plan.id) if e["kind"] == "EXECUTION_STARTED"]
    assert starts[-1]["detail"]["max_duration_seconds"] == 1
    monkeypatch.setattr(adapter, "backfill", backfill)
    if canary:
        assert (
            engine.apply(plan, "operator", resume=True, maintenance_window=True)["status"]
            == "CANARY_PAUSED"
        )
        engine.approve_continuation(plan, plan.id, "reviewer")
    assert (
        engine.apply(plan, "operator", resume=True, maintenance_window=True)["status"] == "COMPLETE"
    )


def test_manual_pause_request_is_durable_without_ledger_lock_and_survives_ack_gap(
    case, monkeypatch
):
    engine, _, ledger, plan = controlled(case)
    request = PauseRequest(ledger.path, plan.id)
    queued = request.create("operator-2")
    with pytest.raises(ValueError, match="already pending"):
        request.create("operator-3")
    acknowledge = PauseRequest.acknowledge
    monkeypatch.setattr(PauseRequest, "acknowledge", lambda *args: None)
    paused = engine.apply(plan, "operator", maintenance_window=True)
    assert paused["status"] == "PAUSED" and not paused["checkpoints"]
    assert paused["control"]["last_pause"]["token"] == queued["token"]
    assert request.path.exists()
    monkeypatch.setattr(PauseRequest, "acknowledge", acknowledge)
    assert (
        engine.apply(plan, "operator", resume=True, maintenance_window=True)["status"] == "COMPLETE"
    )
    assert not request.path.exists()


@pytest.mark.parametrize("seconds", [0, -1, True, 604801])
def test_invalid_duration_cannot_mutate_target(case, seconds):
    engine, adapter, _, plan = controlled(case)
    with pytest.raises(EngineError, match="integer seconds"):
        engine.apply(plan, "operator", maintenance_window=True, max_duration_seconds=seconds)
    assert "carrier_v2" not in adapter.schema("shipments")


def test_legacy_plan_digest_and_public_cli_execution_remain_compatible(demo):
    folder, database = demo
    legacy = Path(__file__).with_name("fixtures") / "legacy-plan.json"
    value = load_plan(legacy)
    raw = json.loads(legacy.read_text())
    assert value.id == raw["plan_id"]
    assert "execution" not in value.migration.model_dump(mode="json")
    assert canonical_json(value) == canonical_json(raw["plan"])
    args = [legacy, "--ledger", folder / "legacy.sqlite", "--connection", database, "--json"]
    invoke(["migrate", *args])
    invoke(["approve", *args, "--digest", value.id, "--operator", "reviewer"])
    assert (
        json.loads(
            invoke(
                ["migrate", *args, "--execute", "--maintenance-window", "--operator", "operator"]
            ).stdout
        )["status"]
        == "COMPLETE"
    )


def test_cli_canary_pause_approval_and_continuation(demo):
    folder, database = demo
    path, _ = make_plan(folder)
    old = load_plan(path)
    plan = build_plan(
        old.current,
        old.proposed,
        "duckdb",
        old.migration.model_copy(update={"execution": ExecutionPolicy(canary_partitions=1)}),
    )
    save_plan(plan, path)
    args = [path, "--connection", database, "--ledger", folder / "controlled.sqlite", "--json"]
    invoke(["migrate", *args])
    invoke(["approve", *args, "--digest", plan.id, "--operator", "reviewer"])
    execution = ["migrate", *args, "--execute", "--maintenance-window", "--operator", "operator"]
    assert json.loads(invoke(execution, 7).stdout)["status"] == "CANARY_PAUSED"
    invoke(["approve-continuation", *args, "--digest", plan.id, "--operator", "reviewer"])
    # No connection or ledger opening is required to queue the request.
    invoke(["pause", path, "--ledger", folder / "controlled.sqlite", "--operator", "operator"])
    assert json.loads(invoke([*execution, "--resume"], 7).stdout)["status"] == "PAUSED"
    assert json.loads(invoke([*execution, "--resume"]).stdout)["status"] == "COMPLETE"


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT, "pause_request"])
def test_cli_signal_mid_transaction_finishes_action_then_pauses(tmp_path, signum):
    database = tmp_path / "data.duckdb"
    path = tmp_path / "plan.json"
    ledger_file = tmp_path / "ledger.sqlite"
    marker = tmp_path / "ready"
    plan = migration_plan()
    save_plan(plan, path)
    with duckdb.connect(str(database)) as connection:
        connection.execute("CREATE TABLE shipments(id INTEGER, day DATE, carrier VARCHAR)")
        connection.execute(
            "INSERT INTO shipments VALUES (1,'2026-01-01','10'),(2,'2026-01-02','20'),(3,'2026-01-03','30')"
        )
    with open_adapter("duckdb", str(database), read_only=False) as adapter:
        with Ledger(ledger_file) as ledger:
            engine = Engine(adapter, ledger)
            engine.dry_run(plan)
            engine.approve(plan, plan.id, "reviewer")
    worker = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).with_name("control_worker.py")),
            str(path),
            str(database),
            str(ledger_file),
            str(marker),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        wait_marker(worker, marker)
        if signum == "pause_request":
            invoke(["pause", path, "--ledger", ledger_file, "--operator", "other-controller"])
        else:
            worker.send_signal(signum)
        marker.with_suffix(".continue").write_text("continue")
        stdout, stderr = worker.communicate(timeout=15)
        assert worker.returncode == 7, stderr
        assert json.loads(stdout)["status"] == "PAUSED"
    finally:
        stop_worker(worker)
    with open_adapter("duckdb", str(database), read_only=False) as adapter:
        with Ledger(ledger_file) as ledger:
            assert len(ledger.get(plan.id)["checkpoints"]) == 2
            assert adapter.fetch("SELECT carrier_v2 FROM shipments ORDER BY id") == [
                (10,),
                (None,),
                (None,),
            ]
            assert (
                Engine(adapter, ledger).apply(
                    plan, "operator", resume=True, maintenance_window=True
                )["status"]
                == "COMPLETE"
            )


def test_postgres_configured_timeouts_are_applied_and_real_timeouts_do_not_commit(pg_case):
    policy = ExecutionPolicy(statement_timeout_seconds=1, lock_timeout_seconds=1)
    old = pg_case.plan
    plan = build_plan(
        old.current,
        old.proposed,
        "postgres",
        old.migration.model_copy(update={"execution": policy}),
    )
    with open_adapter("postgres", pg_case.dsn, read_only=False) as adapter:
        with Ledger(pg_case.directory / "timeouts.sqlite") as ledger:
            engine = Engine(adapter, ledger)
            engine.dry_run(plan)
            assert adapter.fetch(
                "SELECT current_setting('statement_timeout'),current_setting('lock_timeout')"
            ) == [("1s", "1s")]
            engine.approve(plan, plan.id, "reviewer")
            actual = adapter.backfill

            def slow(*args):
                adapter._connection.execute("SELECT pg_sleep(2)")
                return actual(*args)

            adapter.backfill = slow
            with pytest.raises(Exception):
                engine.apply(plan, "operator", maintenance_window=True)
            assert ledger.get(plan.id)["status"] == "FAILED"
            assert not any(
                key.startswith("backfill:") for key in ledger.get(plan.id)["checkpoints"]
            )
            adapter.backfill = actual
            assert (
                engine.apply(plan, "operator", resume=True, maintenance_window=True)["status"]
                == "COMPLETE"
            )


def test_invalid_or_unsupported_policy_is_rejected_before_execution(case):
    _, _, _, old = case
    with pytest.raises(ValueError, match="Canary exceeds"):
        MigrationSpec.model_validate(
            {**old.migration.model_dump(mode="json"), "execution": {"canary_partitions": 4}}
        )
    with pytest.raises(ValueError):
        ExecutionPolicy(canary_partitions=True)
    plan = build_plan(
        old.current,
        old.proposed,
        "duckdb",
        old.migration.model_copy(
            update={"execution": ExecutionPolicy(statement_timeout_seconds=10)}
        ),
    )
    assert any("timeouts" in blocker for blocker in plan.blockers)


def test_continuation_receipt_cannot_be_rewritten(case):
    engine, _, ledger, plan = controlled(case, canary_partitions=1)
    engine.apply(plan, "operator", maintenance_window=True)
    with pytest.raises(LedgerError):
        ledger.record_event(plan.id, "STATE", {"status": "CONTINUATION_APPROVED"})
    engine.approve_continuation(plan, plan.id, "reviewer")
    ledger.connection.execute("UPDATE runs SET status='COMPLETE'")
    ledger.connection.commit()
    with pytest.raises(LedgerIntegrityError):
        ledger.get(plan.id)


def test_canary_evidence_cannot_be_used_as_full_completion_evidence(case):
    engine, _, ledger, plan = controlled(case, canary_partitions=1)
    paused = engine.apply(plan, "operator", maintenance_window=True)
    engine.approve_continuation(plan, plan.id, "reviewer")
    ledger.transition(plan.id, "RUNNING", operator="operator")
    ledger.transition(plan.id, "VERIFYING", operator="operator")
    with pytest.raises(LedgerError, match="Completion requires"):
        ledger.transition(plan.id, "COMPLETE", verification=paused["verification"])
    assert ledger.get(plan.id)["status"] == "VERIFYING"


def test_full_policy_remains_mandatory_after_passing_canary(case):
    engine, _, ledger, old = case
    recipe = old.migration.model_copy(
        update={
            "execution": ExecutionPolicy(canary_partitions=1),
            "checks": (Check(kind="sql", sql="SELECT COUNT(*) < 0 FROM {scope}"),),
        }
    )
    plan = build_plan(old.current, old.proposed, "duckdb", recipe)
    engine.dry_run(plan)
    engine.approve(plan, plan.id, "reviewer")
    assert engine.apply(plan, "operator", maintenance_window=True)["status"] == "CANARY_PAUSED"
    engine.approve_continuation(plan, plan.id, "reviewer")
    with pytest.raises(VerificationFailed, match="final verification"):
        engine.apply(plan, "operator", resume=True, maintenance_window=True)
    assert ledger.get(plan.id)["status"] == "VERIFICATION_FAILED"


def test_changing_execution_policy_invalidates_earlier_approval(case):
    engine, adapter, _, plan = controlled(case, canary_partitions=1)
    changed = build_plan(
        plan.current,
        plan.proposed,
        plan.adapter,
        plan.migration.model_copy(update={"execution": ExecutionPolicy(canary_partitions=2)}),
    )
    assert changed.id != plan.id
    with pytest.raises(EngineError, match="dry-run"):
        engine.apply(changed, "operator", maintenance_window=True)
    assert "carrier_v2" not in adapter.schema("shipments")


def test_crash_after_canary_checkpoint_cannot_skip_review(pg_case):
    old = pg_case.plan
    pg_case.plan = build_plan(
        old.current,
        old.proposed,
        "postgres",
        old.migration.model_copy(update={"execution": ExecutionPolicy(canary_partitions=1)}),
    )
    pg_case.ledger = pg_case.directory / "canary.sqlite"
    save_plan(pg_case.plan, pg_case.file)
    with open_adapter("postgres", pg_case.dsn, read_only=False) as adapter:
        with Ledger(pg_case.ledger) as ledger:
            engine = Engine(adapter, ledger)
            engine.dry_run(pg_case.plan)
            engine.approve(pg_case.plan, pg_case.plan.id, "reviewer")
    worker, marker = start_worker(pg_case, "after_checkpoint")
    try:
        wait_marker(worker, marker)
        stop_worker(worker)
        with open_adapter("postgres", pg_case.dsn, read_only=False) as adapter:
            with Ledger(pg_case.ledger) as ledger:
                engine = Engine(adapter, ledger)
                assert (
                    engine.apply(pg_case.plan, "recovery", resume=True, maintenance_window=True)[
                        "status"
                    ]
                    == "CANARY_PAUSED"
                )
                with pytest.raises(EngineError, match="continuation approval"):
                    engine.apply(pg_case.plan, "recovery", resume=True, maintenance_window=True)
                engine.approve_continuation(pg_case.plan, pg_case.plan.id, "reviewer")
                assert (
                    engine.apply(pg_case.plan, "recovery", resume=True, maintenance_window=True)[
                        "status"
                    ]
                    == "COMPLETE"
                )
    finally:
        stop_worker(worker)


@pytest.mark.parametrize("malformation", ["json", "wrong_plan", "oversized", "symlink", "fifo"])
def test_invalid_pause_request_cannot_trigger_writes(case, malformation):
    engine, adapter, ledger, plan = controlled(case)
    request = PauseRequest(ledger.path, plan.id)
    if malformation == "symlink":
        request.path.symlink_to(ledger.path)
    elif malformation == "fifo":
        import os

        os.mkfifo(request.path)
    else:
        request.path.write_text(
            "{"
            if malformation == "json"
            else "x" * 4097
            if malformation == "oversized"
            else '{"plan_id":"other"}'
        )
    with pytest.raises((ValueError, OSError)):
        engine.apply(plan, "operator", maintenance_window=True)
    assert ledger.get(plan.id)["status"] == "FAILED"
    assert "carrier_v2" not in adapter.schema("shipments")


def test_expired_budget_after_verification_does_not_mark_complete(case, monkeypatch):
    import partitionguard.engine as module

    engine, _, ledger, plan = controlled(case, max_duration_seconds=1)
    clock = [0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    original = module.verify

    def slow(*args):
        result = original(*args)
        clock[0] += 2
        return result

    monkeypatch.setattr(module, "verify", slow)
    assert engine.apply(plan, "operator", maintenance_window=True)["status"] == "PAUSED"
    assert len(ledger.get(plan.id)["checkpoints"]) == 4
    monkeypatch.setattr(module, "verify", original)
    assert (
        engine.apply(plan, "operator", resume=True, maintenance_window=True)["status"] == "COMPLETE"
    )


def test_postgres_real_lock_timeout_fails_closed_then_recovers(pg_case):
    from partitionguard.adapters import quote

    old = pg_case.plan
    plan = build_plan(
        old.current,
        old.proposed,
        "postgres",
        old.migration.model_copy(
            update={
                "execution": ExecutionPolicy(statement_timeout_seconds=3, lock_timeout_seconds=1)
            }
        ),
    )
    with open_adapter("postgres", pg_case.dsn, read_only=False) as adapter:
        with Ledger(pg_case.directory / "lock-timeout.sqlite") as ledger:
            engine = Engine(adapter, ledger)
            engine.dry_run(plan)
            engine.approve(plan, plan.id, "reviewer")
            with open_adapter("postgres", pg_case.dsn, read_only=False) as blocker:
                blocker.execute("BEGIN")
                try:
                    blocker.execute(
                        f"LOCK TABLE {quote(plan.current.table)} IN ACCESS EXCLUSIVE MODE"
                    )
                    with pytest.raises(Exception) as failure:
                        engine.apply(plan, "operator", maintenance_window=True)
                    assert failure.value.sqlstate == "55P03"
                finally:
                    blocker.execute("ROLLBACK")
            assert ledger.get(plan.id)["status"] == "FAILED"
            assert "carrier_v2" not in adapter.schema(plan.current.table)
            assert (
                engine.apply(plan, "operator", resume=True, maintenance_window=True)["status"]
                == "COMPLETE"
            )


def test_report_does_not_export_sql_from_canary_policy(case):
    engine, _, ledger, plan = controlled(
        case,
        canary_partitions=1,
        canary_checks=(
            Check(
                kind="sql",
                sql="SELECT COUNT(*) > 0 AND 'private-SQL-text' = 'private-SQL-text' FROM {scope}",
            ),
        ),
    )
    report = canonical_json(build_report(plan, ledger))
    assert "private-SQL-text" not in report
    assert "SELECT COUNT" not in report


def test_delta_canary_survives_reopen_and_requires_continuation(tmp_path):
    from datetime import date

    import deltalake
    import pyarrow as pa

    target = tmp_path / "delta"
    deltalake.write_deltalake(
        target,
        pa.table(
            {
                "id": pa.array([1, 2, 3], pa.int32()),
                "day": pa.array([date(2026, 1, d) for d in (1, 2, 3)], pa.date32()),
                "carrier": ["10", "20", "30"],
            }
        ),
        partition_by=["day"],
    )
    old = migration_plan(adapter="delta")
    plan = build_plan(
        old.current,
        old.proposed,
        "delta",
        old.migration.model_copy(update={"execution": ExecutionPolicy(canary_partitions=1)}),
    )
    ledger_file = tmp_path / "ledger.sqlite"
    with open_adapter("delta", str(target), read_only=False) as adapter:
        with Ledger(ledger_file) as ledger:
            engine = Engine(adapter, ledger)
            engine.dry_run(plan)
            engine.approve(plan, plan.id, "reviewer")
            assert (
                engine.apply(plan, "operator", maintenance_window=True)["status"] == "CANARY_PAUSED"
            )
    with open_adapter("delta", str(target)) as adapter:
        with Ledger(ledger_file) as ledger:
            Engine(adapter, ledger).approve_continuation(plan, plan.id, "reviewer")
    with open_adapter("delta", str(target), read_only=False) as adapter:
        with Ledger(ledger_file) as ledger:
            assert (
                Engine(adapter, ledger).apply(
                    plan, "operator", resume=True, maintenance_window=True
                )["status"]
                == "COMPLETE"
            )


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
def test_postgres_cli_signal_pauses_only_after_committed_checkpoint(pg_case, signum):
    marker = pg_case.directory / "signal.ready"
    worker = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).with_name("control_worker.py")),
            str(pg_case.file),
            pg_case.dsn,
            str(pg_case.ledger),
            str(marker),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        wait_marker(worker, marker)
        worker.send_signal(signum)
        marker.with_suffix(marker.suffix + ".continue").write_text("continue")
        stdout, stderr = worker.communicate(timeout=15)
        assert worker.returncode == 7, stderr
        assert json.loads(stdout)["status"] == "PAUSED"
        with Ledger(pg_case.ledger) as ledger:
            assert len(ledger.get(pg_case.plan.id)["checkpoints"]) == 2
        resume_and_assert(pg_case)
    finally:
        stop_worker(worker)
