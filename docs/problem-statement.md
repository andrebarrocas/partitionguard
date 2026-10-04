# Problem statement

Phase 0 design review — 2026-10-04. Working product name: PartitionGuard (original brief: DataGuard).

**Safely plan, execute, recover, and verify changes to production data products.**

A data platform engineer must change an important dataset while preserving its consumers' expectations. The change may affect an identifier's type, a column's name, historical values, a partition boundary, a business key, or a quality requirement. Identifying a schema difference is useful, but the operational problem begins after that difference is known.

The engineer needs a bounded procedure that says what will be written, how much work is permitted, which consumers require coordination, what happened before a failure, which work can safely resume, and what evidence is sufficient to accept the result. A successful SQL statement is insufficient evidence that a data-product migration succeeded.

## Intended users and initial job

The primary user is a data platform engineer maintaining relational datasets already produced by other systems. The initial job is to add a replacement representation, populate a deliberately selected historical range, recover interrupted work, and verify the result before consumer cutover. Engineers retain control of producers and consumers. The tool must represent external coordination as an outstanding requirement rather than pretend to update arbitrary applications.

The first useful scenario is a string-to-integer migration on a partitioned table. The old column remains available; conversion policy is explicit; partition bounds and row limits are checked; completed batches survive retries; conversion errors fail visibly; verification compares the migrated representation with a recorded baseline. The same machinery supports renames and bounded repairs.

## Failure modes worth solving

| Failure | Required behavior |
| --- | --- |
| An engineer accidentally omits a date boundary | Refuse execution before any data write. |
| A process dies after the database commits but before the local checkpoint is recorded | Reconcile a destination receipt or safely repeat an idempotent operation; never assume rollback. |
| The source changes between approval and application | Detect drift against the approved scope and stop, or use an explicitly supported snapshot. |
| A cast drops information or creates nulls | Fail conversion or parity policy with measured evidence. |
| A retry duplicates historical records | Prevent duplication through adapter-specific transaction and idempotency guarantees. |
| All actions finish but a required assertion fails | Remain in a failed verification state; never report completion. |
| Lineage is incomplete | Report known impact and its provenance; never imply complete discovery. |
| An operation needs an unsupported engine capability | Block it before execution with a specific explanation. |

## Why a separate tool can be useful

The ecosystem already has contracts, catalogs, lineage, transformations, quality assertions, schema migration histories, and backfills. SQLMesh, in particular, already plans and applies model changes with downstream backfills. The opportunity is narrower: a portable migration procedure for existing datasets whose production code is owned elsewhere, with approval bound to the exact operation, bounded execution, crash reconciliation, and mandatory migration-specific evidence. This is a product hypothesis, not a claim that competitors cannot implement it. See the [competitive review](competitive-landscape.md).

## Non-goals

- A new contract standard or catalog of every asset.
- A replacement transformation framework or continuously running scheduler.
- Automatic discovery of every consumer or inference of business meaning from SQL.
- Autonomous consumer cutover, dual-write deployment, destructive cleanup, or universal rollback.
- General guarantees of exactly-once execution across unrelated systems.
- An LLM deciding safety, authorizing actions, or inventing verification results.

## Evidence needed to earn production confidence

An initial release is useful only if a contributor can reproduce a real bounded migration, inject a mid-run failure, resume without duplicating work, inspect the ledger, and observe verification both passing and failing on deliberately corrupted data. Tests must include the commit/checkpoint crash window, rejected unbounded plans, stale approvals, deterministic plan identity, and verification errors. Documentation must distinguish tested adapters, externally gated integration tests, and future work. This evidence supports an early release; it does not justify a blanket production-ready claim.
