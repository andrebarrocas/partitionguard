# Changelog

Changes follow semantic versioning. This file records implemented behavior; roadmap entries are not released features.

## 0.2.0 candidate (unreleased)

- Measure DuckDB fingerprint workloads, bound verification sorting and bytes,
  and lower default row limits for new manifests without changing saved digests.
- Verify SSH signatures over exact prepared run and canary receipts in a
  controlled runner; restrict ledger permissions and support checked backups.
- Test minimum runtime dependencies and current environments. Exercise a shared
  adapter conformance scenario and rehearse 0.1.0 recovery without replacing its
  baseline or checkpoints. Version the SQLite schema and reject unknown versions.
- Independent security sign-off remains open.

### Fixed

- Preserve original connection failures when rollback or advisory-lock cleanup
  also fails; stop with explicit recovery guidance if failure evidence cannot be
  persisted to an unavailable ledger.
- Select core metadata 2.4 for both distributions so the build remains compatible
  with the Twine 6 publication validator. The previous CI failure rejected
  metadata 2.5; strict validation remains enabled.

### Added

- Approved canary scopes/checks, durable CANARY_PAUSED/CONTINUATION_APPROVED and
  PAUSED states, source/canary rechecks and hash-bound continuation reviews.
  Original full-scope verification remains mandatory for COMPLETE.
- Cross-process pause requests, graceful SIGINT/SIGTERM handling at committed
  action boundaries, per-attempt time budgets and PostgreSQL statement/lock
  timeout configuration. Paused CLI execution exits 7.
- Legacy plan-digest compatibility, controlled-execution reports that omit SQL,
  and an executable canary/pause/continue example with CI artifacts.
- Independent-process concurrency tests across PostgreSQL, DuckDB and Delta,
  including separate ledgers, process/session lock release and external source
  writers. Recheck frozen source evidence inside each partition assignment;
  retain mandatory initial/final verification and writer-freeze requirements.
- Infrastructure recovery coverage for real PostgreSQL client/session loss,
  owned-server restarts, a dropped COMMIT acknowledgement, and SQLite disk-full,
  write-denied and permission failures. CI requires disposable server binaries.
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
