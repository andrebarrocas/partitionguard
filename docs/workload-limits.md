# Workload limits

New manifests default to **100,000 rows per partition** and **1,000,000
rows per run**. Existing saved plans keep their explicit approved row limits.
Execution also rejects fingerprint inputs above **1 MiB per encoded row**
or **128 MiB per encoded partition**, including framing overhead. These
limits apply to the original source columns, not just the changed column.
Legacy plans exceeding the byte ceilings fail closed.

The verifier sorts at most 4 MiB or 16,384 encoded rows at a time and merges
sorted runs, preserving the original duplicate-sensitive SHA-256 byte for byte.
Temporary runs contain source values: use a private, encrypted local temporary
volume (set TMPDIR), and allow at least 256 MiB of free scratch per controller.
The files are anonymous/unlinked on supported POSIX systems and closed on
normal exit or errors. PostgreSQL uses driver row streaming; DuckDB and Delta
consume bounded Python batches. Plugin adapters without a streaming interface
can still buffer results, and are outside this memory envelope.

These limits are admission checks, not hard process RSS, database buffer,
statement scan, or network caps. Drivers can allocate an individual row and
database operators can buffer data before application checks. Use database
resource limits, execution budgets, indexed/physically partitioned data and a
controlled worker. Pilot on representative data, particularly with wide or
compressed payloads, before raising row counts.

## Measured DuckDB fingerprint workloads (2026-11-09)

One fresh macOS ARM64 measurement process per fixture, Python 3.11.14,
DuckDB 1.5.6, two database threads. Setup runs in a separate process. The
synthetic fixtures contain one date partition and three columns; wide text
repeats per-row hash text and is compressible. Values below are observed
single runs, not performance guarantees.

| Rows | Payload per row | Peak process RSS before → after | Wall time before → after | Rows scanned |
|---:|---:|---:|---:|---:|
| 250,000 | 32 B | 161 → 74 MiB | 1.19 → 1.50 s | 250,000 |
| 25,000 | 4 KiB | 523 → 274 MiB | 0.83 → 1.07 s | 25,000 |
| 100,000 | 1 KiB | 439 → 166 MiB | 1.13 → 1.50 s | 100,000 |

All three fingerprints matched the previous implementation. The trade-off
is more CPU and local I/O for lower peak process memory. Raw results include
process CPU, engine CPU, peak engine buffer memory and database storage
bytes read. Engine counters come from a separate EXPLAIN ANALYZE of the
source query after measurement, so storage bytes read were zero in the warm
engine cache; rows scanned and logical payload volume are still reported.
They do not measure physical cold I/O or total migration load. These tests do
not establish a PostgreSQL/Delta throughput or capacity envelope.

Reproduce with `python benchmarks/workload.py --rows 25000 --width 4096`.
Raw results: [previous](https://github.com/andrebarrocas/partitionguard/blob/main/benchmarks/workload-before.jsonl) and
[bounded sort](https://github.com/andrebarrocas/partitionguard/blob/main/benchmarks/workload-after.jsonl).
