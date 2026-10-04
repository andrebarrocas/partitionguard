# Local validation record

Recorded 2026-10-04 for the unpublished 0.1.0 candidate.

## Complete suite

**262 passed, 3 skipped in 6.47 seconds.** Statement coverage is
**89.4%** (1780 of 1991 statements).
This is statement coverage, not a claim of exhaustive behavior coverage.
The [raw test report](test-results.txt) includes per-module results.

The three skips are DuckDB instances of PostgreSQL-specific tests for row
security, rewrite rules and function overload resolution. The corresponding
PostgreSQL cases ran and passed. PostgreSQL integration was executed against an
isolated local PostgreSQL 14.20 server; DuckDB and Delta used real local tables.
AWS tests used botocore stubs and are not live-cloud validation.

```sh
PARTITIONGUARD_TEST_POSTGRES='host=127.0.0.1 port=55439 user=partitionguard dbname=postgres' \
  python -m pytest --cov=partitionguard --cov-report=term-missing -q
```

The connection above is a disposable test instance, not an application default.
Use your own disposable database. Environment: Python 3.11.14, Darwin arm64.
The CI matrix is configured for Python 3.11–3.13 and PostgreSQL 16 but has not run
on GitHub as part of this local delivery.

| Dependency | Tested version |
| --- | --- |
| duckdb | 1.5.6 |
| psycopg | 3.3.6 |
| deltalake | 1.6.6 |
| pyarrow | 23.0.1 |
| pydantic | 2.13.5 |
| pytest | 9.1.1 |
| hypothesis | 6.168.3 |
| boto3 | 1.43.108 |

`requirements-tested.txt` records the full optional/development dependency
set for reproducibility. The package's declared ranges are broader than this
single tested local environment.

## Additional checks performed

- Ruff checks and formatting checks pass across source, tests, examples and benchmarks.
- All seven tutorials regenerate successfully from executed subprocesses; excerpts are labelled.
- Strict MkDocs builds pass. Generated documentation is excluded from source distributions.
- GitHub workflow files pass actionlint 1.7.12; action references use verified commit SHAs.
- Wheel/source builds and wheel metadata inspection pass. An isolated core-only
  wheel environment runs help/version, init, validate, diff, impact, plugin discovery,
  and both passing/failing CI plans without DuckDB, PostgreSQL, Delta or AWS SDKs.
- The benchmark script ran on synthetic 1,000/10,000-row tables. Raw results and
  methodology are in `benchmarks/`; no production throughput or Rust speedup is claimed.

## Safety regressions exercised

Plan digest/content tampering; nested validation bypass; missing/invalid bounds;
source drift despite equal counts; pre-existing unrelated replacement columns;
blocked destructive/in-place changes; row limits; strict numeric conversion;
transaction interruption; commit-before-checkpoint interruption; persisted resume;
checkpoint reconciliation; concurrent controller exclusion; target/runtime mismatch;
failed mandatory checks; corruption after COMPLETE; restricted custom SQL and
PostgreSQL overloads; ledger hash/state/checkpoint/run-identity/timestamp tampering; Delta conflicts,
physical partition scoping and historical reads; plugin version/profile validation;
lineage identity collisions; bounded cloud inventory pagination.

## Not established

No public package/repository release, remote CI run, hosted documentation, live
AWS integration, Windows support, online migrations under arbitrary writers,
distributed ledger, exhaustive ODCS conformance, automatic consumer cutover,
destructive retirement, or production-scale performance is claimed. The local
fingerprint method is deliberately capped and materializes rows in memory.
External user validation and external security review remain adoption gates.
