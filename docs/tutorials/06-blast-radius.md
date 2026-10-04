# Analyze downstream blast radius

Explicit dependency nodes are stored in an ODCS namespaced custom property. Cycle-safe traversal reports direct/transitive consumers, transformations, quality policies and SLA nodes. Absence of a declared edge is not proof that a consumer is unaffected.

Install from the repository: `python -m pip install ".[duckdb]"`. Run commands in a fresh working directory. The commands below were executed by `python examples/run_tutorials.py`; selected JSON excerpts are labelled. All data is synthetic.

Setup (synthetic data):

```sh
partitionguard init contracts
python /path/to/partitionguard/examples/create_demo.py data.duckdb
```

```sh
$ partitionguard impact contracts/current.yaml contracts/proposed.yaml --json
```

Actual output:

```text
{"direct":["carrier_performance","shipment_summary"],"quality_checks":[],"slas":["service_level_metrics"],"transformations":["carrier_performance","shipment_summary","weekly_logistics_report"],"transitive":["service_level_metrics","weekly_logistics_report"],"unknown":[]}
```
