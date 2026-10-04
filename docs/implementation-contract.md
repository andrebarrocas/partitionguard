# Internal implementation contract (v0.1)

This document fixes module boundaries before implementation. Serialized plans are
versioned artifacts, not an external data-contract standard. ODCS is the contract input.

## Models (`partitionguard.models`)

All models use Pydantic v2, forbid unexpected execution fields, serialize via
`model_dump(mode="json")`. Tuples preserve immutability. `canonical_json(value)`
and `digest(value)` return stable sorted JSON and SHA-256.

* `Column(name, data_type, nullable=True, description="", enum=())`
* `Dependency(name, kind="product", upstream=(), columns=(), sla=False)`
* `Contract(product, version, owner, table, columns, primary_key=(), business_keys=(),
  partition_by=(), dependencies=(), quality=(), sla={}, retention={},
  compatibility="backward", metadata={}, unsupported=())`; `.column_map` property.
* `Check(kind, column=None, columns=(), reference=None, threshold=0.0,
  absolute_tolerance=0.0, relative_tolerance=0.0, sql=None, values=())`.
  Kinds: unique, not_null, null_rate, aggregate, freshness, sql, range, enum.
  Aggregate compares SUM(column) with SUM(reference), with tolerances;
  null_rate compares fraction against threshold; freshness threshold is seconds.
* `Bounds(column, start:date, end:date, max_partitions=366,
  max_rows_per_partition=1000000, max_total_rows=10000000)`;
  `.partitions()` yields date ISO strings, start inclusive/end exclusive.
* `Operation(source, target, data_type, transform="cast")` (copy or cast only).
* `MigrationSpec(bounds, operations=(), checks=(), estimated_bytes=None,
  require_nonempty=True, maintenance_window=True)`.
* `Change(kind, path, before=None, after=None, compatibility, reason)`.
* `Impact(direct=(), transitive=(), transformations=(), quality_checks=(), slas=(), unknown=())`.
* `Risk(level, reasons)`.
* `Action(id, kind, description, partition=None, operation=None)`.
* `Plan(format_version=1, adapter, current:Contract, proposed:Contract,
  changes=(), impact:Impact, risk:Risk, migration:MigrationSpec|None,
  actions=(), blockers=())`. `.id` is digest of entire serialized plan.
  No timestamps or connection secrets in content-addressed plans.

## Storage interface (`partitionguard.adapters`)

`open_adapter(name, connection, read_only=True)` returns an adapter. Connections
are user-supplied at execution, never saved in a plan or logs. Optional imports
occur only when opening an adapter. Built-in names: duckdb, postgres.

* `name`, `identity` (non-secret stable digest of target identity), `read_only`.
* `schema(table) -> dict[str,str]` normalized canonical types.
* `fetch(sql, parameters=()) -> list[tuple]`.
* `execute(sql, parameters=())` (reject writes when read_only).
* `transaction()` context manager with rollback on any BaseException.
* `lock(table)` context manager: PostgreSQL advisory lock or DuckDB connection
  writer exclusion; target source is quiescent by explicit operator assertion.
* `close()` and context manager.
* `placeholder` is `?` for DuckDB, `%s` for PostgreSQL.
* `quote(identifier)` safely quotes a qualified table or one column.
* `add_column(table, operation)` idempotent DDL, reject wrong existing type.
* `backfill(table, operation, bounds, partition)` deterministic bounded UPDATE.

No general arbitrary SQL execution command is exposed to the CLI.

## Verification (`partitionguard.verification`)

`capture_baseline(adapter, plan) -> dict`: schema, row counts and source-row
multiset SHA256 per partition, capped fetches (row cap + 1). Fingerprint includes
all original columns, excludes new targets; deterministic ordered canonical
serialization, never persists row values. Fail on any cap exceeded, unknown
schema, or unexpected pre-existing target at initial admission.

`verify(adapter, plan, baseline) -> dict`: `passed: bool`, `checks: list[dict]`
with check name, partition, passed, observed, expected, and summary. All mandatory
checks run: row count, original source fingerprint, additive target schema,
partition completeness, transform parity, source and target contract null/enum
rules, and primary-key uniqueness over entire bounded scope. Optional explicit
checks run too; no empty-policy success. No full-table row scan: scope is bounds.

## Execution (`partitionguard.engine`, `partitionguard.ledger`)

`Engine(adapter, ledger)` with methods:
* `dry_run(plan) -> dict`: read-only preflight, persist immutable plan + baseline
  and target identity as a PREPARED ledger run. Reject blockers.
* `approve(plan, digest, operator) -> dict`: require matching full digest,
  prepared baseline and operator; persist APPROVED.
* `apply(plan, operator, resume=False, maintenance_window=False,
  fail_after=None) -> dict`: require APPROVED, explicit maintenance window,
  target identity, baseline unchanged, then deterministic per-partition replay.
  `fail_after` is Python test hook only, never a CLI flag. Checkpoint only after
  target commit. Failure state FAILED/VERIFICATION_FAILED; COMPLETE only after
  mandatory policy passes. Resume requires previous approval and same plan and
  target, retries incomplete actions, validates completed work before skipping.
* `verify(plan) -> dict`: recheck saved baseline; failed evidence changes state.

`Ledger(path)` owns SQLite append-only hash-linked events, runs and checkpoints.
`get(plan_id) -> dict|None`, `history() -> list[dict]`, `events(plan_id) -> list[dict]`.
A local exclusive file lock prevents two processes writing one ledger. Target
advisory lock additionally coordinates independent ledgers. Local hash chains
are tamper-evident against accidental edits, not secure against an administrator
rewriting the entire database; retain external digests for stronger evidence.

## Plugins

Entry point groups partitionguard.contracts, partitionguard.storage,
partitionguard.lineage, partitionguard.execution, partitionguard.verification,
partitionguard.catalogs, partitionguard.queries. Explicit selection only;
API version 1, reject duplicate names or incompatible major API. Third-party
plugins are trusted executable Python and inherit process privileges.
