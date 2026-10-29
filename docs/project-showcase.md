# PartitionGuard — André Barrocas

**A personal data engineering project by [André Barrocas](https://github.com/andrebarrocas).**

PartitionGuard addresses the operational part of changing an important dataset:
how to bound the work, approve the exact operation, recover after a partial
failure, and prove the result is acceptable. It provides an executable migration
procedure for datasets produced by existing systems.

The initial supported procedure adds a replacement column and backfills an
explicit date range. DuckDB, PostgreSQL, and local Delta Lake provide real storage
integrations. Contracts, lineage and schema comparison support that procedure.

## Engineering decisions you can inspect

| Decision | Why it matters | Evidence |
| --- | --- | --- |
| Plans have deterministic content digests | Approval cannot silently carry over to a changed operation or scope | [Migration model](migration-model.md) |
| Writes have explicit date and row bounds | A missing boundary cannot become an accidental historical rewrite | [Backfill model](backfill-model.md) |
| Source evidence is preserved across retries | Equal row counts alone cannot prove that the source stayed unchanged | [Verification reference](verification-reference.md) |
| Checkpoints follow target commits | A crash can occur between the target commit and the local ledger write; replay must reconcile that gap | [Architecture](architecture.md) |
| Verification is required for completion | A committed write can still violate the intended data policy | [Verification tutorial](tutorials/05-verification.md) |
| Adapters state their real atomicity | A native Delta commit does not provide relational rollback across a sequence of actions | [Delta adapter](delta.md) |

The [competitive review](competitive-landscape.md) discusses overlap with SQLMesh,
database migration tools, quality frameworks and orchestrators. The project does
not assume that planning or backfills are new inventions; its focus is the
reviewed, recoverable procedure and acceptance evidence.

## Reproduce the working demonstration

From a checkout on Linux or macOS with Python 3.11 or newer:

```sh
python -m pip install -e '.[duckdb]'
python examples/showcase.py
```

This runs the public CLI against eight synthetic rows. It checks that the dry run
leaves the schema unchanged and unapproved writes are blocked. A Python-only
fault-injection hook interrupts execution after the first committed partition;
the CLI resumes that persisted run. All three selected partitions then pass
verification, while two outside rows remain untouched.

Next, the demonstration deliberately corrupts a replacement value. Verification
must fail with exit code 5 and revoke the completed state. A further explicit
resume repairs the value and passes verification again. The original source
columns remain unchanged throughout.

The script reports observations only after checking these outcomes; it fails if
any expected safety property is absent. `--json` produces the same evidence for
automation. Temporary datasets are removed when the demo finishes.

## Inspect the verification evidence

The [CI workflow](https://github.com/andrebarrocas/partitionguard/actions/workflows/ci.yml)
tests Python 3.11, 3.12 and 3.13 with a real PostgreSQL 16 service, DuckDB tables,
and local Delta tables. It also checks formatting, package construction,
distribution metadata, a clean wheel installation, and documentation builds.
The Python 3.12 job runs the demonstration and all seven tutorials.

Download the `validation-python-*` artifacts from a completed run to inspect
JUnit test results and coverage. The Python 3.12 artifact also contains the
observed demonstration output. The [dated validation record](validation.md)
distinguishes local observations from remote CI evidence.

## Scope and project status

This is a functioning early open-source project, with source and commit history
in [andrebarrocas/partitionguard](https://github.com/andrebarrocas/partitionguard).
It requires a maintenance window with relevant writers frozen. Completion proves
the approved bounded additive stage; it does not deploy producers or consumers,
retire source fields, or prove production-scale performance. AWS inspection is
read-only and tested using service stubs, not a live AWS account.

The [roadmap](roadmap.md) and [phase review](phase-review.md) document remaining
work. Source publication, a package release, and production adoption are distinct
milestones.
