"""Real owned-server failures, lost COMMIT acknowledgements, and SQLite limits."""

import select
import socket
import sqlite3
import threading

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
from test_engine import approved
from test_engine import case as case

from partitionguard.adapters import open_adapter, quote
from partitionguard.engine import Engine, EngineError
from partitionguard.ledger import Ledger


@pytest.mark.parametrize("barrier", ["inside_transaction", "before_checkpoint", "after_checkpoint"])
@pytest.mark.parametrize("failure", ["client_kill", "terminate_session", "server_restart"])
def test_postgres_actual_failure_then_recovery(pg_case, barrier, failure):
    worker, marker = start_worker(pg_case, barrier)
    try:
        evidence = wait_marker(worker, marker)
        if failure == "client_kill":
            stop_worker(worker)
            assert worker.returncode == -9
        else:
            if failure == "terminate_session":
                with open_adapter("postgres", pg_case.dsn, read_only=False) as adapter:
                    assert adapter.fetch(
                        "SELECT pg_terminate_backend(%s)", (evidence["backend_pid"],)
                    ) == [(True,)]
            else:
                pg_case.server.restart()
            marker.with_suffix(marker.suffix + ".continue").write_text("continue")
            worker.communicate(timeout=15)
            assert worker.returncode != 0
    finally:
        stop_worker(worker)
    with Ledger(pg_case.ledger) as ledger:
        run = ledger.get(pg_case.plan.id)
        assert run["status"] != "COMPLETE"
        assert ("backfill:carrier_v2:2026-01-01" in run["checkpoints"]) == (
            barrier == "after_checkpoint"
            or (barrier == "before_checkpoint" and failure != "client_kill")
        )
    with open_adapter("postgres", pg_case.dsn) as adapter:
        expected = None if barrier == "inside_transaction" else 10
        assert adapter.fetch(
            f"SELECT carrier_v2 FROM {quote(pg_case.plan.current.table)} WHERE id=1"
        ) == [(expected,)]
    resume_and_assert(pg_case)


def test_committed_transaction_with_lost_commit_response(pg_case):
    """A plaintext local proxy forwards COMMIT but discards its server reply."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(15)
    proxy_port = listener.getsockname()[1]
    committed = threading.Event()
    failures = []

    def proxy():
        try:
            client, _ = listener.accept()
            with (
                client,
                socket.create_connection(("127.0.0.1", pg_case.server.port), timeout=10) as server,
            ):
                frame = b"Q\x00\x00\x00\x0bCOMMIT\x00"
                tail = b""
                commits = 0
                dropping = False
                response = b""
                while True:
                    readable, _, _ = select.select([client, server], [], [], 15)
                    if not readable:
                        raise TimeoutError("Proxy timed out")
                    for source in readable:
                        data = source.recv(65536)
                        if not data:
                            return
                        if source is client:
                            combined = tail + data
                            commits += combined.count(frame)
                            tail = combined[-(len(frame) - 1) :]
                            server.sendall(data)
                            # First commit adds the column, second backfills a partition.
                            dropping = commits >= 2
                        elif dropping:
                            response += data
                            if b"Z\x00\x00\x00\x05I" in response:
                                committed.set()
                                return  # Close both sockets without acknowledging COMMIT.
                        else:
                            client.sendall(data)
        except Exception as exc:
            failures.append(type(exc).__name__)
        finally:
            listener.close()

    thread = threading.Thread(target=proxy, daemon=True)
    thread.start()
    proxy_dsn = pg_case.dsn.replace(f"port={pg_case.server.port}", f"port={proxy_port}")
    with Ledger(pg_case.ledger) as ledger:
        with open_adapter("postgres", proxy_dsn, read_only=False) as adapter:
            with pytest.raises(Exception):
                Engine(adapter, ledger).apply(pg_case.plan, "operator", maintenance_window=True)
        assert ledger.get(pg_case.plan.id)["status"] == "FAILED"
        assert "backfill:carrier_v2:2026-01-01" not in ledger.get(pg_case.plan.id)["checkpoints"]
    thread.join(timeout=20)
    assert not thread.is_alive() and not failures, failures
    assert committed.is_set(), "Proxy did not observe the committed transaction"
    with open_adapter("postgres", pg_case.dsn) as adapter:
        assert adapter.fetch(
            f"SELECT carrier_v2 FROM {quote(pg_case.plan.current.table)} WHERE id=1"
        ) == [(10,)]
    resume_and_assert(pg_case)


@pytest.mark.parametrize("failure", ["disk_full", "read_only", "permission_denied"])
def test_checkpoint_storage_failure_never_completes_and_can_recover(case, monkeypatch, failure):
    engine, adapter, ledger, plan = approved(case)
    original = ledger.checkpoint
    ledger.connection.execute("CREATE TABLE quota_fill(data BLOB)")
    ledger.connection.commit()

    def fail_checkpoint(plan_id, action_id, detail):
        if action_id.startswith("backfill:"):
            if failure == "read_only":
                ledger.connection.execute("PRAGMA query_only=ON")
            elif failure == "disk_full":
                ledger.connection.executescript(
                    "CREATE TRIGGER full_checkpoint BEFORE INSERT ON checkpoints BEGIN INSERT INTO quota_fill VALUES(zeroblob(1048576)); END;"
                )
                pages = ledger.connection.execute("PRAGMA page_count").fetchone()[0]
                ledger.connection.execute(f"PRAGMA max_page_count={pages}")
            else:
                raise PermissionError("private path and credentials must not be logged")
        return original(plan_id, action_id, detail)

    monkeypatch.setattr(ledger, "checkpoint", fail_checkpoint)
    with pytest.raises((EngineError, sqlite3.DatabaseError, PermissionError)):
        engine.apply(plan, "operator", maintenance_window=True)
    ledger.connection.execute("PRAGMA query_only=OFF")
    ledger.connection.execute("PRAGMA max_page_count=1073741823")
    ledger.connection.execute("DROP TRIGGER IF EXISTS full_checkpoint")
    ledger.connection.commit()
    monkeypatch.setattr(ledger, "checkpoint", original)
    run = ledger.get(plan.id)
    assert run["status"] in {"RUNNING", "FAILED"}
    assert "backfill:carrier_v2:2026-01-01" not in run["checkpoints"]
    assert adapter.fetch("SELECT carrier_v2 FROM shipments WHERE id=1") == [(10,)]
    assert "private path" not in str(ledger.events(plan.id))
    with pytest.raises(EngineError, match="explicit resume"):
        engine.apply(plan, "operator", maintenance_window=True)
    assert (
        engine.apply(plan, "operator", resume=True, maintenance_window=True)["status"] == "COMPLETE"
    )
    assert ledger.verify_integrity(plan.id)
