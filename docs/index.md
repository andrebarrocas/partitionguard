# PartitionGuard

**Safely plan, execute, recover, and verify changes to production data products.**

Created and maintained by [André Barrocas](https://github.com/andrebarrocas).
For a portfolio-oriented introduction, start with the
[engineering walkthrough and runnable demo](project-showcase.md).

PartitionGuard focuses on bounded, additive migrations of existing datasets. A plan connects the contract change, affected consumers, physical actions, approval, checkpoints, and deterministic acceptance evidence. The migration run is the main product object.

Start with the runnable tutorials in the navigation. The [problem statement](problem-statement.md), [competitive review](competitive-landscape.md), and [architecture](architecture.md) explain why the project exists and what it deliberately leaves to transformation, catalog, and orchestration tools.

## Execution boundaries

This candidate executes replacement-column additions and bounded copy/cast backfills. It requires a dry run, approval of the full plan digest, finite date/row limits, a maintenance window, and passing verification. The original column remains available. Producer changes, consumer cutover, and retiring the old column are separate operational steps.

DuckDB and PostgreSQL adapters establish the relational path. The [Delta adapter](delta.md) adds physically partitioned local execution and historical verification. Open standards are treated as interoperability inputs: read [ODCS support and limits](standards-integration.md) before assuming every contract field is enforced.

## Review and contribution

Read the [migration model](migration-model.md), [backfill model](backfill-model.md), [verification model](verification-model.md), and [plugin architecture](plugin-architecture.md) to understand the safety contract. The [roadmap](roadmap.md) separates implemented scope from future integrations. The [candidate release notes](releases/0.1.0.md) and [release operations](release-operations.md) explain what still needs owner action before public release.
