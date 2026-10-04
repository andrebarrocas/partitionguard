# SQL adapter support and safety boundaries

The DuckDB and PostgreSQL adapters execute additive column stages and deterministic bounded updates over existing tables. They do not deploy producer changes, dual writing, consumer cutover, indexes, or legacy-column retirement. A migration's `COMPLETE` state means its approved bounded stage passed verification; it does not mean every consumer has moved to a final contract.

| Capability | DuckDB | PostgreSQL |
| --- | --- | --- |
| Target | Durable local database file, base table | Existing ordinary table |
| Default connection | Native read-only | Read-only transactions |
| Atomic action | Database transaction | Database transaction |
| Work bound | One existing `DATE` column, half-open daily range | Same |
| Recovery | Repeat assignment from unchanged source; reconcile each checkpoint | Same |
| Executor exclusion | Native process writer exclusion plus process-local lock | Session advisory lock on resolved relation identity |
| Timeout | No portable statement timeout imposed | 30-second statement timeout, 5-second lock timeout |
| Namespace | Simple table or schema/table names | Simple table or schema/table names |

PostgreSQL views, foreign tables, inheritance/native partition trees, row-level security, rewrite rules, and user-defined triggers are rejected during schema admission. These structures can change the population being verified or introduce effects that this release cannot prove. The date partitions in this release are logical row ranges; they do not require native PostgreSQL partitioning. DuckDB views and remote/external sources are not execution targets. External access is disabled on its connection.

Supported canonical types are `STRING`, `INTEGER`, `BIGINT`, `DOUBLE`, `BOOLEAN`, `DATE`, and timezone-naive `TIMESTAMP`. Known engine aliases normalize to these names. Parameterized types, nested types, decimals, timezone-bearing timestamps, and unrecognized native types are rejected rather than approximated. `STRING` is unbounded text; bounded `VARCHAR(n)` is not advertised as compatible.

## Conversion policy

Supported operations are same-type copies/casts, `STRING` to `INTEGER`/`BIGINT`, `INTEGER` to `BIGINT`, and `INTEGER`/`BIGINT` to `STRING`. Other pairs are blocked pending explicit semantics and tests. Integer strings must match `[+-]?[0-9]+`; whitespace, exponent notation, decimal strings, and non-ASCII digits fail. The database cast still checks numeric range. Nulls are preserved. Leading zeroes are intentionally lost when converting to an integer and must be acceptable for the chosen business field.

Preflight evaluates conversions within the approved range before any destination change. Final verification compares each target value with the database's planned expression using null-safe comparison. Assignment is deterministic from original source columns; repeated batches do not append duplicate rows. This depends on the operator freezing relevant writers for the complete stage.

## What the bounds guarantee

Both boundaries are required; the start date is inclusive and the end date exclusive. The plan caps partition count, rows per partition, and total rows. Baseline reads fetch at most each partition's cap plus one and refuse an excess. Updates repeat the per-partition cap check and always include the approved date predicate. Added columns affect the table's schema; out-of-range rows retain null replacement values.

These limits constrain logical rows and client memory, **not physical bytes scanned**. Without indexes or pruning, a database may scan storage outside the logical range to evaluate a predicate. Large tables require appropriate physical design and workload review. Estimates are not metered resource guarantees. Baseline fingerprints sort bounded row encodings in memory; very large scopes are not a streaming verification claim.

## Recovery and concurrency

One transaction covers one DDL action or one operation/partition update. A checkpoint follows its commit. A crash between commit and checkpoint leaves an uncertain action that is repeated safely; an existing replacement column must match the intended type, and the engine requires evidence that this run previously attempted its creation. Completed batches are verified before being skipped. No distributed transaction joins the target database and SQLite ledger.

PostgreSQL advisory locks coordinate cooperating PartitionGuard clients; they do not stop arbitrary producers. Its identity is derived from the resolved server endpoint and database without persisting credentials. DuckDB relies on native file access rules plus an in-process guard. Use one canonical destination path/endpoint and a maintenance window. Neither adapter guarantees online migrations with concurrent external writers. A detected source change refuses continuation instead of silently accepting a new baseline.

The Python adapter API is trusted code. Its internal SQL methods are not a public query service. CLI execution exposes only generated operations; custom verification SQL passes a separate restricted parser. Database privileges and administrator-defined functions remain part of the deployment trust boundary.

## Reproducing PostgreSQL integration coverage

Use an expendable PostgreSQL database where the configured user can create and drop tables. The tests create randomly named tables and clean them up:

```bash
export PARTITIONGUARD_TEST_POSTGRES='host=127.0.0.1 port=5432 dbname=partitionguard_test user=partitionguard'
python -m pytest tests/test_adapters.py tests/test_verification.py tests/test_postgres_engine.py
```

Without that variable the PostgreSQL cases are explicitly skipped. They are not mocked and are not counted as proof of live integration when skipped. Tests cover approval, failed execution, closing both connections and ledger, resume, the commit/checkpoint gap, target corruption, failed re-verification, and repair through deterministic replay.

Relevant backend semantics: [DuckDB concurrency](https://duckdb.org/docs/lts/connect/concurrency), [DuckDB Python connections](https://duckdb.org/docs/current/clients/python/dbapi), [PostgreSQL advisory locks](https://www.postgresql.org/docs/current/explicit-locking.html#ADVISORY-LOCKS).
