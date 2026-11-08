"""Independent processes and ledgers must still coordinate on the target."""

import subprocess
import sys
from datetime import date
from pathlib import Path

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

from partitionguard.adapters import open_adapter, quote
from partitionguard.engine import Engine, SourceDriftError
from partitionguard.ledger import Ledger, LedgerError


@pytest.mark.parametrize("release", ["client_kill", "session_loss"])
def test_postgres_controllers_with_separate_ledgers_exclude_and_release(pg_case, release):
    second_path = pg_case.directory / "second.sqlite"
    with open_adapter("postgres", pg_case.dsn, read_only=False) as adapter:
        with Ledger(second_path) as ledger:
            engine = Engine(adapter, ledger)
            engine.dry_run(pg_case.plan)
            engine.approve(pg_case.plan, pg_case.plan.id, "reviewer-2")
            worker, marker = start_worker(pg_case, "lock")
            try:
                evidence = wait_marker(worker, marker)
                with pytest.raises(RuntimeError, match="target lock"):
                    engine.apply(pg_case.plan, "contender", maintenance_window=True)
                assert ledger.get(pg_case.plan.id)["status"] == "APPROVED"
                assert "carrier_v2" not in adapter.schema(pg_case.plan.current.table)
                with pytest.raises(LedgerError, match="another controller"):
                    Ledger(pg_case.ledger)
                if release == "session_loss":
                    adapter.fetch("SELECT pg_terminate_backend(%s)", (evidence["backend_pid"],))
                    marker.with_suffix(marker.suffix + ".continue").write_text("continue")
                    worker.communicate(timeout=15)
                    assert worker.returncode != 0
                else:
                    stop_worker(worker)
                assert (
                    engine.apply(pg_case.plan, "contender", maintenance_window=True)["status"]
                    == "COMPLETE"
                )
                with Ledger(pg_case.ledger) as first:
                    assert first.get(pg_case.plan.id)["status"] != "COMPLETE"
                    assert first.verify_integrity(pg_case.plan.id)
            finally:
                stop_worker(worker)


def test_separate_ledger_cannot_adopt_an_interrupted_migration(pg_case):
    second = pg_case.directory / "second.sqlite"
    with open_adapter("postgres", pg_case.dsn, read_only=False) as adapter:
        with Ledger(second) as ledger:
            engine = Engine(adapter, ledger)
            engine.dry_run(pg_case.plan)
            engine.approve(pg_case.plan, pg_case.plan.id, "reviewer-2")
            worker, marker = start_worker(pg_case, "before_checkpoint")
            try:
                wait_marker(worker, marker)
                stop_worker(worker)
                with pytest.raises(Exception, match="already exist"):
                    engine.apply(pg_case.plan, "contender", maintenance_window=True)
                assert ledger.get(pg_case.plan.id)["status"] != "COMPLETE"
            finally:
                stop_worker(worker)
    resume_and_assert(pg_case)


@pytest.mark.parametrize("row", [1, 2, 4])
def test_external_writer_during_execution_is_not_hidden(pg_case, row):
    worker, marker = start_worker(pg_case, "after_checkpoint")
    try:
        wait_marker(worker, marker)
        with open_adapter("postgres", pg_case.dsn, read_only=False) as writer:
            writer.execute(
                f"UPDATE {quote(pg_case.plan.current.table)} SET carrier='99' WHERE id=%s", (row,)
            )
        marker.with_suffix(marker.suffix + ".continue").write_text("continue")
        worker.communicate(timeout=15)
        with Ledger(pg_case.ledger) as ledger:
            run = ledger.get(pg_case.plan.id)
            assert run["status"] == (
                "COMPLETE" if row == 4 else "FAILED" if row == 2 else "VERIFICATION_FAILED"
            )
            assert run["baseline"]["partitions"]["2026-01-02"]["row_count"] == 1
        if row == 2:
            with open_adapter("postgres", pg_case.dsn) as adapter:
                assert adapter.fetch(
                    f"SELECT carrier_v2 FROM {quote(pg_case.plan.current.table)} WHERE id=2"
                ) == [(None,)]
    finally:
        stop_worker(worker)


def test_source_drift_while_controller_is_down_blocks_recovery(pg_case):
    worker, marker = start_worker(pg_case, "after_checkpoint")
    try:
        wait_marker(worker, marker)
        stop_worker(worker)
        with open_adapter("postgres", pg_case.dsn, read_only=False) as writer:
            writer.execute(
                f"UPDATE {quote(pg_case.plan.current.table)} SET carrier='99' WHERE id=2"
            )
            with Ledger(pg_case.ledger) as ledger:
                original = ledger.get(pg_case.plan.id)["baseline"]
                with pytest.raises(SourceDriftError):
                    Engine(writer, ledger).apply(
                        pg_case.plan, "recovery", resume=True, maintenance_window=True
                    )
                assert ledger.get(pg_case.plan.id)["baseline"] == original
            writer.execute(
                f"UPDATE {quote(pg_case.plan.current.table)} SET carrier='20' WHERE id=2"
            )
        resume_and_assert(pg_case)
    finally:
        stop_worker(worker)


@pytest.mark.parametrize("backend", ["duckdb", "delta"])
def test_local_target_locks_exclude_other_processes_and_release(tmp_path, backend):
    target = tmp_path / ("table.duckdb" if backend == "duckdb" else "delta-table")
    if backend == "duckdb":
        import duckdb

        with duckdb.connect(str(target)) as connection:
            connection.execute("CREATE TABLE events(id INTEGER, day DATE)")
            connection.execute("INSERT INTO events VALUES (1, '2026-01-01')")
    else:
        import deltalake
        import pyarrow as pa

        deltalake.write_deltalake(
            target,
            pa.table({"id": [1], "day": pa.array([date(2026, 1, 1)], pa.date32())}),
            partition_by=["day"],
        )
    marker = tmp_path / "ready"
    worker = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).with_name("target_lock_worker.py")),
            backend,
            str(target),
            str(tmp_path / "first.sqlite"),
            str(marker),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        wait_marker(worker, marker)
        with Ledger(tmp_path / "second.sqlite"):
            with pytest.raises(Exception, match="(?i)lock"):
                with open_adapter(backend, str(target), read_only=False) as adapter:
                    with adapter.lock("events"):
                        pytest.fail("Another process acquired the held target lock")
            stop_worker(worker)
            with open_adapter(backend, str(target), read_only=False) as adapter:
                with adapter.lock("events"):
                    assert adapter.schema("events")["day"] == "DATE"
    finally:
        stop_worker(worker)
