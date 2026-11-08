# Production operation boundaries

The supported execution unit is an additive, bounded migration stage with frozen
source writers. Recovery reconciles committed actions against the original
approved baseline; it does not replace that baseline or roll back earlier commits.

## Infrastructure failures

A lost database connection does not establish whether its last COMMIT succeeded.
Partition checkpoints follow target commits. If the commit reply or subsequent
ledger write is lost, leave the source frozen, restore connectivity/storage,
inspect the run, and explicitly resume the same plan against the same target and
ledger. Recovery checks original source evidence and reconciles/replays supported
assignments before final verification. Never restart with a fresh baseline merely
to bypass drift or an uncertain commit outcome.

The engine preserves the original database exception if rollback or lock cleanup
also fails. If the ledger cannot record the failure, execution stops with an
explicit warning that its status may be stale. A durable RUNNING/VERIFYING state
after interruption is recoverable and requires `--resume`; it is not success.
Restore ledger access before continuation. Do not delete checkpoints or edit
SQLite projections. Back up the database together with its WAL using SQLite's
backup facilities rather than copying only a live main file.

Infrastructure regression tests create their **own** temporary PostgreSQL data
directory and server on loopback. They never restart a connection supplied through
`PARTITIONGUARD_TEST_POSTGRES`. Install PostgreSQL server binaries (`initdb` and
`pg_ctl`) for these tests. CI and release builds require them with
`PARTITIONGUARD_REQUIRE_INFRA=1`; otherwise missing binaries produce explicit skips.

```sh
python -m pytest tests/test_infrastructure_failures.py -q
```

Tests cover client SIGKILL, backend session termination, and immediate server
restart inside a transaction, after commit before checkpoint, and after a durable
checkpoint. A plaintext local test proxy forwards a real backfill COMMIT and
discards its acknowledgement, proving recovery from a committed-but-unacknowledged
transaction. SQLite page quotas and query-only mode exercise actual disk-full and
write-denied errors; a separate permission-error injection checks classification
and evidence filtering. These establish process/server failure behavior, not
hardware power-loss durability, replicated failover, or arbitrary storage faults.

## Controllers and external writers

Target locks coordinate independent controllers even when they use different
ledgers. PostgreSQL uses a session advisory lock on physical relation identity;
DuckDB excludes other writer processes; local Delta uses a filesystem lock.
Process death releases these locks. A second ledger cannot adopt replacement
columns created by an interrupted first controller: recovery must use the first
run's original ledger, plan and baseline. Never delete a lock file to bypass a
live controller or start a competing migration with a fresh ledger.

Before each partition assignment, the engine rechecks that partition's original
columns against its approved fingerprint inside the action transaction. Initial
and final whole-scope checks remain mandatory. Pending-partition drift prevents
that assignment; drift in an already completed partition fails final acceptance.
Changes outside the approved date scope do not count as scoped source drift.
This improves detection; it does not fence arbitrary writers or make an online
migration protocol. An external write can race individual checks. The maintenance
window and source freeze remain mandatory.

```sh
python -m pytest tests/test_concurrency.py -q
```

Nine cases cover real independent-process/independent-ledger exclusion, lock
release after process or PostgreSQL-session death, foreign-column rejection,
source changes during execution/recovery, and DuckDB/Delta process locks.

### Writer-freeze and restoration procedure

1. Identify relevant producers, scheduled jobs, ingestion, ad hoc users and retry
   mechanisms. Pause those writers and prevent automatic restarts for the approved
   table/scope using the platform's own access/scheduling controls.
2. Confirm in-flight writes have ended. Record the responsible operator and
   maintenance window. A CLI acknowledgement alone is not proof of a freeze.
3. Prepare the dry run against the frozen source, review its exact plan/target,
   approve it, then execute. Keep the source frozen through recovery and final
   verification. Preserve the original ledger and plan on durable local storage.
4. If an unexpected writer is detected, stop it and investigate. Restore source
   data only through a separately reviewed procedure if returning to the original
   baseline is correct. Do not edit fingerprints or rebaseline to hide drift.
5. Before restoring writers, verify the additive stage, review its evidence and
   coordinate producer behavior for future records. Backfilling history does not
   implement producer dual writes or consumer deployment. Restore scheduling and
   access intentionally, then monitor new data and the retained original column.

## Canary, pause and continuation

Add an execution policy to the existing migration manifest before planning and
approval. It changes the plan digest and cannot reuse an earlier approval.
Existing manifests without `execution` retain their original serialization,
digest and behavior; existing SQLite ledgers require no schema conversion.

```yaml
execution:
  canary_partitions: 1
  canary_checks:
    - kind: sql
      sql: SELECT COUNT(*) = 2 FROM {scope}
  max_duration_seconds: 300
```

The canary is the first N daily partitions in the approved half-open date range.
Choose checks appropriate for your actual canary data; the two-row assertion
above belongs to the synthetic example. Source fingerprints, schema, partition
counts, keys, column constraints and conversion parity are checked on that
subset, followed by the explicitly approved `canary_checks`. Full-scope contract
quality rules and migration checks are deferred until final verification: a
full-backfill assertion such as COUNT(*) = 6 is not a one-partition assertion.
No custom full-policy check is silently rewritten or considered satisfied by
the canary. Passing the canary never certifies the whole scope.

```sh
partitionguard migrate plan.json --ledger ledger.sqlite --execute \
  --maintenance-window --operator executor
# Exit 7: CANARY_PAUSED, with passing scoped evidence and committed checkpoints.

partitionguard report plan.json --ledger ledger.sqlite --output canary.html
partitionguard approve-continuation plan.json --ledger ledger.sqlite \
  --digest FULL_PLAN_DIGEST --operator reviewer
partitionguard migrate plan.json --ledger ledger.sqlite --execute --resume \
  --maintenance-window --operator executor
```

Set `PARTITIONGUARD_CONNECTION` for these database commands. Continuation approval
rechecks the entire frozen source and the canary, persists the approving operator,
and references the exact hash-linked canary review. The next execution still
requires `--resume`, reconciles checkpoints and rechecks canary verification.
A crash after canary commit but before the pause cannot bypass review: resume
first reconstructs the verified canary pause. Corrupt canaries fail approval and
require investigation/reconciliation and a new review. Final `COMPLETE` requires
the original full-scope policy. Approvals remain self-reported identities, not
authenticated access controls.

### Cooperative pause and cancellation

```sh
partitionguard pause plan.json --ledger ledger.sqlite --operator oncall
```

This queues a plan-scoped request on the controller's local filesystem without
opening its busy SQLite ledger. It reports PAUSE_REQUESTED, not an assertion
that execution has already stopped. The controller records PAUSED at its next
action boundary. SIGINT and SIGTERM request the same cooperative behavior.
The current transaction finishes verification/commit and checkpoint persistence;
earlier commits remain. No cross-action rollback is attempted. Confirm PAUSED
through `status` before making operational changes.

Pause requests survive process death. They are atomically created beside the
ledger as `<ledger>.<plan-digest>.pause`, with restricted file permissions; existing
requests are not overwritten and symlinks/nonregular files are rejected. Protect
this directory as carefully as the ledger. Acknowledgement follows durable pause
evidence, and resume handles a crash between recording the pause and removing
its request. A newer request is not cleared as an old acknowledgement. A queued
request for an already completed run has no active controller to pause.

Use explicit `migrate --execute --resume` after a recorded ordinary pause. A
canary pause additionally requires `approve-continuation`. CLI exit 7 means
paused/waiting for review; it must not be treated as successful completion.
SIGKILL cannot be handled gracefully and continues to require crash recovery.
Allow the process supervisor enough termination grace for an atomic action to
finish, or expect forced-kill recovery instead.

### Time budgets and database timeouts

`execution.max_duration_seconds` is a per-execution-invocation cooperative duration budget,
not a hard wall-clock interruption or a whole-migration elapsed-time limit.
It includes source reconciliation inside the engine and is checked between
actions and before completion. An in-flight read, write or verification can
overrun it; the next boundary pauses without starting another action. A shorter
`migrate --execute --max-duration-seconds N` can tighten an invocation's budget.
It cannot enlarge the approved limit. Every invocation records its effective
budget. Repeated resumes retain the original finite partition/row scope.

The built-in PostgreSQL adapter also supports approved
`statement_timeout_seconds` and `lock_timeout_seconds` under `execution`.
Unspecified values retain its 30-second statement and 5-second lock defaults.
They apply to engine database sessions, including preflight reads. A real database
timeout fails the action and requires explicit recovery; it is not a clean pause.
Set a connection-establishment timeout in the PostgreSQL DSN when needed; the
engine budget begins after connection setup. DuckDB/Delta do not advertise these
PostgreSQL settings; plans that request them on other adapters are blocked.

## Reproducible controlled-execution demonstration

```sh
python examples/controlled_execution.py --output /tmp/partitionguard-controls
```

The directory must not exist. Actual output observed on 2026-11-08:

```text
Canary: CANARY_PAUSED; 2 checkpoints; scoped verification passed (exit 7)
Resume without continuation approval: refused (exit 6)
Exact-plan continuation approved after fresh canary/source recheck
Durable pause request: PAUSED; no additional checkpoints (exit 7)
Explicit resume: COMPLETE; 4 checkpoints; full final policy passed
```

Inspect `canary.json`, `completed.html`, `completed.json` and `transcript.txt`.
CI runs the demonstration on Python 3.12 and publishes only these filtered files
as `execution-controls`; source data and the ledger remain outside the artifact.
Reports distinguish canary verification from final verification and omit SQL
from the execution policy, as they do for the rest of the plan.
