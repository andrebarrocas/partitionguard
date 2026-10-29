# Validation record

Recorded 2026-10-29 for the public-source 0.1.0 candidate maintained by
[André Barrocas](https://github.com/andrebarrocas).

For remote results, inspect the checks on the commit being reviewed in the
[CI workflow](https://github.com/andrebarrocas/partitionguard/actions/workflows/ci.yml).
Its matrix runs Python 3.11–3.13 with PostgreSQL 16; the job artifacts contain
JUnit test results and coverage, plus demonstration observations for Python 3.12.
The observations below are the dated local verification, not an assumed CI result.

## Complete suite

**263 passed, 3 skipped in 25.05 seconds.** Statement coverage is
**89.4%** (1780 of 1991 statements).
This is statement coverage, not a claim of exhaustive behavior coverage.
The original [2026-10-04 raw test report](test-results.txt) is retained as an
archive. Current machine-readable results are uploaded by CI for each revision.

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
Verification was repeated in a fresh virtual environment, resolving the current
dependencies allowed by the project rather than relying on the original installation.

| Dependency | Tested version |
| --- | --- |
| duckdb | 1.5.6 |
| psycopg | 3.3.6 |
| deltalake | 1.6.6 |
| pyarrow | 23.0.1 |
| pydantic | 2.14.1 |
| pytest | 9.2.0 |
| hypothesis | 6.170.13 |
| boto3 | 1.43.121 |
| hatchling | 1.33.0 |
| twine | 6.2.0 |

`requirements-tested.txt` records the full optional/development dependency
set for reproducibility. The package's declared ranges are broader than this
single tested local environment.

## Additional checks performed

- Ruff checks and formatting checks pass across source, tests, examples and benchmarks.
- All seven tutorials regenerate successfully from executed subprocesses; excerpts are labelled.
- The public-CLI showcase passes from an unrelated working directory. It verifies
  dry-run behavior, approval refusal, failure after a committed partition, resume,
  24 required checks, corruption detection, repair and original source preservation.
- Strict MkDocs builds pass. Generated documentation is excluded from source distributions.
- GitHub workflow files pass actionlint 1.7.12; action references use verified commit SHAs.
- Wheel/source builds and wheel metadata inspection pass. An isolated core-only
  wheel environment runs help/version, init, validate, diff, impact, plugin discovery,
  and both passing/failing CI plans without DuckDB, PostgreSQL, Delta or AWS SDKs.
- The benchmark script ran on synthetic 1,000/10,000-row tables. Raw results and
  methodology are in `benchmarks/`; no production throughput or Rust speedup is claimed.

## CI defect found and fixed

The earlier remote CI test jobs passed, but its quality job failed at
`twine check`: Hatchling emitted core metadata 2.5, which Twine 6 rejected.
Both distribution targets now explicitly select core metadata 2.4. Fresh
Hatchling 1.33.0 builds of the wheel and sdist pass Twine 6.2.0 validation.
The validator remains enabled in both CI and the release workflow.
See the [Hatch wheel configuration](https://hatch.pypa.io/latest/plugins/builder/wheel/)
for the supported metadata-version setting.

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

Source is public. No tagged package release, hosted documentation, live AWS
integration, Windows support, online migrations under arbitrary writers,
distributed ledger, exhaustive ODCS conformance, automatic consumer cutover,
destructive retirement, or production-scale performance is claimed. The local
fingerprint method is deliberately capped and materializes rows in memory.
External user validation and external security review remain adoption gates.
