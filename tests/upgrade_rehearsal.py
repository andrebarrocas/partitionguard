"""Explicitly run against an installed previous wheel with PARTITIONGUARD_PREVIOUS_PYTHON."""

import json
import os
import sqlite3
import subprocess
import sys

from infrastructure import owned_postgres as owned_postgres
from test_storage_conformance import EXPECTED
from test_storage_conformance import conformance_target as conformance_target

from partitionguard.ledger import Ledger
from partitionguard.models import ExecutionPolicy
from partitionguard.planning import plan, save_plan


def test_recover_previous_wheel_canary_with_original_baseline(conformance_target, tmp_path):
    target = conformance_target
    old = os.environ["PARTITIONGUARD_PREVIOUS_PYTHON"]
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONPATH", "PARTITIONGUARD_TRUST_FILE", "PARTITIONGUARD_CONNECTION"}
    }

    def cli(python, *args, expected=0):
        result = subprocess.run(
            [python, "-m", "partitionguard", *map(str, args)],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == expected, result.stdout + result.stderr
        return result.stdout

    assert "0.1.0" in cli(old, "--version")
    assert "0.2.0" in cli(sys.executable, "--version")
    seed = target.plan
    recipe = seed.migration.model_copy(update={"execution": ExecutionPolicy(canary_partitions=1)})
    value = plan(seed.current, seed.proposed, seed.adapter, recipe)
    saved = tmp_path / "plan.json"
    ledger_path = tmp_path / "ledger.sqlite"
    save_plan(value, saved)
    args = [saved, "--connection", target.connection, "--ledger", ledger_path, "--json"]
    operator = ["--operator", "rehearsal"]
    flags = ["--execute", "--maintenance-window", *operator]
    cli(old, "migrate", *args)
    cli(old, "approve", *args, "--digest", value.id, *operator)
    assert json.loads(cli(old, "migrate", *args, *flags, expected=7))["status"] == "CANARY_PAUSED"
    # Read through the old controller before any newer code opens the ledger.
    before = json.loads(cli(old, "status", saved, "--ledger", ledger_path, "--json"))
    assert before["baseline"]["runtime_signature"]["core"] == "0.1.0"
    with sqlite3.connect(ledger_path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 0
    cli(sys.executable, "approve-continuation", *args, "--digest", value.id, *operator)
    assert (
        json.loads(cli(sys.executable, "migrate", *args, *flags, "--resume"))["status"]
        == "COMPLETE"
    )
    with Ledger(ledger_path) as ledger:
        after = ledger.get(value.id)
        assert ledger.connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert after["baseline"] == before["baseline"]
        assert after["plan"] == before["plan"]
        assert after["run_id"] == before["run_id"]
        assert all(
            after["checkpoints"][key] == checkpoint
            for key, checkpoint in before["checkpoints"].items()
        )
        assert after["verification"]["passed"] is True
        assert ledger.verify_integrity(value.id)
    assert target.inspect() == EXPECTED
