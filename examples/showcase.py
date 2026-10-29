"""Exercise the public CLI on disposable synthetic data and verify each outcome."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import duckdb
from create_demo import create

from partitionguard.examples import initialize

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def run_showcase():
    with tempfile.TemporaryDirectory(prefix="partitionguard-showcase-") as directory:
        work = Path(directory)
        initialize(work / "contracts")
        create(work / "warehouse.duckdb")

        def cli(*arguments, expected=0):
            result = subprocess.run(
                [sys.executable, "-m", "partitionguard", *arguments, "--json"],
                cwd=work,
                text=True,
                capture_output=True,
                timeout=60,
            )
            require(
                result.returncode == expected,
                f"{arguments[0]} returned {result.returncode}; expected {expected}: "
                f"{result.stdout}{result.stderr}",
            )
            return json.loads(result.stdout if result.stdout.strip() else result.stderr)

        def query(sql):
            with duckdb.connect(str(work / "warehouse.duckdb"), read_only=True) as database:
                return database.execute(sql).fetchall()

        originals = query(
            "SELECT event_id, event_date, carrier_code, amount "
            "FROM shipment_events ORDER BY event_id"
        )
        planned = cli(
            "plan",
            "contracts/current.yaml",
            "contracts/proposed.yaml",
            "--migration",
            "contracts/migration.yaml",
            "--output",
            "plan.json",
            "--ci",
        )
        require(not planned["plan"]["blockers"], "The example plan was blocked")
        target = (
            "plan.json",
            "--connection",
            "warehouse.duckdb",
            "--ledger",
            "ledger.sqlite",
        )
        execution = ("--execute", "--maintenance-window", "--operator", "showcase")
        cli("migrate", *target, *execution, expected=6)
        prepared = cli("migrate", *target)
        require(prepared["status"] == "PREPARED", "Dry run did not prepare the target")
        require(
            "carrier_code_v2" not in {row[0] for row in query("DESCRIBE shipment_events")},
            "Dry run changed the table schema",
        )
        cli("approve", *target, "--digest", "wrong", "--operator", "showcase", expected=6)
        approved = cli("approve", *target, "--digest", planned["plan_id"], "--operator", "showcase")
        require(approved["status"] == "APPROVED", "Exact-plan approval failed")

        interruption = subprocess.run(
            [
                sys.executable,
                str(ROOT / "examples" / "inject_failure.py"),
                "plan.json",
                "warehouse.duckdb",
                "ledger.sqlite",
            ],
            cwd=work,
            text=True,
            capture_output=True,
            timeout=60,
        )
        require(interruption.returncode == 0, interruption.stdout + interruption.stderr)
        failed = json.loads(interruption.stdout)
        populated_before_resume = query(
            "SELECT count(*) FROM shipment_events WHERE carrier_code_v2 IS NOT NULL"
        )[0][0]
        require(failed["status"] == "FAILED", "Failure injection was not recorded")
        require(populated_before_resume == 2, "Expected exactly one committed partition")

        recovered = cli("migrate", *target, *execution, "--resume")
        require(recovered["status"] == "COMPLETE", "Recovery did not finish")
        verified = cli("verify", *target)
        require(verified["passed"] is True, "Recovered migration did not verify")
        require(recovered["checkpoints"] == 4, "Expected DDL and three partition checkpoints")
        actual = query("SELECT event_id, carrier_code_v2 FROM shipment_events ORDER BY event_id")
        require(
            actual == [(1, 10), (2, 20), (3, 10), (4, 30), (5, 20), (6, 30), (7, None), (8, None)],
            "Backfill did not preserve its exact row scope and values",
        )

        # Deliberate corruption of this disposable demo proves verification can fail.
        with duckdb.connect(str(work / "warehouse.duckdb")) as database:
            database.execute("UPDATE shipment_events SET carrier_code_v2=-1 WHERE event_id=1")
        rejected = cli("verify", *target, expected=5)
        require(rejected["passed"] is False, "Corruption was not detected")
        corrupt_status = cli("status", "plan.json", "--ledger", "ledger.sqlite")
        require(
            corrupt_status["status"] == "VERIFICATION_FAILED",
            "Verification failure did not revoke completion",
        )
        repaired = cli("migrate", *target, *execution, "--resume")
        require(repaired["status"] == "COMPLETE", "Deterministic repair did not verify")
        require(
            query("SELECT event_id, carrier_code_v2 FROM shipment_events ORDER BY event_id")
            == actual,
            "Repair changed the expected migrated values",
        )
        require(
            query(
                "SELECT event_id, event_date, carrier_code, amount "
                "FROM shipment_events ORDER BY event_id"
            )
            == originals,
            "Original source data changed",
        )
        events = cli("history", "--ledger", "ledger.sqlite", "--plan-file", "plan.json")
        kinds = {event["kind"] for event in events}
        require(
            {"EXECUTION_FAILED", "ACTION_RECONCILED", "ACTION_REPLAY_REQUIRED"} <= kinds,
            "Recovery evidence is incomplete",
        )
        return {
            "product": "shipment_events",
            "total_rows": len(originals),
            "selected_partitions": 3,
            "migrated_rows": 6,
            "outside_scope_rows_untouched": 2,
            "dry_run": prepared["status"],
            "unapproved_execution_exit": 6,
            "wrong_approval_exit": 6,
            "injected_failure": failed["status"],
            "populated_rows_before_resume": populated_before_resume,
            "recovery": recovered["status"],
            "checkpoints": recovered["checkpoints"],
            "required_checks_passed": len(verified["checks"]),
            "corruption_detected": rejected["passed"] is False,
            "corruption_exit": 5,
            "corrupted_status": corrupt_status["status"],
            "repair": repaired["status"],
            "original_source_preserved": True,
            "audit_events": len(events),
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Emit verified observations as JSON")
    arguments = parser.parse_args()
    result = run_showcase()
    if arguments.json:
        print(json.dumps(result, indent=2))
    else:
        print("PartitionGuard — executable recovery and verification demo")
        print(f"Scope: {result['migrated_rows']}/{result['total_rows']} rows in 3 date partitions")
        print(f"Dry run: {result['dry_run']} (schema unchanged)")
        print("Unapproved execution and wrong plan approval: BLOCKED (exit 6)")
        print(
            f"Injected failure: {result['injected_failure']} "
            f"after {result['populated_rows_before_resume']} committed rows"
        )
        print(f"Resume: {result['recovery']} with {result['checkpoints']} checkpoints")
        print(f"Verification: {result['required_checks_passed']} checks passed")
        print(f"Corruption detected: {result['corrupted_status']} (exit 5)")
        print(f"Repair and reverify: {result['repair']}")
        print(f"Source preserved; {result['outside_scope_rows_untouched']} outside rows untouched")
        print(f"Audit: {result['audit_events']} durable events, integrity checked on read")


if __name__ == "__main__":
    main()
