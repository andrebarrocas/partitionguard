# Changelog

Changes follow semantic versioning. This file records implemented behavior; roadmap entries are not released features.

## Unreleased

### Fixed

- Select core metadata 2.4 for both distributions so the build remains compatible
  with the Twine 6 publication validator. The previous CI failure rejected
  metadata 2.5; strict validation remains enabled.

### Added

- Portable HTML/JSON migration evidence snapshots with ledger integrity checks,
  filtered timeline/checkpoint/verification evidence, escaped self-contained HTML,
  explicit evidence boundaries and refusal to overwrite existing files.
- Three real SIGKILL integration cases covering an open transaction, the
  commit/checkpoint gap, and a durable checkpoint, followed by fresh-CLI recovery.
- An executable crash/recovery/corruption/repair demonstration and downloadable
  CI evidence artifacts. Report regressions cover lifecycle accuracy, filtering,
  HTML injection, corrupted ledgers, and safe CLI file handling.
- Distribution regression tests that build and validate the actual wheel and
  source archive, including the CLI entry point, license and shipped engine.
- CLI regression tests for recovery after a failed partition and refusal to
  execute a changed plan using an earlier approval.
- JUnit and coverage artifacts in CI, execution of all seven tutorials, and a
  live CI badge for reviewing the current branch's results.

### Changed

- Replace the README heading with "Supported capabilities" and refresh the
  verification guidance for presenting reproducible results.

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

The package and workflows are prepared for maintainers. No public repository, package publication, production deployment, user adoption, or hosted documentation is implied. See [candidate release notes](docs/releases/0.1.0.md) for acceptance and remaining work.
