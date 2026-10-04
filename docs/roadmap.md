# Roadmap

This roadmap is a sequence of release gates, not a declaration that work is complete. See CHANGELOG and release notes for delivered behavior and current test evidence. PartitionGuard was selected after an initial registry/repository availability check; repeat the check at release time because availability is not a reservation.

## Phase 0 — differentiate and specify

Write problem statement, product requirements, competitive research, standards mapping, architecture, compatibility, migration/backfill/verification models, plugin design, repository structure, and safety ADRs. Review whether the wedge is executable/recoverable migration safety. Stop scope drift toward a catalog, general orchestrator, or contract validator.

**Gate:** clear one-sentence positioning, independently researched overlaps, explicit safety assumptions, and honest boundaries for the first executable use case.

## Phase 1 — contracts and deterministic change analysis

Implement contract loading, a documented ODCS subset, normalized schema diff, adapter-aware compatibility, CLI text/JSON, and validation diagnostics. Add schema/compatibility tests and valuable diff-invariant property tests.

**Gate:** stable output, explicit unsupported semantics, and no mutation path in analysis commands.

## Phase 2 — dependencies and impact

Implement explicit dependency nodes/edges, cycle-safe direct/transitive traversal, affected models/checks/SLAs, lineage completeness, and deterministic explained risk. Add graph invariants and impact tests.

**Gate:** missing lineage is reported as uncertainty; no inference masquerades as observed dependency.

## Phase 3 — plans, bounds, and evidence

Implement immutable content-addressed migration plans, bounded date partition plans, required checks/manual actions, approvals, and SQLite ledger. Define typed actions and recovery states before adding writes.

**Gate:** changed semantic content invalidates approval; invalid/unbounded/unsupported plans cannot reach execution.

## Phase 4 — useful execution and recovery

Implement transactional DuckDB/PostgreSQL additive shadow-column migrations, partition assignments, source reconciliation, verification, and persisted checkpoints. Exercise crash windows and safe replay, failed casts, data drift, and failed verification.

**Gate:** a real dataset can migrate, fail midway, resume, and produce reviewable evidence; no complete state bypasses required checks/manual gates. PostgreSQL integration evidence requires an actual server, not only mocks.

## Phase 5 — contributor-ready release candidate

Finish CLI exit codes/JSON, GitHub Actions plan checks, seven executable tutorials, packaging, license, contribution/security guidance, issue/PR templates, docs publishing, and release automation. Capture actual tutorial output and run packaging smoke tests.

**Gate:** installable wheel/source distribution, complete test report with explicit skips, reproducible tutorials, a support matrix, and candid release notes. Public release is a separate publication operation with configured repository and registry credentials.

## Phase 6 — one serious ecosystem adapter

Prefer Delta Lake via delta-rs for local, reproducible table/version inspection and controlled bounded migration/verification. Select only operations whose actual API and transaction behavior can be tested. Exercise version conflicts, replay, historical verification, and file-rewrite disclosures.

**Gate:** meaningful integration tests on real Delta tables; unsupported capabilities rejected; no list of shallow integrations.

## After the core is useful

- Optional lineage ingestion from OpenLineage/DataHub/OpenMetadata.
- AWS subsets chosen by executable user need and available integration environments.
- An explicit online writer/consumer cutover protocol, only with concurrency evidence.
- Tamper-evident external evidence export and authenticated approvals.
- More partition representations and a remote execution backend if users need them.
- Profile actual bottlenecks before considering Rust; compare reproducible implementations and publish measurements.
- Optional AI explanation may read deterministic results but never decide, approve, or execute safety-critical changes.

## Every phase

Run the complete applicable test suite, update documentation and CHANGELOG, remove dead code, review architectural complexity, write realistic release notes, and record anything deferred, untested, shallow, or dependent on unavailable infrastructure. Benchmarks run only for performance claims and report measured results with environment details.
