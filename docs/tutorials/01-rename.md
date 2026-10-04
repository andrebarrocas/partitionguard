# Safely rename a column

Use an additive expand stage: retain `carrier_code`, populate `carrier_id`, then migrate producers and consumers outside this tool. COMPLETE means the selected historical expand stage is verified. Final removal and online cutover are unsupported and require a separate reviewed process.

Install from the repository: `python -m pip install ".[duckdb]"`. Run commands in a fresh working directory. The commands below were executed by `python examples/run_tutorials.py`; selected JSON excerpts are labelled. All data is synthetic.

Setup (synthetic data):

```sh
partitionguard init contracts
python /path/to/partitionguard/examples/create_demo.py data.duckdb
```

```sh
$ partitionguard plan contracts/current.yaml contracts/rename.yaml --migration contracts/rename-migration.yaml --output plan.json --json
```

Selected fields from actual JSON output:

```text
{
  "plan_id": "69a7cad0ff7cd2275a533329a8d10290b3992034c8839caa12515c4bdb38847d",
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
$ partitionguard approve plan.json --connection data.duckdb --ledger ledger.sqlite --digest 69a7cad0ff7cd2275a533329a8d10290b3992034c8839caa12515c4bdb38847d --operator tutorial --json
```

Actual output:

```text
{"approved_digest":"69a7cad0ff7cd2275a533329a8d10290b3992034c8839caa12515c4bdb38847d","plan_id":"69a7cad0ff7cd2275a533329a8d10290b3992034c8839caa12515c4bdb38847d","status":"APPROVED"}
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

Actual query result (`SELECT event_id, carrier_code, carrier_id FROM shipment_events ORDER BY event_id`):

```json
[
  [
    1,
    "10",
    "10"
  ],
  [
    2,
    "20",
    "20"
  ],
  [
    3,
    "10",
    "10"
  ],
  [
    4,
    "30",
    "30"
  ],
  [
    5,
    "20",
    "20"
  ],
  [
    6,
    "30",
    "30"
  ],
  [
    7,
    "legacy",
    null
  ],
  [
    8,
    "future",
    null
  ]
]
```
