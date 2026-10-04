import sqlite3

import pytest

from partitionguard.ledger import Ledger, LedgerError, LedgerIntegrityError


def prepare(ledger):
    return ledger.prepare("a" * 64, {"product": "orders"}, {"count": 4}, "database-identity")


def test_evidence_survives_reopen_and_preserves_failed_attempts(tmp_path):
    path = tmp_path / "ledger.sqlite"
    with Ledger(path) as ledger:
        run = prepare(ledger)
        migration = run["plan_id"]
        ledger.approve(migration, "alice")
        ledger.transition(migration, "RUNNING", operator="bob")
        ledger.checkpoint(migration, "partition:1", {"count": 2})
        ledger.transition(migration, "FAILED", error="Injected failure")
    with Ledger(path) as ledger:
        failed = ledger.get(migration)
        assert failed["status"] == "FAILED"
        assert failed["approved_digest"] == migration
        assert failed["checkpoints"]["partition:1"]["detail"] == {"count": 2}
        ledger.transition(migration, "RUNNING", operator="bob")
        ledger.transition(migration, "VERIFYING")
        ledger.transition(
            migration, "COMPLETE", verification={"passed": True, "checks": ["parity"]}
        )
        assert ledger.get(migration)["attempts"] == 2
        assert ledger.verify_integrity(migration)
        assert any(e["detail"].get("status") == "FAILED" for e in ledger.events(migration))
        assert len(ledger.history()) == 1


def test_cannot_replace_prepared_baseline_or_target(tmp_path):
    with Ledger(tmp_path / "ledger.sqlite") as ledger:
        run = prepare(ledger)
        for baseline, target in [({"count": 5}, "database-identity"), ({"count": 4}, "other")]:
            with pytest.raises(LedgerError, match="cannot be replaced"):
                ledger.prepare(run["plan_id"], run["plan"], baseline, target)
        assert ledger.get(run["plan_id"])["baseline"] == {"count": 4}


def test_single_writer_lock_and_release(tmp_path):
    path = tmp_path / "ledger.sqlite"
    with Ledger(path):
        with pytest.raises(LedgerError, match="another controller"):
            Ledger(path)
    with Ledger(path) as ledger:
        assert ledger.history() == []


def test_events_are_append_only_and_run_projection_is_verified(tmp_path):
    with Ledger(tmp_path / "ledger.sqlite") as ledger:
        run = prepare(ledger)
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            ledger.connection.execute("UPDATE events SET detail='{}'")
        ledger.connection.rollback()
        with ledger.connection:
            ledger.connection.execute("UPDATE runs SET baseline='{}'")
        with pytest.raises(LedgerIntegrityError, match="does not match"):
            ledger.get(run["plan_id"])


def test_hash_chain_detects_event_tampering(tmp_path):
    with Ledger(tmp_path / "ledger.sqlite") as ledger:
        run = prepare(ledger)
        with ledger.connection:
            ledger.connection.execute("DROP TRIGGER events_no_update")
            ledger.connection.execute("UPDATE events SET timestamp='edited'")
        with pytest.raises(LedgerIntegrityError, match="hash chain"):
            ledger.get(run["plan_id"])


def test_completion_requires_valid_transition_and_evidence(tmp_path):
    with Ledger(tmp_path / "ledger.sqlite") as ledger:
        migration = prepare(ledger)["plan_id"]
        with pytest.raises(LedgerError, match="Invalid execution transition"):
            ledger.transition(migration, "COMPLETE", verification={"passed": True})
        ledger.approve(migration, "alice")
        ledger.transition(migration, "RUNNING")
        ledger.transition(migration, "VERIFYING")
        with pytest.raises(LedgerError, match="passing verification"):
            ledger.transition(migration, "COMPLETE", verification={"passed": False})
        assert ledger.get(migration)["status"] == "VERIFYING"


def test_generic_event_cannot_forge_lifecycle_evidence(tmp_path):
    with Ledger(tmp_path / "ledger.sqlite") as ledger:
        migration = prepare(ledger)["plan_id"]
        with pytest.raises(LedgerError, match="dedicated"):
            ledger.record_event(migration, "custom", {"status": "COMPLETE"})
        with pytest.raises(LedgerError, match="dedicated"):
            ledger.record_event(migration, "APPROVED", {})


def test_verification_projection_cannot_be_rewritten(tmp_path):
    with Ledger(tmp_path / "ledger.sqlite") as ledger:
        migration = prepare(ledger)["plan_id"]
        ledger.approve(migration, "alice")
        ledger.transition(migration, "RUNNING")
        ledger.transition(migration, "VERIFICATION_FAILED", verification={"passed": False})
        with ledger.connection:
            ledger.connection.execute("UPDATE runs SET verification=?", ('{"passed":true}',))
        with pytest.raises(LedgerIntegrityError, match="does not match"):
            ledger.get(migration)


@pytest.mark.parametrize(
    "field", ["run_id", "created_at", "updated_at", "started_at", "finished_at"]
)
def test_run_identity_and_timestamp_projections_are_bound_to_events(tmp_path, field):
    with Ledger(tmp_path / "ledger.sqlite") as ledger:
        migration = prepare(ledger)["plan_id"]
        ledger.approve(migration, "alice")
        ledger.transition(migration, "RUNNING")
        ledger.transition(migration, "VERIFYING")
        ledger.transition(migration, "COMPLETE", verification={"passed": True})
        assert ledger.get(migration)[field] is not None
        with ledger.connection:
            ledger.connection.execute(f"UPDATE runs SET {field}=?", ("tampered",))
        with pytest.raises(LedgerIntegrityError, match="does not match"):
            ledger.get(migration)


def test_checkpoint_completion_time_is_bound_to_event(tmp_path):
    with Ledger(tmp_path / "ledger.sqlite") as ledger:
        migration = prepare(ledger)["plan_id"]
        ledger.approve(migration, "alice")
        ledger.transition(migration, "RUNNING")
        ledger.checkpoint(migration, "partition:1", {"count": 2})
        with ledger.connection:
            ledger.connection.execute("UPDATE checkpoints SET completed_at='tampered'")
        with pytest.raises(LedgerIntegrityError, match="Checkpoints do not match"):
            ledger.get(migration)


def test_projection_timestamps_match_event_clock_through_recovery(tmp_path, monkeypatch):
    from partitionguard import ledger as ledger_module

    times = iter(f"2026-10-04T20:00:{index:02d}+00:00" for index in range(30))
    monkeypatch.setattr(ledger_module, "_now", lambda: next(times))
    with Ledger(tmp_path / "ledger.sqlite") as ledger:
        prepared = prepare(ledger)
        migration = prepared["plan_id"]
        approved = ledger.approve(migration, "alice")
        running = ledger.transition(migration, "RUNNING")
        ledger.checkpoint(migration, "partition:1", {"count": 2})
        failed = ledger.transition(migration, "FAILED", error="interrupted")
        resumed = ledger.transition(migration, "RUNNING")
        ledger.checkpoint(migration, "partition:1", {"count": 2, "reconciled": True})
        ledger.transition(migration, "VERIFYING")
        complete = ledger.transition(migration, "COMPLETE", verification={"passed": True})
        ledger.record_event(migration, "EXPORTED", {"format": "json"})
        events = ledger.events(migration)
        assert all(event["detail"]["run_id"] == prepared["run_id"] for event in events)
        assert prepared["created_at"] == prepared["updated_at"] == events[0]["timestamp"]
        assert approved["updated_at"] == events[1]["timestamp"]
        assert running["started_at"] == events[2]["timestamp"]
        assert failed["finished_at"] == events[4]["timestamp"]
        assert resumed["started_at"] == running["started_at"]
        assert resumed["finished_at"] is None
        assert complete["finished_at"] == complete["updated_at"] == events[8]["timestamp"]
        assert complete["checkpoints"]["partition:1"]["completed_at"] == events[6]["timestamp"]
        assert ledger.get(migration)["updated_at"] == complete["updated_at"]
        assert ledger.verify_integrity(migration)
