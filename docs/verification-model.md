# Verification model

Verification is a deterministic policy gate. A completed write is not a completed migration. Every required check must return PASS, with evidence for the approved scope, before the migration can be complete.

## Check representation

A check records a stable ID, kind, scope, parameters, expected/baseline values, absolute/relative tolerance where appropriate, required status, and backend capability. Results record observed values, PASS/FAIL/ERROR, reason, duration, and evidence time. Unknown or unsupported required checks block execution; runtime check errors fail verification.

The plan digest binds the verification policy. Lowering a threshold after a failure creates a different plan; a successful rerun does not erase failed evidence.

## Verification families

| Check | Semantics and limits |
|---|---|
| Row count | Compare the same bounded logical population before/after. Count equality does not prove value equality. |
| Key uniqueness | Check duplicate key groups and null-key policy; composite keys need explicit null semantics. |
| Null rate | Compare numerator and denominator for the same population; empty-scope behavior is explicit. |
| Schema | Verify required replacement type/nullability and approved retained source fields. Do not claim full final-schema equality during additive migration. |
| Partition completeness | Check every expected partition according to empty/missing-partition policy; detect unintended scope gaps. |
| Conversion validity | Reject unconvertible values and overflow; do not silently count failed conversions as valid nulls. |
| Value/aggregate parity | Compare the intended source transformation with replacement values. Aggregate agreement alone can hide row-level errors. |
| Quality rules | Evaluate supported normalized rules and preserve clear diagnostics for unsupported rules. |
| Freshness | Compare a measured data timestamp with an explicit reference instant and maximum age. The current backend evaluates against current UTC at each verification; freshness can therefore expire on a later run. The ledger timestamps the evidence. |
| Custom SQL assertion | Only when a backend provides a restricted read-only execution boundary and validates the returned result shape. Arbitrary SQL is not safe merely because it starts with SELECT. |
| Statistical threshold | Apply an explicitly selected deterministic metric and threshold; document sample population/seed if sampling exists. |

This table defines the desired families, not a claim that each backend implements all of them. The CLI must report actual support. The first migration path prioritizes exact source/replacement parity, counts, keys/nulls, schema, and partition coverage. Unsupported custom SQL must be rejected until a credible read-only boundary is available.

## Tolerance rules

Exact integer/key/schema invariants use exact equality. Floating-point and semantically lossy transforms may use explicit absolute and relative tolerances, for example `abs(actual - expected) <= abs_tol + rel_tol * abs(expected)`. Zero, negative baselines, non-finite values, nulls, decimals, and empty populations require tests. Do not silently supply a permissive tolerance. Tolerances express approved domain semantics, not a way to dismiss unexpected differences.

## Baseline and concurrency

Capture baseline evidence before modifying the intended scope and persist it with the run. The initial execution contract freezes relevant source writes. Recheck source evidence on resume and before completion. A source drift failure is distinct from replacement-value mismatch and cannot be fixed by overwriting the stored baseline.

Verification should execute under a suitable adapter transaction/snapshot where available. Independent queries against changing sources do not establish a common point-in-time result. The first release must state this limitation instead of advertising online migration verification.

## Completion gate

Completion requires all planned executable actions, all partition checkpoints and reconciliation, all required verification results, source consistency, and all required manual acknowledgements. An explicitly additive staged target can complete without claiming final cutover or source retirement. Optional warnings remain visible. Pending downstream cutover produces an explicit blocked state even when automated checks pass.

The evidence proves only the stated checks and scope. It does not prove undeclared business meaning, absent consumers, or the correctness of external deployment steps.
