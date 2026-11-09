# Validation record

Recorded 2026-11-09 for the public-source 0.2.0 candidate. Results below were
observed locally using the development environment created on 2026-10-31. For remote results on the revision
being reviewed, inspect the
[CI workflow](https://github.com/andrebarrocas/partitionguard/actions/workflows/ci.yml?query=branch%3Amain).
Its `test-results-python-*` artifacts contain JUnit test results and coverage.

## Complete suite

**346 passed, 3 skipped in 46.12 seconds.** Statement coverage is
**90.4%** (2287 of 2529 statements).
This is statement coverage, not a claim of exhaustive behavior coverage.
The minimum direct dependency profile passed 346 tests with the same three
skips in 43.49 seconds. The separate previous-wheel rehearsal passed all
three backends in 20.13 seconds. See [supported environments](supported-environments.md).
The security tests use real SSH signatures, including run/canary replay rejection
and restoring private signed approval evidence from a checked snapshot.
Independent security sign-off is still outstanding.

The original [2026-10-04 raw test report](test-results.txt) is retained as an
archive. Current machine-readable reports are uploaded by CI for each revision.

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
CI runs Python 3.11–3.13 with PostgreSQL 16. Its status must be checked for the
specific revision; configured jobs alone do not establish that a run passed.

| Dependency | Tested version |
| --- | --- |
| duckdb | 1.5.6 |
| psycopg | 3.3.6 |
| deltalake | 1.6.6 |
| pyarrow | 23.0.1 |
| pydantic | 2.14.2 |
| pytest | 9.2.0 |
| hypothesis | 6.170.14 |
| boto3 | 1.43.123 |
| hatchling | 1.33.0 |
| twine | 6.2.0 |

`requirements-tested.txt` records the full optional/development dependency
set for reproducibility. The package's declared ranges are broader than this
single tested local environment.

## Additional checks performed

- Thirty-three operational-control regressions cover verified canaries,
  hash-linked continuation approval, crash-before-review recovery, mandatory full
  final policy, cross-process pause requests, SIGINT/SIGTERM on DuckDB/PostgreSQL,
  duration budgets, actual PostgreSQL statement/lock timeouts, Delta reopen/
  continuation and legacy plan digests/CLI execution. An older November 5 ledger
  also exports successfully through the updated reporter.
- The canary/pause/continuation example executes successfully; its actual output
  is in [production operations](production-operations.md). CI publishes filtered
  `execution-controls` evidence. SQL inside canary checks is excluded from reports.
- Nine independent-process concurrency and external-writer cases pass across
  PostgreSQL, DuckDB and Delta, including target exclusion with separate ledgers,
  process/session death, foreign-column rejection and source drift during recovery.
- Thirteen infrastructure regressions pass against an owned PostgreSQL server
  and real SQLite quota/write-denied conditions, including an acknowledged-by-server
  COMMIT whose reply is deliberately dropped. See [operation boundaries](production-operations.md).
- Twelve earlier regression cases cover three real SIGKILL crash windows and nine
  evidence-report cases (including parameterized CLI cases). Reopened stores and
  a fresh public CLI verify recovery without Python cleanup. These are DuckDB
  process-crash tests, not power-loss or PostgreSQL server-restart tests.
- The executable evidence demonstration completed SIGKILL recovery, deliberate
  corruption detection, repair and three filtered HTML/JSON snapshots. CI runs
  it on Python 3.12 and publishes `migration-evidence` artifacts. See
  [evidence reports](evidence-reports.md) for actual output and evidence boundaries.
- Six earlier regression cases still pass: strict wheel/source publication validation,
  wheel entry point/license/engine integrity, source archive contents, persisted
  CLI recovery, and refusal to execute a changed previously approved plan.
- Ruff checks and formatting checks pass across source, tests, examples and benchmarks.
- All seven tutorials regenerate successfully from executed subprocesses; excerpts are labelled.
- Strict MkDocs builds pass. Generated documentation is excluded from source distributions.
- GitHub workflow files pass actionlint 1.7.12; action references use verified commit SHAs.
- Wheel/source builds and wheel metadata inspection pass. An isolated core-only
  wheel environment runs help/version, init, validate, diff, impact, plugin discovery,
  and both passing/failing CI plans without DuckDB, PostgreSQL, Delta or AWS SDKs.
- The newly built wheel also exports HTML evidence from an existing ledger in
  that core-only environment without any database SDK installed.
- The benchmark script ran on synthetic 1,000/10,000-row tables. Raw results and
  methodology are in `benchmarks/`; no production throughput or Rust speedup is claimed.

## Earlier package-validation failure and regression coverage

The failing CI quality job rejected the distributions' core metadata version
2.5. Both new publication-validation cases reproduced that failure before the
fix. The wheel and source targets now explicitly select metadata 2.4, which is
supported by the existing Twine 6 validator. Both cases pass with this setting;
strict publication checks remain enabled in CI and the release workflow.

The development extra now includes the build backend and validator so the same
tests run after the documented development installation. Distribution tests
build actual artifacts without installing packages or accessing a registry,
then inspect their metadata, license, CLI entry point, shipped engine and
exclusion of generated data.

The earlier 2026-10-31 full-suite result was 268 passed, 3 skipped, with 89.4%
statement coverage. The current result includes the additional crash/report tests;
timings are individual observations, not performance comparisons.

The three 2026-11-08 implementation checkpoints were 293 passed (infrastructure),
302 passed (concurrency), and 335 passed (operational controls), each with the same
three backend-specific skips. Each change was committed and pushed separately.

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
fingerprint method is capped and uses bounded local sort runs.
External user validation and external security review remain adoption gates.
