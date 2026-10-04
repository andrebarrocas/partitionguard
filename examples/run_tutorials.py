"""Execute all seven tutorials and regenerate their actual transcripts."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

import duckdb
from create_demo import create

from partitionguard.examples import initialize

ROOT = Path(__file__).resolve().parents[1]


def run_scenario(kind: str):
    chunks = []
    with tempfile.TemporaryDirectory(prefix="partitionguard-tutorial-") as temp:
        work = Path(temp)
        initialize(work / "contracts")
        create(work / "data.duckdb")
        chunks.append(
            "Setup (synthetic data):\n\n```sh\npartitionguard init contracts\npython /path/to/partitionguard/examples/create_demo.py data.duckdb\n```\n"
        )

        def run(args, expected=0, excerpt=None, python_script=False):
            command = (
                [sys.executable, str(ROOT / "examples" / args[0]), *args[1:]]
                if python_script
                else [sys.executable, "-m", "partitionguard", *args]
            )
            env = dict(os.environ, NO_COLOR="1", COLUMNS="110", TERM="dumb")
            result = subprocess.run(command, cwd=work, env=env, text=True, capture_output=True)
            if result.returncode != expected:
                raise RuntimeError(
                    f"{args}: expected {expected}, got {result.returncode}\n{result.stdout}\n{result.stderr}"
                )
            display = (
                ("python /path/to/partitionguard/examples/" + shlex.join(args))
                if python_script
                else "partitionguard " + shlex.join(args)
            )
            text = result.stdout.strip()
            if excerpt is not None:
                data = json.loads(text)
                text = json.dumps(excerpt(data), indent=2)
            chunks.append(
                "```sh\n$ "
                + display
                + "\n```\n\n"
                + (
                    "Selected fields from actual JSON output:\n\n"
                    if excerpt
                    else "Actual output:\n\n"
                )
                + "```text\n"
                + text
                + "\n```\n"
            )
            if expected:
                chunks.append(f"Observed exit code: `{expected}`.\n")
            return result

        def prepare(rename=False):
            proposed = "rename.yaml" if rename else "proposed.yaml"
            recipe = "rename-migration.yaml" if rename else "migration.yaml"
            args = [
                "plan",
                "contracts/current.yaml",
                "contracts/" + proposed,
                "--migration",
                "contracts/" + recipe,
                "--output",
                "plan.json",
                "--json",
            ]
            result = run(
                args,
                excerpt=lambda d: {
                    "plan_id": d["plan_id"],
                    "risk": d["plan"]["risk"],
                    "blockers": d["plan"]["blockers"],
                    "actions": len(d["plan"]["actions"]),
                },
            )
            identity = json.loads(result.stdout)["plan_id"]
            run(
                [
                    "migrate",
                    "plan.json",
                    "--connection",
                    "data.duckdb",
                    "--ledger",
                    "ledger.sqlite",
                    "--json",
                ],
                excerpt=lambda d: {k: d[k] for k in ("status", "dry_run", "checkpoints")},
            )
            run(
                [
                    "approve",
                    "plan.json",
                    "--connection",
                    "data.duckdb",
                    "--ledger",
                    "ledger.sqlite",
                    "--digest",
                    identity,
                    "--operator",
                    "tutorial",
                    "--json",
                ]
            )

        def apply(resume=False):
            args = [
                "migrate",
                "plan.json",
                "--connection",
                "data.duckdb",
                "--ledger",
                "ledger.sqlite",
                "--execute",
                "--operator",
                "tutorial",
                "--maintenance-window",
                "--json",
            ]
            if resume:
                args += ["--resume"]
            return run(
                args,
                excerpt=lambda d: {
                    "status": d["status"],
                    "checkpoints": d["checkpoints"],
                    "verification_passed": d["verification"]["passed"],
                },
            )

        if kind == "rename":
            prepare(rename=True)
            apply()
            with duckdb.connect(str(work / "data.duckdb"), read_only=True) as db:
                rows = db.execute(
                    "SELECT event_id, carrier_code, carrier_id FROM shipment_events ORDER BY event_id"
                ).fetchall()
            chunks.append(
                "Actual query result (`SELECT event_id, carrier_code, carrier_id FROM shipment_events ORDER BY event_id`):\n\n```json\n"
                + json.dumps(rows, indent=2)
                + "\n```\n"
            )
        elif kind == "type":
            run(["plan", "contracts/current.yaml", "contracts/breaking.yaml", "--ci"], expected=3)
            prepare()
            apply()
        elif kind == "backfill":
            prepare()
            apply()
            with duckdb.connect(str(work / "data.duckdb"), read_only=True) as db:
                rows = db.execute(
                    "SELECT CAST(event_date AS VARCHAR), count(*), count(carrier_code_v2) FROM shipment_events GROUP BY event_date ORDER BY event_date"
                ).fetchall()
            chunks.append(
                "Actual query result: date, total rows, populated replacement rows.\n\n```json\n"
                + json.dumps(rows, indent=2)
                + "\n```\n"
            )
        elif kind == "recovery":
            prepare()
            run(
                ["inject_failure.py", "plan.json", "data.duckdb", "ledger.sqlite"],
                python_script=True,
            )
            apply(resume=True)
            run(
                ["history", "--ledger", "ledger.sqlite", "--json"],
                excerpt=lambda d: [
                    {
                        "status": r["status"],
                        "attempts": r["attempts"],
                        "checkpoints": len(r["checkpoints"]),
                    }
                    for r in d
                ],
            )
        elif kind == "verify":
            prepare()
            apply()
            run(
                [
                    "verify",
                    "plan.json",
                    "--connection",
                    "data.duckdb",
                    "--ledger",
                    "ledger.sqlite",
                    "--json",
                ],
                excerpt=lambda d: {"passed": d["passed"], "check_count": len(d["checks"])},
            )
            with duckdb.connect(str(work / "data.duckdb")) as db:
                db.execute("UPDATE shipment_events SET carrier_code_v2=-1 WHERE event_id=1")
            chunks.append(
                'For this synthetic failure demonstration only, run:\n\n```python\nimport duckdb\n\nwith duckdb.connect("data.duckdb") as db:\n    db.execute("UPDATE shipment_events SET carrier_code_v2=-1 WHERE event_id=1")\n```\n'
            )
            run(
                [
                    "verify",
                    "plan.json",
                    "--connection",
                    "data.duckdb",
                    "--ledger",
                    "ledger.sqlite",
                    "--json",
                ],
                expected=5,
                excerpt=lambda d: {
                    "passed": d["passed"],
                    "failed_checks": [c for c in d["checks"] if not c["passed"]],
                },
            )
            run(
                ["status", "plan.json", "--ledger", "ledger.sqlite", "--json"],
                excerpt=lambda d: {"status": d["status"], "error": d["error"]},
            )
        elif kind == "impact":
            run(["impact", "contracts/current.yaml", "contracts/proposed.yaml", "--json"])
        elif kind == "ci":
            run(
                [
                    "plan",
                    "contracts/current.yaml",
                    "contracts/breaking.yaml",
                    "--ci",
                    "--summary",
                    "summary.md",
                ],
                expected=3,
            )
            run(
                [
                    "plan",
                    "contracts/current.yaml",
                    "contracts/proposed.yaml",
                    "--migration",
                    "contracts/migration.yaml",
                    "--ci",
                    "--summary",
                    "summary.md",
                ]
            )
    return "\n".join(chunks)


TUTORIALS = [
    (
        "01-rename.md",
        "Safely rename a column",
        "rename",
        "Use an additive expand stage: retain `carrier_code`, populate `carrier_id`, then migrate producers and consumers outside this tool. COMPLETE means the selected historical expand stage is verified. Final removal and online cutover are unsupported and require a separate reviewed process.",
    ),
    (
        "02-type-migration.md",
        "Perform an incompatible type migration",
        "type",
        "An in-place STRING → INTEGER change is breaking and blocked. Supply an additive target contract with `carrier_code_v2` and an explicit cast. Integer strings must be ASCII signed digits; fractional/exponent/whitespace values fail before mutation.",
    ),
    (
        "03-backfill.md",
        "Backfill historical partitions",
        "backfill",
        "The manifest selects [2026-01-01, 2026-01-04), with three daily batches and finite row caps. Outside rows remain untouched, including values that are not valid integers. These are logical row bounds; SQL engines may still scan more physical data.",
    ),
    (
        "04-recovery.md",
        "Recover from a failed migration",
        "recovery",
        "Use synthetic data to inject failure after a committed partition. The Python-only test hook demonstrates recovery; real interruptions use the same `--resume` path. Freeze writers, preserve the original plan and ledger, and inspect failures before continuing. Source drift blocks recovery rather than silently replacing the baseline.",
    ),
    (
        "05-verification.md",
        "Verify a completed migration",
        "verify",
        "Required checks run automatically before COMPLETE. Reverification can revoke that state when data is corrupted. This tutorial deliberately corrupts a replacement value in synthetic data and records the observed failed evidence.",
    ),
    (
        "06-blast-radius.md",
        "Analyze downstream blast radius",
        "impact",
        "Explicit dependency nodes are stored in an ODCS namespaced custom property. Cycle-safe traversal reports direct/transitive consumers, transformations, quality policies and SLA nodes. Absence of a declared edge is not proof that a consumer is unaffected.",
    ),
    (
        "07-github-actions.md",
        "Run change-safety checks in GitHub Actions",
        "ci",
        "Plan CI is credential-free and read-only. It fails with exit 3 for missing required migrations, incompatible policy, or unsupported execution semantics. A passing plan is not a data dry-run or permission to execute.",
    ),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / "tutorials")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for filename, title, kind, introduction in TUTORIALS:
        body = (
            "# "
            + title
            + "\n\n"
            + introduction
            + '\n\nInstall from the repository: `python -m pip install ".[duckdb]"`. Run commands in a fresh working directory. The commands below were executed by `python examples/run_tutorials.py`; selected JSON excerpts are labelled. All data is synthetic.\n\n'
            + run_scenario(kind)
        )
        if kind == "ci":
            body += """\nA repository workflow for your checked-in contracts (adapt the paths):\n
```yaml
name: Change safety
on: [pull_request]
permissions:
  contents: read
jobs:
  plan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
      - uses: actions/setup-python@v6
        with:
          python-version: '3.12'
      - run: python -m pip install .
      - run: partitionguard plan contracts/current.yaml contracts/proposed.yaml --migration contracts/migration.yaml --ci --summary summary.md
      - if: always()
        run: cat summary.md >> "$GITHUB_STEP_SUMMARY"
```

Use `pull_request`, not a privileged trigger for untrusted code. A local CLI return code is shown above; no claim is made that this example workflow has run on a remote repository.\n"""
        (args.output / filename).write_text(body)
        print("Executed and recorded " + filename)


if __name__ == "__main__":
    main()
