# Repository structure

The intended layout keeps the deterministic control plane separate from engine integrations. Concrete filenames may be consolidated where that makes the implementation easier to understand.

```text
partitionguard/
  pyproject.toml                 # package metadata and optional dependencies
  README.md                     # installation, quick start, capability matrix
  CHANGELOG.md
  LICENSE
  CONTRIBUTING.md
  CODE_OF_CONDUCT.md
  SECURITY.md
  src/partitionguard/
    cli.py                      # arguments and rendering; delegates decisions
    contracts.py                # normalized model and contract loaders
    diff.py                     # deterministic change facts
    compatibility.py            # rules and policy
    graph.py                    # explicit dependency graph and impact
    risk.py                     # explained deterministic severity
    planner.py                  # typed migration and bounded backfill plans
    ledger.py                   # durable runs, events, checkpoints, approvals
    execution.py                # state machine, preflight, retry, resume
    verification.py             # checks, thresholds, completion gate
    plugins.py                  # extension protocols and registry
    adapters/                   # optional storage integrations
  tests/
    unit/                       # analysis, graph, plans, state and checks
    integration/                # real DuckDB/PostgreSQL/Delta engines
    fixtures/                   # small contracts and deterministic data
  examples/                     # executable local examples
  docs/
    adr/                        # architectural decisions
    tutorials/                  # commands and captured output
    releases/                   # release evidence and limitations
  benchmarks/                   # reproducible measured performance scripts
  .github/
    workflows/                  # tests, package, release, documentation
    ISSUE_TEMPLATE/
    pull_request_template.md
```

Ledger databases, credentials, generated local data, environments, caches, and build output do not belong in source control. Examples generate their own small data. Test fixtures must not contain production records or secrets. Generated tutorial transcripts identify the script/version that produced them.

Use the smallest module split that maintains safety boundaries. Directory size and interface count are not success metrics. Tests target behavior and failure modes instead of mirroring every helper function.
