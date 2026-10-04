# Perform an incompatible type migration

An in-place STRING → INTEGER change is breaking and blocked. Supply an additive target contract with `carrier_code_v2` and an explicit cast. Integer strings must be ASCII signed digits; fractional/exponent/whitespace values fail before mutation.

Install from the repository: `python -m pip install ".[duckdb]"`. Run commands in a fresh working directory. The commands below were executed by `python examples/run_tutorials.py`; selected JSON excerpts are labelled. All data is synthetic.

Setup (synthetic data):

```sh
partitionguard init contracts
python /path/to/partitionguard/examples/create_demo.py data.duckdb
```

```sh
$ partitionguard plan contracts/current.yaml contracts/breaking.yaml --ci
```

Actual output:

```text
PartitionGuard · shipment_events · 17 → 18
Plan: 61773b6fc5fd54ab5c6f95b6947bed99088beb8b0147729eb0ba1fd051f52b12
Adapter: duckdb · Risk: BLOCKING
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━━━━┓
┃ Change                    ┃ Before ┃ After   ┃ Compatibility ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━━━━┩
│ columns.carrier_code.type │ STRING │ INTEGER │ BREAKING      │
└───────────────────────────┴────────┴─────────┴───────────────┘
Direct consumers: carrier_performance, shipment_summary
Transitive consumers: service_level_metrics, weekly_logistics_report
Affected transformations: 3 · SLA nodes: 1
Impact reflects declared dependencies; consumer discovery is incomplete by design.
• Breaking contract changes
• 4 declared downstream nodes affected
• Downstream SLA commitments are affected
• columns.carrier_code.type: in-place/retirement operation unsupported; use an additive stage, consumer 
migration, and separately reviewed retirement
• Backward compatibility policy rejects breaking changes
• Required migration manifest is absent; supply bounded operations and verification policy
Execution: BLOCKED
```

Observed exit code: `3`.

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
