"""Real SIGKILL and a fresh public-CLI process exercise OS/database recovery."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import duckdb
import pytest
from test_engine import migration_plan

from partitionguard.adapters import open_adapter
from partitionguard.engine import Engine
from partitionguard.ledger import Ledger
from partitionguard.planning import save_plan


@pytest.mark.skipif(os.name != "posix", reason="SIGKILL recovery requires POSIX")
@pytest.mark.parametrize("barrier", ["inside_transaction", "before_checkpoint", "after_checkpoint"])
def test_sigkill_then_fresh_cli_resume(tmp_path, barrier):
    database = tmp_path / "warehouse.duckdb"
    journal = tmp_path / "ledger.sqlite"
    plan_file = tmp_path / "plan.json"
    marker = tmp_path / "ready"
    plan = migration_plan()
    save_plan(plan, plan_file)
    with duckdb.connect(str(database)) as connection:
        connection.execute("CREATE TABLE shipments(id INTEGER, day DATE, carrier VARCHAR)")
        connection.execute(
            "INSERT INTO shipments VALUES (1,'2026-01-01','10'), (2,'2026-01-02','20'), (3,'2026-01-03','30'), (4,'2026-02-01','40')"
        )
    with open_adapter("duckdb", str(database), read_only=False) as adapter:
        with Ledger(journal) as ledger:
            engine = Engine(adapter, ledger)
            engine.dry_run(plan)
            engine.approve(plan, plan.id, "reviewer")
    worker = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).with_name("process_worker.py")),
            str(plan_file),
            str(database),
            str(journal),
            barrier,
            str(marker),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 20
        while not marker.exists() and worker.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert marker.exists(), "Worker did not reach the crash barrier"
        worker.kill()
        worker.communicate(timeout=10)
        assert worker.returncode == -9
    finally:
        if worker.poll() is None:
            worker.kill()
        worker.communicate(timeout=10)
    # Reopen both stores after an uncatchable termination. No FAILED event can
    # have been written by an exception handler in the killed process.
    with Ledger(journal) as ledger:
        run = ledger.get(plan.id)
        assert run["status"] == "RUNNING"
        action = "backfill:carrier_v2:2026-01-01"
        assert (action in run["checkpoints"]) == (barrier == "after_checkpoint")
        assert not any(e["kind"] == "EXECUTION_FAILED" for e in ledger.events(plan.id))
    with duckdb.connect(str(database)) as connection:
        expected = None if barrier == "inside_transaction" else 10
        assert connection.execute("SELECT carrier_v2 FROM shipments WHERE id=1").fetchone() == (
            expected,
        )
    command = [
        sys.executable,
        "-m",
        "partitionguard",
        "migrate",
        str(plan_file),
        "--connection",
        str(database),
        "--ledger",
        str(journal),
        "--execute",
        "--maintenance-window",
        "--operator",
        "recovery-test",
        "--json",
    ]
    rejected = subprocess.run(command, capture_output=True, text=True, timeout=20)
    assert rejected.returncode == 6, rejected.stderr
    assert "explicit resume" in rejected.stderr
    resumed = subprocess.run([*command, "--resume"], capture_output=True, text=True, timeout=30)
    assert resumed.returncode == 0, resumed.stderr
    assert json.loads(resumed.stdout)["status"] == "COMPLETE"
    with Ledger(journal) as ledger:
        run = ledger.get(plan.id)
        assert run["attempts"] == 2
        assert len(run["checkpoints"]) == 4
        assert run["verification"]["passed"] is True
        assert ledger.verify_integrity(plan.id)
    with duckdb.connect(str(database)) as connection:
        assert connection.execute(
            "SELECT id,carrier,carrier_v2 FROM shipments ORDER BY id"
        ).fetchall() == [(1, "10", 10), (2, "20", 20), (3, "30", 30), (4, "40", None)]
