"""Execute a verified canary, approve continuation, pause, resume and export evidence."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

import yaml
from create_demo import create

from partitionguard.examples import initialize


def demonstrate(directory):
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    initialize(directory / "contracts")
    create(directory / "data.duckdb")
    manifest = directory / "contracts" / "migration.yaml"
    recipe = yaml.safe_load(manifest.read_text())
    recipe["execution"] = {
        "canary_partitions": 1,
        "canary_checks": [{"kind": "sql", "sql": "SELECT COUNT(*) = 2 FROM {scope}"}],
        "max_duration_seconds": 300,
    }
    manifest.write_text(yaml.safe_dump(recipe), encoding="utf-8")
    transcript = []

    def record(message):
        print(message, flush=True)
        transcript.append(message)

    def cli(*args, expected=0):
        result = subprocess.run(
            [sys.executable, "-m", "partitionguard", *args],
            cwd=directory,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != expected:
            raise RuntimeError(
                f"Unexpected exit {result.returncode}: {result.stdout} {result.stderr}"
            )
        return result.stdout

    plan = json.loads(
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
    args = [
        "plan.json",
        "--connection",
        str(directory / "data.duckdb"),
        "--ledger",
        "ledger.sqlite",
        "--json",
    ]
    cli("migrate", *args)
    cli("approve", *args, "--digest", plan["plan_id"], "--operator", "reviewer")
    execution = [*args, "--execute", "--maintenance-window", "--operator", "operator"]
    paused = json.loads(cli("migrate", *execution, expected=7))
    assert paused["status"] == "CANARY_PAUSED" and paused["checkpoints"] == 2
    record("Canary: CANARY_PAUSED; 2 checkpoints; scoped verification passed (exit 7)")
    cli("migrate", *execution, "--resume", expected=6)
    record("Resume without continuation approval: refused (exit 6)")
    cli(
        "report",
        "plan.json",
        "--ledger",
        "ledger.sqlite",
        "--format",
        "json",
        "--output",
        "canary.json",
    )
    evidence = json.loads((directory / "canary.json").read_text())
    assert evidence["latest_recorded_verification"] is None
    assert evidence["canary_verification"]["passed"] is True
    cli("approve-continuation", *args, "--digest", plan["plan_id"], "--operator", "reviewer")
    record("Exact-plan continuation approved after fresh canary/source recheck")
    cli("pause", "plan.json", "--ledger", "ledger.sqlite", "--operator", "other-operator")
    held = json.loads(cli("migrate", *execution, "--resume", expected=7))
    assert held["status"] == "PAUSED" and held["checkpoints"] == 2
    record("Durable pause request: PAUSED; no additional checkpoints (exit 7)")
    complete = json.loads(cli("migrate", *execution, "--resume"))
    assert complete["status"] == "COMPLETE" and complete["verification"]["passed"] is True
    record(
        f"Explicit resume: COMPLETE; {complete['checkpoints']} checkpoints; full final policy passed"
    )
    for format in ("html", "json"):
        cli(
            "report",
            "plan.json",
            "--ledger",
            "ledger.sqlite",
            "--format",
            format,
            "--output",
            f"completed.{format}",
        )
    (directory / "transcript.txt").write_text("\n".join(transcript) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    demonstrate(parser.parse_args().output)
