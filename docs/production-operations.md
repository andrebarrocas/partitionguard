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
