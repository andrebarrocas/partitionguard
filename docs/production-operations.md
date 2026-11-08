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
`pg_ctl`) for these tests. CI requires them with
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
