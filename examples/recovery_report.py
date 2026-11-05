"""Generate real crash/recovery/corruption evidence on disposable DuckDB data."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from create_demo import create

from partitionguard.adapters import open_adapter
from partitionguard.engine import Engine
from partitionguard.examples import initialize
from partitionguard.ledger import Ledger
from partitionguard.planning import load_plan


def worker(folder):
    plan = load_plan(folder / "plan.json")
    with open_adapter("duckdb", str(folder / "data.duckdb"), read_only=False) as adapter:
        with Ledger(folder / "ledger.sqlite") as ledger:
            checkpoint = ledger.checkpoint

            def pause_before_checkpoint(plan_id, action_id, detail):
                if action_id.startswith("backfill:"):
                    (folder / "ready").write_text("committed", encoding="utf-8")
                    threading.Event().wait()
                return checkpoint(plan_id, action_id, detail)

            ledger.checkpoint = pause_before_checkpoint
            Engine(adapter, ledger).apply(plan, "demo-operator", maintenance_window=True)


def demonstrate(folder):
    if os.name != "posix":
        raise RuntimeError("This SIGKILL demonstration requires POSIX")
    folder = folder.resolve()
    folder.mkdir(parents=True, exist_ok=False)
    initialize(folder / "contracts")
    create(folder / "data.duckdb")
    transcript = []

    def record(message):
        transcript.append(message)
        print(message, flush=True)

    def cli(*args, expected=0):
        result = subprocess.run(
            [sys.executable, "-m", "partitionguard", *args],
            cwd=folder,
            text=True,
            capture_output=True,
            timeout=30,
        )
        if result.returncode != expected:
            raise RuntimeError(f"{args}: exit {result.returncode}: {result.stdout} {result.stderr}")
        return result.stdout

    planned = json.loads(
        cli(
            "plan",
            "contracts/current.yaml",
            "contracts/proposed.yaml",
            "--migration",
            "contracts/migration.yaml",
            "--output",
            "plan.json",
            "--json",
        )
    )
    connection = [
        "plan.json",
        "--ledger",
        "ledger.sqlite",
        "--connection",
        str(folder / "data.duckdb"),
    ]
    cli("migrate", *connection, "--json")
    cli(
        "approve",
        *connection,
        "--digest",
        planned["plan_id"],
        "--operator",
        "demo-reviewer",
        "--json",
    )
    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--worker", str(folder)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 20
        while (
            not (folder / "ready").exists()
            and process.poll() is None
            and time.monotonic() < deadline
        ):
            time.sleep(0.02)
        if not (folder / "ready").exists():
            raise RuntimeError("Worker did not reach the committed-action barrier")
        process.kill()
        process.communicate(timeout=10)
        if process.returncode != -9:
            raise RuntimeError("Worker was not terminated by SIGKILL")
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)
    state = json.loads(cli("status", "plan.json", "--ledger", "ledger.sqlite", "--json"))
    assert state["status"] == "RUNNING"
    assert len(state["checkpoints"]) == 1
    record("SIGKILL after target commit: RUNNING; backfill checkpoint absent")
    execution = [
        *connection,
        "--execute",
        "--maintenance-window",
        "--operator",
        "demo-operator",
        "--json",
    ]
    cli("migrate", *execution, expected=6)
    record("Execution without explicit resume: refused (exit 6)")
    resumed = json.loads(cli("migrate", *execution, "--resume"))
    assert resumed["status"] == "COMPLETE"
    record(f"Fresh-process resume: {resumed['status']}; {resumed['checkpoints']} checkpoints")

    def export(stem):
        for format in ("html", "json"):
            cli(
                "report",
                "plan.json",
                "--ledger",
                "ledger.sqlite",
                "--format",
                format,
                "--output",
                f"{stem}.{format}",
            )

    export("recovered")
    import duckdb

    with duckdb.connect(str(folder / "data.duckdb")) as connection_db:
        connection_db.execute("UPDATE shipment_events SET carrier_code_v2=999 WHERE event_id=1")
    verification = json.loads(cli("verify", *connection, "--json", expected=5))
    assert verification["passed"] is False
    record("Deliberate target corruption: verification failed (exit 5)")
    export("corrupted")
    repaired = json.loads(cli("migrate", *execution, "--resume"))
    assert repaired["status"] == "COMPLETE" and repaired["verification"]["passed"] is True
    record("Explicit resume after corruption: COMPLETE; verification passed")
    export("repaired")
    with Ledger(folder / "ledger.sqlite") as ledger:
        assert ledger.verify_integrity(planned["plan_id"])
    record("Exported recovered, corrupted and repaired HTML/JSON evidence snapshots")
    (folder / "transcript.txt").write_text("\n".join(transcript) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        worker(args.worker)
    elif args.output:
        demonstrate(args.output)
    else:
        parser.error("--output is required (must be a new directory)")
