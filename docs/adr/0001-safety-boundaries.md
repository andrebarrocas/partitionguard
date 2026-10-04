# ADR 0001: Bounded additive execution and explicit recovery

- **Status:** Accepted as the initial design boundary
- **Date:** 2026-10-04

## Context

Production data changes span contracts, physical tables, writers, consumer deployments, historical partitions, and evidence. A first release cannot honestly provide distributed transactions across these systems. A general SQL runner with an approval prompt would not establish migration safety.

The project needs a small migration operation that is useful, executable, recoverable, and verifiable on real engines.

## Decision

1. Build the control plane and CLI in Python. Keep vendor SDKs behind optional adapters.
2. Use an established contract representation, initially a documented ODCS v3 subset. Unsupported semantics remain visible.
3. Bind exact plan content to approval through canonical serialization and a cryptographic digest. Changed operations, logical target table, bounds, or verification policy require new approval. Dry-run binds the physical connection identity and source/runtime baseline in the prepared ledger record before approval.
4. Start physical migration execution with additive shadow columns and deterministic copy/cast assignments over finite date partitions. Retain the source field.
5. Require quiescent writers and source-baseline reconciliation. Refuse drift; do not quietly adopt a new baseline.
6. Use per-batch adapter transactions where supported. Persist state and checkpoints in a separate SQLite ledger.
7. Acknowledge the commit/checkpoint gap explicitly: attempts are at least once. Safe replay depends on deterministic assignments and unchanged source evidence.
8. Require policy verification and all required manual acknowledgements before complete. Pending consumer/writer cutover remains blocked.
9. Keep destructive retirement, arbitrary migration SQL, distributed execution, and automatic online dual writing outside the initial executor.
10. Implement a focused real Delta adapter after relational semantics work. Do not add AWS breadth or Rust before a testable need.

## Consequences

The tool can safely prepare a replacement representation and backfill it without deleting the original. It can explain partial state and resume useful work. It does not complete a production consumer deployment by itself and does not guarantee rollback.

Writer freeze is an operational prerequisite. Baseline checks detect observed drift but cannot prevent every race from an external uncooperative writer. Single-controller locking is not distributed locking. The ledger is auditable local evidence, not tamper-proof attestation.

Some requested transformations must be reported as unsupported/manual. That is preferable to claiming generic migration safety with untested semantics. Future online execution or destructive retirement needs a separate ADR, adapter capabilities, and failure-injection evidence.

## Alternatives rejected

- **Arbitrary SQL plus approval:** cannot establish idempotency, bounds, or safe recovery automatically.
- **Exactly-once claim with external SQLite ledger:** false without an atomic cross-system protocol.
- **Full-table rewrite as a convenient default:** hides physical risk and defeats bounded execution.
- **Automatic rename/drop:** can invalidate undeclared consumers and removes recovery evidence.
- **LLM safety classification:** cannot provide deterministic, reviewable policy guarantees.
- **Generic orchestration platform:** expands scope without solving the specific verification/recovery boundary.
