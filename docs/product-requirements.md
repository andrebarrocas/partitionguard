# Product requirements

**Definition:** Safely plan, execute, recover, and verify changes to production data products.

This document specifies the product. It does not claim every requirement is implemented. The README capability matrix, test suite, and release notes describe the executable scope of each release.

## User and problem

A data platform engineer must change a production dataset without losing track of affected consumers, partially written historical partitions, or the evidence needed to prove completion. Today the contract review, backfill script, scheduler, quality checks, and incident record are often disconnected. PartitionGuard joins those activities around a durable, reviewable migration plan.

The product's primary artifact is an executable migration and verification plan. Schema comparison, dependency declarations, and contract validation supply evidence to that plan. They are not a replacement catalog or contract standard.

## First useful scope

1. Read supported ODCS v3 contract fields and a documented local configuration format.
2. Compare contract changes using deterministic, adapter-aware compatibility rules.
3. Traverse explicitly declared dependencies and explain direct and transitive impact.
4. Produce immutable, content-addressed plans with bounded partition workloads, required checks, and manual actions.
5. Execute supported additive column migrations and bounded backfills against DuckDB and PostgreSQL.
6. Record approvals, attempts, checkpoints, failures, and verification in a durable SQLite ledger.
7. Resume failed work by replaying idempotent assignments under the same approved plan.
8. Require deterministic verification before completion.
9. Exercise a focused Delta Lake adapter after the relational core works.

Source columns remain present during a rename or incompatible type migration. Automatic source removal, writer deployment, consumer code changes, online dual writing, and implicit cutover are outside the initial executable scope. An explicitly additive staged target can complete after its own verification. A required manual action for a broader target is recorded as blocking until there is an explicit, auditable acknowledgement supported by the release. A descriptive plan step is never represented as an executed operation.

## Functional requirements

| ID | Requirement | Acceptance evidence |
|---|---|---|
| R1 | Contracts normalize product identity, version, ownership, columns, keys, partitioning, dependencies, quality, SLA, retention, and policy without silently treating unsupported semantics as enforced. | Adapter tests cover normalization, diagnostics, and unsupported requirements. |
| R2 | Identical inputs and rule versions produce identical analysis and plan identities. | Ordering and round-trip tests; no clock/operator values in content identity. |
| R3 | A plan reports compatibility, impact, risk reasons, operations, bounds, verification, and blocking reasons. | CLI text and JSON integration tests. |
| R4 | CI fails for unhandled breaking changes, policy violations, absent migration coverage, or absent required verification. | Exit-code tests with positive and negative fixtures. |
| R5 | Mutations require an explicit execute mode and approval of the exact plan digest. | Dry-run and changed-plan rejection tests. |
| R6 | Every backfill declares a finite half-open date range, a partition column, and limits on execution. | Empty, reversed, oversized, and absent-bound tests. |
| R7 | Execution refuses unsafe source drift, unsupported operations, unsafe identifier/expression input, and capabilities the adapter cannot guarantee. | Adapter safety tests. |
| R8 | Failure after a target commit but before a ledger checkpoint is recoverable without duplicating effects. | Injected crash-window and resume tests. |
| R9 | A migration cannot be complete with a failing/missing required check or unresolved required manual action. | Lifecycle transition tests. |
| R10 | Evidence identifies plan, operator, product versions, attempts, actions, partition checkpoints, errors, verification, and timing. | Ledger persistence and history tests. |
| R11 | CLI commands have stable JSON output and distinguish invalid input, blocked safety policy, execution failure, and verification failure. | CLI contract tests and documented exit codes. |
| R12 | A contributor can implement an adapter without adding its vendor SDK to the core dependency set. | Plugin registration and conformance tests. |

## Safe execution assumptions

The first executor requires the source and relevant writers to be quiescent for the approved bounded workload. The operator arranges that freeze; a command-line acknowledgement is not proof that an external writer stopped. Preflight and postflight baseline reconciliation detect observable drift and fail closed. Adapter transactions and source checks reduce risk but do not establish an online, cross-system consistency protocol.

Plans must distinguish measured facts from estimates and unknowns. Missing external lineage means incomplete lineage, not zero consumers. Logical schema compatibility does not prove arbitrary consumer SQL remains correct. Version metadata is not proof of current physical state.

## Explicit non-goals

No data catalog, task scheduler, universal SQL rewrite engine, full ODCS validator, autonomous consumer migration, access-control platform, or LLM-based safety judgment. No automatic rollback promise: retaining source data and resuming forward are separate from restoring every external system. No Rust without a benchmark identifying a material bottleneck. No claim of exactly-once execution across a database and an external ledger.

## Release gate

A release needs reproducible tutorials using captured output, packaging checks, a complete passing local test suite, the declared adapter integration matrix, recovery tests, documented limits, and truthful release notes. A skipped environment-dependent integration test must be disclosed separately from a passed test. A public registry release additionally needs an account, repository provenance, and a confirmed available name; creating release automation alone is not publication.
