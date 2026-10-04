# Run change-safety checks in GitHub Actions

Plan CI is credential-free and read-only. It fails with exit 3 for missing required migrations, incompatible policy, or unsupported execution semantics. A passing plan is not a data dry-run or permission to execute.

Install from the repository: `python -m pip install ".[duckdb]"`. Run commands in a fresh working directory. The commands below were executed by `python examples/run_tutorials.py`; selected JSON excerpts are labelled. All data is synthetic.

Setup (synthetic data):

```sh
partitionguard init contracts
python /path/to/partitionguard/examples/create_demo.py data.duckdb
```

```sh
$ partitionguard plan contracts/current.yaml contracts/breaking.yaml --ci --summary summary.md
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
$ partitionguard plan contracts/current.yaml contracts/proposed.yaml --migration contracts/migration.yaml --ci --summary summary.md
```

Actual output:

```text
PartitionGuard · shipment_events · 17 → 18-expand
Plan: 0730548d04ae38e00c67960e2cfa3d4621f5f0b8f1e5acd99f5f3866e83ad6b1
Adapter: duckdb · Risk: HIGH
┏━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━┓
┃ Change                  ┃ Before ┃ After            ┃ Compatibility ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━┩
│ columns.carrier_code_v2 │ —      │ INTEGER nullable │ SAFE          │
└─────────────────────────┴────────┴──────────────────┴───────────────┘
Direct consumers: carrier_performance, shipment_summary
Transitive consumers: service_level_metrics, weekly_logistics_report
Affected transformations: 3 · SLA nodes: 1
Impact reflects declared dependencies; consumer discovery is incomplete by design.
Backfill: 3 daily partitions · [2026-01-01, 2026-01-04) · row cap 300
Actions: 5 · source columns retained · writer maintenance window required
• 4 declared downstream nodes affected
• Downstream SLA commitments are affected
• Historical data writes require explicit approval
Execution: requires dry-run and explicit exact-plan approval
```

A repository workflow for your checked-in contracts (adapt the paths):

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

Use `pull_request`, not a privileged trigger for untrusted code. A local CLI return code is shown above; no claim is made that this example workflow has run on a remote repository.
