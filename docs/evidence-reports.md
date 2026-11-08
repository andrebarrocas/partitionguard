# Migration evidence reports

Export a portable snapshot for review, incident analysis, or a project presentation.
The exporter needs a valid plan and its original ledger, but no database connection
or optional adapter SDK. It checks the ledger event chain and persisted state
before writing anything. It does not add ledger events or change lifecycle state.

```sh
partitionguard report plan.json --ledger ledger.sqlite --output evidence.html
partitionguard report plan.json --ledger ledger.sqlite --format json --output evidence.json
```

HTML is a standalone document with embedded styling, no scripts, and no external
assets. JSON has `report_version: 1` and contains the same evidence. Both include
plan/run IDs, source and target versions, declared impact, risk, bounded scope,
operations, partition source counts, checkpoint timestamps, approval and execution
timeline, and the latest recorded final-policy verification. Partition-level
checks do not masquerade as final verification. Missing verification is `null`,
not a successful result. Failed attempts remain visible after successful recovery.

## Executable demonstration

From a development checkout with the DuckDB extra installed:

```sh
python examples/recovery_report.py --output /tmp/partitionguard-evidence
```

The directory must not exist. The script operates only on newly generated
synthetic data inside it. Actual output observed on 2026-11-05:

```text
SIGKILL after target commit: RUNNING; backfill checkpoint absent
Execution without explicit resume: refused (exit 6)
Fresh-process resume: COMPLETE; 4 checkpoints
Deliberate target corruption: verification failed (exit 5)
Explicit resume after corruption: COMPLETE; verification passed
Exported recovered, corrupted and repaired HTML/JSON evidence snapshots
```

Open `repaired.html` to review the final timeline, including the failed
verification and subsequent reconciliation. Compare it with `recovered.html`
and `corrupted.html`; these are separate snapshots of the recorded states.
Equivalent `.json` files and `transcript.txt` are generated alongside them.
The directory also contains disposable working data and the ledger. CI uploads
only the filtered reports and transcript as `migration-evidence`, not that database
or ledger. JUnit results remain in the separate `test-results-python-*` artifacts.

## What the crash tests establish

Three integration cases launch a separate DuckDB worker and send SIGKILL at a
synchronized barrier: inside a backfill transaction, after target commit before
checkpoint persistence, and after a durable checkpoint. No Python exception
handler or context-manager cleanup runs in the killed process. Each test reopens
the ledger/database, confirms rolled-back versus committed values and persisted
checkpoints, rejects execution without explicit resume, and recovers through a
new public-CLI process. Final counts, assignments, out-of-scope data and ledger
integrity are checked. These tests establish local process-crash behavior, not
power-loss durability, PostgreSQL server-restart behavior, or distributed recovery.
Separate [infrastructure tests](production-operations.md) now exercise owned-server
PostgreSQL restarts, session loss and a dropped COMMIT acknowledgement.

Canary-enabled runs additionally export filtered execution-policy fields, canary
verification, and the review hash/continuation approval. Canary evidence is labelled
`scope: canary` and never replaces `latest_recorded_verification` for the full
scope. An ordinary pause or canary pause is not COMPLETE. Check SQL is omitted
even when it appears inside the new execution policy.

## Evidence and sharing boundaries

- Export reads recorded evidence. Run `partitionguard verify` first when a fresh
  live assessment is needed. A COMPLETE snapshot may predate later data changes.
- Reports omit full contracts, connection identity, source fingerprints, raw SQL,
  source rows, raw errors, and verification observations/expected values. This
  sacrifices some diagnostic detail in favor of safer sharing; consult the local
  ledger privately when those details are needed.
- Product, version, dependency, column, operator and check labels remain visible,
  as do dates, counts and outcome metadata. Review reports before sharing. This
  explicit field filter is not an arbitrary-text secret detector. HTML text is
  escaped, including self-reported operator names.
- Integrity checking detects inconsistent local evidence. Exports are unsigned;
  neither the JSON nor HTML authenticates an operator or prevents an administrator
  from rewriting the ledger and its hashes. Reports can themselves be edited.
- A verified additive stage does not certify consumer deployment, producer
  rollout, old-column retirement, or an online migration. Existing writer-freeze
  requirements still apply.
- The exporter refuses existing output paths, unsupported formats, missing runs,
  invalid plans and corrupt ledgers. It never creates a missing ledger. Successful
  export means the snapshot was written, even if its migration or verification
  status is failed; inspect those fields rather than treating exit 0 as migration
  success.
