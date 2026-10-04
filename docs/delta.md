# Delta Lake: bounded changes and historical evidence

The `delta` adapter uses real delta-rs transactions on a **local Delta table with a physical DATE partition column**. It supports adding replacement columns, populating a bounded date range, recovering interrupted work, and verifying snapshots. Install from this checkout with `python -m pip install -e '.[delta]'`.

The contract's table name is a logical SQL alias; the connection is the local directory containing the Delta table. A connection serves one table. Remote URIs are rejected: this release has no tested object-store coordination or Spark writer integration. The supported dependency floor is deltalake 1.6.6 and PyArrow 23; tests exercise actual Parquet files and Delta logs.

## What changes on disk

Adding a nullable replacement column changes Delta metadata. A backfill uses one equality predicate on a physical DATE partition and assigns only the new representation. Matching partition files may be rewritten. Files in other date partitions are not rewritten by this operation, and the test suite checks their file identities. Physical partitioning is mandatory because a logical date predicate on an unpartitioned Delta table would not provide the promised file boundary.

All verification relations are backed by Arrow datasets pruned to the approved physical partitions before DuckDB receives them. Plan bounds, row caps, and the original-column fingerprints still apply. Reading the transaction log and file metadata is necessary; no claim is made that metadata work is proportional only to selected rows.

The conversion policy matches the core: same-type copies, strict signed ASCII string-to-integer conversion, INTEGER-to-BIGINT widening, and integer-to-string conversion. Fractional/exponent forms and surrounding whitespace are refused for numeric strings. Overflow fails the partition. Nested fields, precision-specific decimals, timezone-aware timestamps, arbitrary expressions, destructive schema changes, and protocol upgrades are outside this release. Naive timestamp copies require the existing `timestampNtz` table feature.

## Transactions and recovery

Each metadata addition or partition update is **one atomic Delta commit**. There is no transaction spanning all actions. The adapter's `transaction()` context accepts one mutation and cannot roll back a Delta action already committed before a subsequent exception.

The engine records a checkpoint only after the mutation returns and its parity check passes. If the process exits after the Delta commit but before checkpoint persistence, resume repeats the deterministic assignment. Rows already holding the expected value are excluded using `IS DISTINCT FROM`; an unchanged replay produces no additional Delta commit in the integration tests. Original source columns remain untouched.

A local file lock coordinates PartitionGuard writers using the same table path. It does not stop Spark or an unrelated delta-rs process. Every read checks the selected version against the latest version, every write disables automatic commit retries, and unexpected versions fail visibly. An explicit maintenance window remains required. Stop other writers before preparation and execution; this is not an online migration protocol.

## Historical inspection

The Python adapter exposes read-only snapshots and Delta history:

```python
from datetime import date
from partitionguard.adapters.delta import DeltaAdapter
from partitionguard.models import Bounds

bounds = Bounds(column="event_date", start=date(2025, 1, 1), end=date(2025, 2, 1))
with DeltaAdapter("/local/path/events") as current:
    current.configure_scope("events", bounds)
    print(current.evidence())  # version, table identity, atomicity
    print(current.history(limit=5))
    with current.snapshot(0) as historical:
        print(historical.schema("events"))
        print(historical.fetch("SELECT count(*) FROM events"))
```

The same `capture_baseline(snapshot, plan)` and `verify(snapshot, plan, baseline)` functions used by execution can inspect historical snapshots. Tests compare the source snapshot's original-column fingerprint with the persisted baseline and run the complete verification policy against the migrated snapshot. Missing historical files or unsupported protocols raise errors; they are never reported as passing evidence.

PartitionGuard does not vacuum, restore, or promise indefinite history retention. Its writes disable opportunistic log cleanup, but an external retention job can still remove files required for a historical read. Retain snapshots according to your recovery policy.

## Reproduce the adapter evidence

```console
python -m pytest tests/test_delta.py -q
```

The suite covers bounded file rewrites, snapshots, strict conversion, read-only behavior, row caps, local writer exclusion, competing native commits, process interruption between commit and checkpoint, resumed verification, and failed acceptance rules. It is local integration evidence, not a distributed-scale benchmark.

Implementation references: [delta-rs table operations](https://delta-io.github.io/delta-rs/latest/api/delta_table/), [table alteration](https://delta-io.github.io/delta-rs/api/delta_table/delta_table_alterer/), and [commit properties](https://delta-io.github.io/delta-rs/latest/api/transaction/).
