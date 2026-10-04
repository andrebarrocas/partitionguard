# Compatibility rules

Compatibility is deterministic analysis of a change under an adapter and declared policy. It is distinct from execution risk and from permission to execute. A logically safe change can still carry a large backfill risk.

## Rule output

Every decision contains the change path, kind, classification, stable rule identifier, explanation, and any missing prerequisite. Results are ordered deterministically. Unknown or unsupported semantics produce a visible diagnostic; they must never default to safe. Policy can be stricter than a baseline rule but cannot turn an unsupported operation into an executable one.

| Change | Baseline classification | Required handling |
|---|---|---|
| Add a nullable column | SAFE | Confirm adapter type support and no stricter policy. |
| Change descriptive metadata | SAFE | Preserve metadata; no physical migration. |
| Add a non-null field | BREAKING without validated strategy; WARNING with one | Backfill/default strategy and null check. A nonempty string describing a strategy is not proof it works. |
| Drop a column | BREAKING | Explicit destructive workflow; unavailable in initial executor. |
| Rename a column | BREAKING | Explicit source/target mapping; no heuristic rename execution. Additive shadow migration is the initial route. |
| Change a type | Adapter-aware | Proven widening can be SAFE/WARNING; narrowing or incompatible change is BREAKING. CAST availability does not prove compatibility. |
| Nullable to non-null | BREAKING unless policy explicitly permits a validated migration | Null check and writer/consumer coordination. |
| Non-null to nullable | Policy-aware, at least WARNING when consumers rely on non-null | Consumer compatibility review. |
| Primary/business key change | BREAKING | Uniqueness, null, and consumer migration requirements. |
| Remove permitted enumeration values | BREAKING | Check existing values and consumer assumptions. |
| Tighten quality threshold | WARNING or policy failure | Verify proposed threshold against data. |
| Change partition column, granularity, or layout | WARNING/BREAKING according to adapter | Explicit bounded physical strategy; no implied table rewrite. |
| Destructive partition change | BREAKING | Unsupported until a dedicated implementation exists. |
| Tighten SLA/freshness | WARNING | Capacity/freshness evidence and impacted consumers. |
| Shorten retention | BREAKING when data/consumer commitments are removed | Explicit retention policy review; no implicit deletion. |

The implemented rule set may be narrower than this design matrix; unsupported rows must remain visible. Type aliases are normalized only when the adapter guarantees their meaning. Decimal precision/scale, timestamp timezone behavior, signedness, nested types, and text-to-number parsing cannot be reduced to string-name similarity.

## Risk

Risk is the maximum applicable deterministic severity, with a list of reasons:

- **LOW:** safe metadata/nullable additions without migration work or material downstream impact.
- **MEDIUM:** warnings, bounded supported migration work, or declared consumer exposure.
- **HIGH:** breaking changes, substantial bounded backfills, high-value downstream/SLA exposure, or a manual cutover requirement.
- **BLOCKING:** absent required bounds, unresolved operation mapping, unsupported required capabilities, missing verification, violated policy, or unreconciled source drift.

Numerical thresholds such as maximum partitions or estimated bytes are configuration, included in plan identity. An unknown size is not zero bytes. Approval resolves an approval gate; it never resolves a failed precondition or unsupported capability.

## Dependency uncertainty

Impact traversal reports the declared graph's direct and transitive consumers once each, handles cycles, and excludes the starting product from its own impacted-consumer set. A missing lineage source is reported as incomplete coverage. The engine does not infer undeclared SQL dependencies from plausible names.

## CI decision

CI fails when an unhandled breaking change, compatibility-policy violation, required migration without executable/manual coverage, or missing required verification is present. A migration plan that only enumerates prose steps cannot satisfy execution coverage. CI analysis approval is separate from production execution approval.
