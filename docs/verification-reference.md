# Verification reference

Verification is required for completion. Execution without successful acceptance evidence stays failed, even if every database statement committed. Every result contains `passed`, individual checks with observed and expected values, and a summary. Driver errors become failed checks with error classifications; potentially sensitive failing values and connection strings are not copied into evidence.

## Mandatory checks

| Check | Meaning |
| --- | --- |
| Original schema | Every source column still exists with its canonical type. |
| Proposed schema | All and only the proposed columns exist with expected types. |
| Row count | Each bounded partition has its baseline count. |
| Source fingerprint | Each partition's original-column multiset matches its saved baseline. |
| Partition completeness | Every declared date exists when `require_nonempty` is true. |
| Transformation parity | Every replacement equals the approved cast/copy expression, including null behavior. |
| Null and enumeration rules | Source and proposed contracts' supported column rules pass. |
| Primary/business keys | Declared key tuples are non-null and unique across the complete bounded scope. |
| Required policy | Both contracts' executable quality checks and migration checks pass. |
| Runtime identity | Core, driver, and applicable database versions match baseline admission. |

The original fingerprint includes every original column, not only keys or changed fields. It is independent of row ordering and preserves duplicate multiplicity through sorted, length-prefixed SHA-256 input. Type tags distinguish values such as integer `1`, text `"1"`, and boolean `true`; dates, timestamps, and IEEE nonfinite values serialize deterministically. The ledger stores hashes and counts rather than source rows.

The fingerprint is an exact integrity check for the bounded original population. It does not provide a durable data snapshot or restore deleted data. Keep source writers quiescent while using this release. Fingerprints and counts cannot prove unknown business semantics. Key validation is scoped to the approved bounds; it does not certify uniqueness in unscanned history.

The baseline also records the core version and installed adapter driver versions; PostgreSQL includes its server version. A runtime change refuses resume and fails verification against the old baseline. Preserve the original environment to recover, or investigate and create a new reviewed plan. This signature detects version changes; it does not attest that an installed package's bytes are unmodified.

Missing partitions fail by default. With `require_nonempty: false`, empty partitions are permitted deliberately; this is recorded in the plan. Baseline schema/cap evaluation errors stop verification before further policy scans. Failures remain evidence, not a default success.

## Optional checks

| Kind | Fields | Passing condition |
| --- | --- | --- |
| `unique` | `column` or `columns` | No repeated tuple across the bounded scope. Null is a group value; use `not_null` if required. |
| `not_null` | `column` | Zero nulls. |
| `null_rate` | `column`, `threshold` | Null fraction is at most threshold, in `[0,1]`. Empty scope fails. |
| `null_rate` comparison | Also `reference` | Absolute difference in null fractions is at most threshold. |
| `aggregate` | `column`, `reference`, tolerances | Difference between sums is at most `absolute_tolerance + relative_tolerance * abs(reference_sum)`. |
| `enum` | `column`, `values` | Every non-null value belongs to the allowed set. Null policy is separate. |
| `range` | `column`, `values: [lower, upper]` | Every value is non-null and within inclusive finite bounds. |
| `freshness` | `column`, `threshold` seconds | Most recent value is not in the future and its age is at most threshold. Empty/all-null scope fails. |
| `sql` | `sql` | One bounded scalar result, exactly boolean true. Integer `1` is insufficient. |

Aggregate sums are compared using decimal arithmetic on returned numeric values so integer differences above floating-point precision are not lost. Floating-point aggregation still inherits the database's summation behavior; configure semantic tolerances rather than assuming bit-for-bit equality. Empty or all-null sums and nonfinite aggregates fail. Freshness evaluates against UTC wall-clock time; a timezone-naive timestamp is interpreted as UTC and dates as UTC midnight. Results can change when time advances; no random statistical sampling is used.

Distribution tests, p-values, inferred tolerances, arbitrary statistical engines, and exact schema nullability-constraint installation are not implemented. Supported nullability is checked as a data requirement over the bounded range. A nullable additive target outside that range is intentional.

## Restricted custom SQL

```yaml
checks:
  - kind: sql
    sql: SELECT COUNT(*) > 0 AND SUM(carrier_code_v2) >= 0 FROM {scope}
  - kind: aggregate
    column: amount_v2
    reference: amount
    absolute_tolerance: 0.01
    relative_tolerance: 0.000001
```

`{scope}` must occur exactly once as the sole table reference. It expands to a subquery over the approved table and date bounds. The parser accepts one `SELECT` expression. No joins, nested queries, CTEs, window functions, sampling, write statements, locking clauses, external tables, or arbitrary functions are accepted. Aggregate/scalar function support is limited to `COUNT`, `SUM`, `AVG`, `MIN`, `MAX`, `ABS`, `COALESCE`, `NULLIF`, `CAST`, `ROUND`, `FLOOR`, `CEIL`, `LENGTH`, `LOWER`, and `UPPER`; casts must use supported canonical types. A result with zero rows, multiple rows, null, or a non-boolean value fails.

This deliberately limited assertion surface preserves scope and reviewability. It is not an SQL sandbox for untrusted tenants. Operators trust the database and installed plugins. Unsupported syntax fails closed and may need an explicit verification plugin in a future release.

## Recovering a failed verification

Inspect failed check names and observations, correct the cause, and resume the same approved plan only if its original source baseline remains unchanged. A corrupted replacement can be repaired by replaying the deterministic assignment; changed original rows require investigation and a new approved plan, not overwriting the stored baseline. Read-only re-verification records new evidence and retains past failures.
