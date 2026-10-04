# Verify a completed migration

Required checks run automatically before COMPLETE. Reverification can revoke that state when data is corrupted. This tutorial deliberately corrupts a replacement value in synthetic data and records the observed failed evidence.

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
$ partitionguard migrate plan.json --connection data.duckdb --ledger ledger.sqlite --execute --operator tutorial --maintenance-window --json
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
$ partitionguard verify plan.json --connection data.duckdb --ledger ledger.sqlite --json
```

Selected fields from actual JSON output:

```text
{
  "passed": true,
  "check_count": 24
}
```

For this synthetic failure demonstration only, run:

```python
import duckdb

with duckdb.connect("data.duckdb") as db:
    db.execute("UPDATE shipment_events SET carrier_code_v2=-1 WHERE event_id=1")
```

```sh
$ partitionguard verify plan.json --connection data.duckdb --ledger ledger.sqlite --json
```

Selected fields from actual JSON output:

```text
{
  "passed": false,
  "failed_checks": [
    {
      "expected": 0,
      "name": "parity:carrier_code->carrier_code_v2",
      "observed": 1,
      "partition": "2026-01-01",
      "passed": false
    }
  ]
}
```

Observed exit code: `5`.

```sh
$ partitionguard status plan.json --ledger ledger.sqlite --json
```

Selected fields from actual JSON output:

```text
{
  "status": "VERIFICATION_FAILED",
  "error": "Required verification failed or errored"
}
```
