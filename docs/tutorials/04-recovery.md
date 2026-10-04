# Recover from a failed migration

Use synthetic data to inject failure after a committed partition. The Python-only test hook demonstrates recovery; real interruptions use the same `--resume` path. Freeze writers, preserve the original plan and ledger, and inspect failures before continuing. Source drift blocks recovery rather than silently replacing the baseline.

Install from the repository: `python -m pip install ".[duckdb]"`. Run commands in a fresh working directory. The commands below were executed by `python examples/run_tutorials.py`; selected JSON excerpts are labelled. All data is synthetic.

Setup (synthetic data):

```sh
partitionguard init contracts
python /path/to/partitionguard/examples/create_demo.py data.duckdb
```

```sh
$ partitionguard plan contracts/current.yaml contracts/proposed.yaml --migration contracts/migration.yaml --output plan.json --json
```

Selected fields from actual JSON output:

```text
{
  "plan_id": "0730548d04ae38e00c67960e2cfa3d4621f5f0b8f1e5acd99f5f3866e83ad6b1",
  "risk": {
    "level": "HIGH",
    "reasons": [
      "4 declared downstream nodes affected",
      "Downstream SLA commitments are affected",
      "Historical data writes require explicit approval"
    ]
  },
  "blockers": [],
  "actions": 5
}
```

```sh
$ partitionguard migrate plan.json --connection data.duckdb --ledger ledger.sqlite --json
```

Selected fields from actual JSON output:

```text
{
  "status": "PREPARED",
  "dry_run": true,
  "checkpoints": 0
}
```

```sh
$ partitionguard approve plan.json --connection data.duckdb --ledger ledger.sqlite --digest 0730548d04ae38e00c67960e2cfa3d4621f5f0b8f1e5acd99f5f3866e83ad6b1 --operator tutorial --json
```

Actual output:

```text
{"approved_digest":"0730548d04ae38e00c67960e2cfa3d4621f5f0b8f1e5acd99f5f3866e83ad6b1","plan_id":"0730548d04ae38e00c67960e2cfa3d4621f5f0b8f1e5acd99f5f3866e83ad6b1","status":"APPROVED"}
```

```sh
$ python /path/to/partitionguard/examples/inject_failure.py plan.json data.duckdb ledger.sqlite
```

Actual output:

```text
{"checkpointed_actions":["add:carrier_code_v2","backfill:carrier_code_v2:2026-01-01"],"message":"Synthetic interruption after one committed partition; resume uses the same plan.","status":"FAILED"}
```

```sh
$ partitionguard migrate plan.json --connection data.duckdb --ledger ledger.sqlite --execute --operator tutorial --maintenance-window --json --resume
```

Selected fields from actual JSON output:

```text
{
  "status": "COMPLETE",
  "checkpoints": 4,
  "verification_passed": true
}
```

```sh
$ partitionguard history --ledger ledger.sqlite --json
```

Selected fields from actual JSON output:

```text
[
  {
    "status": "COMPLETE",
    "attempts": 2,
    "checkpoints": 4
  }
]
```
