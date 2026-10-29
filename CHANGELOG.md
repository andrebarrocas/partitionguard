# Changelog

Changes follow semantic versioning. This file records implemented behavior; roadmap entries are not released features.

## Unreleased — project verification and presentation

### Fixed

- Pin wheel and source core metadata to version 2.4 so current Hatchling builds
  remain compatible with the Twine 6 validation used in CI and release checks.
  The prior remote test jobs passed, but package validation rejected metadata 2.5.

### Added

- A public-CLI demonstration that checks approval gates, bounded execution,
  persisted recovery, corruption detection, repair, and source preservation.
- CI execution of that demonstration and all seven tutorials, with JUnit,
  coverage and machine-readable demo evidence uploaded as artifacts.
- Explicit André Barrocas author/maintainer metadata, repository links, a live
  CI badge, and an engineering walkthrough for the personal project page.

## 0.1.0 — unreleased candidate

### Added

- Phase 0 problem, standards, competitor, compatibility, migration, verification, and architecture reviews, including explicit overlap with SQLMesh.
- A documented ODCS relational subset, deterministic schema changes, declared dependency traversal, explainable risk, and content-addressed plans.
- Typed additive replacement migrations with half-open date bounds, row caps, conversion preflight, exact-plan approval, and explicit maintenance-window acknowledgment.
- DuckDB and PostgreSQL storage adapters, a persistent execution ledger, committed-action checkpoints, explicit failure states, safe replay, and mandatory post-migration verification.
- Verification for original-row fingerprints, row counts, schema, partition coverage, transform parity, keys, nullability, enums, aggregate tolerances, freshness, and restricted SQL assertions.
- A local Delta Lake adapter with physical DATE partition boundaries, native atomic commits, conflict refusal, file-scope tests, historical snapshots, and recovery integration tests.
- Experimental read-only bounded S3 inventory and Glue schema inspection, tested with botocore stubs rather than a live AWS account; an offline OpenLineage importer for declared dependency information.
- A CLI, CI-friendly output and exit codes, plugin entry-point contracts, runnable tutorials, packaging, contributor policies, and manually gated release/documentation workflows.

### Safety limits

- Execution supports additive replacement stages. Producer changes, consumer cutover, legacy-column removal, online writers, and rollback of an entire multi-action migration are not automated.
- Contracts are a supported ODCS subset, not full ODCS certification. Unsupported safety semantics block execution.
- Delta execution is local only. AWS execution, Spark, remote object-store coordination, and live catalog/lineage connectors remain future work. AWS inventory is read-only and has no live-cloud validation claim.
- The hash-linked local ledger is tamper-evident against accidental modification, not administrator-proof evidence.

### Release status

Source is public at [andrebarrocas/partitionguard](https://github.com/andrebarrocas/partitionguard).
A tagged package release, production deployment, user adoption and hosted
documentation are separate milestones. See [candidate release notes](docs/releases/0.1.0.md)
for acceptance and remaining work.
