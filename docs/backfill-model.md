# Backfill model

A backfill is a finite set of typed assignments applied to an explicitly bounded dataset scope, with checkpoints and required verification. Partition planning is a first-class plan artifact.

## Required input

- Adapter and physical table identity.
- Source and replacement columns and a supported deterministic operation.
- Date-compatible partition column and inclusive start/exclusive end.
- Maximum partitions/batches and optional enforceable row/byte limits.
- Required source baseline, execution consistency assumptions, and verification policy.

The initial date model uses half-open ranges: `[2026-01-01, 2026-01-04)` contains January 1, 2, and 3. Date/timestamp boundary semantics must be explicit; adapters must reject ambiguous coercions. Empty, reversed, missing, invalid, or excessive ranges fail before mutation. No default expands to all historical data.

## Planning

Enumerate partition boundaries deterministically; assign stable partition/action IDs; group only within the configured batch limits. Include expected/observed partition presence, source row estimates or measurements, checkpoints, retry strategy, and check requirements. Estimates are labelled as estimates and carry provenance. Contract metadata is not a measured table size.

The initial implementation may use one date partition per batch. That is preferable to a configurable grouping mechanism with untested recovery semantics. Row/byte budgets must be measured/enforced if presented as hard limits. A date bound limits scope but does not by itself guarantee a small scan or short runtime on an unindexed table.

Null partition keys and rows outside the requested range remain outside execution scope. If the migration requires all rows to be populated, preflight must prove coverage or block the broader migration. A successful bounded backfill is not proof of a complete whole-table migration.

## Execution and idempotency

For each partition, preflight reconciles required source evidence, the adapter begins its supported transaction, applies the assignment, runs required batch checks, commits, and only then writes a durable checkpoint. A transaction failure rolls back that batch where supported. Prior committed partitions remain committed.

The supported idempotency strategy is replacement assignment from a frozen source, for example `replacement = CAST(source AS INTEGER)` restricted by the partition predicate. The cast/type/identifier must be adapter-approved. Incrementing values, appending without deduplication, nondeterministic functions, and arbitrary SQL are unsupported.

After a commit/checkpoint crash window, repeating the same assignment is safe only if the source baseline still matches. A resume command explicitly names the original plan/run and approval; it does not recalculate historical boundaries from the current date.

## Completion and cancellation

All planned partitions must be checkpointed and reconciled. Missing partitions must have an explicit policy: an empty partition can be allowed only when the plan and verification policy say so. Physical file existence alone is not logical partition completeness.

Final required checks include source stability, row/key/null invariants as applicable, value/aggregate parity, and expected partition coverage. Cancelled or failed runs retain committed work and audit evidence. Cancellation stops future batches; it is not rollback. Retry budgets bound repeated failures and remain visible in history.

## Limits to document per adapter

State whether a partition predicate prunes storage; whether a mutation rewrites files or scans the table; transaction boundaries; supported partition types; source-evidence strength; concurrency/conflict behavior; and any maintenance/vacuum effects. Never present a bounded logical predicate as a guarantee of bounded physical I/O without evidence.
