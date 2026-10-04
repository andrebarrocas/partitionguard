# Competitive landscape and differentiation review

Evaluated 2026-10-04 using project-maintained documentation. This is a scoped product comparison, not an exhaustive feature audit or performance benchmark. Product documentation changes; links identify the evidence used. Boundaries below describe PartitionGuard's intended scope, not categorical claims that another product cannot solve a problem through extensions.

## Existing categories

| Category / examples | Existing capability and overlap | PartitionGuard boundary |
| --- | --- | --- |
| Contracts — Data Contract CLI | ODCS linting, compatibility changes, schema/quality testing, import/export, and CI already exist. Merely adding a schema diff CLI would duplicate established work. [Commands](https://docs.datacontract.com/commands), [overview](https://docs.datacontract.com/) | Read established contracts; focus on executable transitions and recovery evidence. |
| Catalogs — DataHub | Dataset and column lineage, impact analysis, and connected metadata already serve discovery and dependency investigation. [DataHub lineage](https://github.com/datahub-project/datahub/blob/master/docs/features/feature-guides/lineage.md) | Consume dependency information; do not build a catalog, glossary, or discovery UI. |
| Catalogs — OpenMetadata | Table/column lineage supports impact analysis and can be captured automatically or edited manually. [Column lineage](https://docs.open-metadata.org/v1.12.x/how-to-guides/data-lineage/column) | Carry source/provenance into a migration's impact report. |
| Lineage — OpenLineage | Standardized job, dataset, and run events connect observed transformations. [Object model](https://openlineage.io/docs/spec/object-model/) | Optional lineage ingestion; observations do not authorize writes or prove consumer completeness. |
| Orchestration — Airflow | Date-range backfills already support dry runs, reprocessing modes, ordering, and concurrency limits. [Backfill](https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/backfill.html) | Run a bounded change procedure synchronously or under an existing scheduler; do not introduce a scheduler. |
| Asset orchestration — Dagster | Partitioned backfills and policies for batching partitions are existing capabilities. [Backfills](https://dagster.io/docs/guides/build/partitions-and-backfills/backfilling-data), [partition API](https://dagster.io/docs/api/dagster/partitions) | Reuse orchestration if necessary; specialize the plan, approval, receipts, and acceptance evidence. |
| Transformations — dbt | Incremental models have schema-change policies. Full refresh rebuilds models; its documentation explicitly distinguishes schema changes from backfilling existing rows. [Incremental models](https://docs.getdbt.com/docs/build/incremental-models) | Operate on existing datasets without requiring adoption of a transformation project. |
| Transformation change management — SQLMesh | Plans compare project/environment state, classify changes, describe indirect effects and intervals, and apply backfills. Restatements reprocess selected historical ranges and downstream models. This is the closest overlap. [Plans](https://sqlmesh.readthedocs.io/en/stable/concepts/plans/), [current plan reference](https://github.com/SQLMesh/sqlmesh/blob/main/docs/concepts/plans.md) | Prove value for externally owned datasets and explicit expand/backfill/verify procedures. Do not present “plan/apply for data” as an original invention. |
| Schema migrations — Flyway | Ordered migration history records identity, checksums, operator/timing information, and outcomes; validation detects changes to migration files. [Schema history](https://documentation.red-gate.com/flyway/flyway-concepts/migrations/flyway-schema-history-table) | Reuse established migration principles while making partition work and data acceptance evidence first-class. |
| Schema migrations — Liquibase | Changesets and the database changelog track applied migrations and checksums. [DATABASECHANGELOG](https://docs.liquibase.com/concepts/basic/databasechangelog-table.html) | Do not claim audit trails or resumable change tracking are novel. |
| Quality — Great Expectations | Validation definitions, persisted results, checkpoints, and result-triggered actions already operationalize assertions. [Checkpoints](https://docs.greatexpectations.io/docs/core/trigger_actions_based_on_results/create_a_checkpoint_with_actions/) | Bind assertions to the exact migration scope and require them before completion; leave broad quality management to established tools. |

## Chosen wedge

The proposed differentiation is the **combination** of a reviewable execution artifact, a hard workload boundary, adapter-specific idempotency and crash reconciliation, and deterministic post-migration acceptance evidence for an existing dataset. Schema analysis and lineage explain a change, but the migration run is the principal product object.

A plan should connect its contract inputs, explicit conversion or rename intent, affected partitions, external coordination gates, approval identity, physical actions, destination receipts, retries, and verification results. An engineer should answer “what remains safe to do?” after a failure from that artifact and its ledger.

The strongest design constraints are:

1. Keep adoption independent of an existing transformation framework.
2. Make approval apply to a particular plan and target, not a generic “yes” checkbox.
3. Handle the destination-commit / local-checkpoint gap explicitly.
4. Refuse unbounded work and unsupported guarantees.
5. Report incomplete external cutover and verification honestly.
6. Produce a portable machine-readable evidence record without claiming tamper-proof storage.

These requirements are our inference about a useful product position. The cited documentation establishes overlap; it does not prove market demand or exclusive functionality.

## Go / no-go decision

**Conditional go for a deliberately narrow implementation.** Start with reproducible migrations over real DuckDB and PostgreSQL tables, and a capability-scoped Delta Lake integration only after the core recovery path works. The release should demonstrate failure, reconciliation, resume, and a verification gate; an impressive impact report alone is insufficient.

**No-go for a generic contract validator, catalog, or transformation framework.** Also stop expanding scope if the system becomes a thin SQL runner with cosmetic checkpoints. If interviews show intended users already solve the entire job in SQLMesh or their orchestrator with little additional work, contribute the missing capability there or revise the wedge.

## Validation still required

Recruit maintainers of independently produced datasets; ask them to run a real historical correction. Measure setup effort, clarity of failure recovery, prevented operator mistakes, and whether evidence is useful in a review. Document cases where the tool provides no advantage. This research review establishes architectural focus, not user adoption or a moat.
