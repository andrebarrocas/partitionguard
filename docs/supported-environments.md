# Supported environments and upgrades

The matrix describes the tested execution surface for the 0.2.0 candidate,
not a guarantee for every driver release or every production workload.

| Environment | Tested configuration | Scope |
| --- | --- | --- |
| CPython / Linux | 3.11, 3.12, 3.13 on GitHub-hosted Ubuntu | Full suite with current resolved dependencies; additional 3.11 minimum profile |
| CPython / macOS | 3.11.14 on ARM64 | Current and minimum profiles, all three storage adapters, and workload measurements |
| DuckDB | 1.5.6 baseline; `>=1.5.6,<2` | Local files, additive stage and recovery |
| PostgreSQL | PostgreSQL 14 locally, 16 in CI; psycopg `>=3.2.1,<4` | Real server, transactional additive stage; owned instances for restart tests |
| Delta Lake | delta-rs 1.6.6, PyArrow 23.0.0 and 23.0.1, DuckDB 1.5.6 | Local physically DATE-partitioned tables; no remote/object-store execution |
| AWS | boto3 minimum 1.35.0 and current resolved version | Experimental read-only inventory; stub-tested, not a migration backend |
| Controlled approvals | POSIX private local storage and OpenSSH `/usr/bin/ssh-keygen` supporting `-Y` | Requires a protected runner and externally administered trust |

Windows, PyPy, network filesystem ledger locking, remote Delta, and unlisted
Python/database combinations have no support claim. The minimum profile
pins direct runtime dependencies; transitive and development dependencies
are resolved within their declared ranges. Compatibility floors are not a
recommendation to deploy outdated packages: use a separately reviewed,
locked production environment. Do not change adapter dependencies during
an active migration.

```sh
python -m pip install -c requirements-minimum.txt -e '.[dev,duckdb,postgres,delta,aws]'
python -m pip check
PARTITIONGUARD_REQUIRE_INFRA=1 python -m pytest
```

Set `PARTITIONGUARD_TEST_POSTGRES` to a disposable PostgreSQL service for
the integration fixtures. The suite creates its own PostgreSQL instances
for restart scenarios; it never restarts the supplied service.

## Storage adapter conformance

Run `python -m pytest tests/test_storage_conformance.py`. The same scenario
executes against real DuckDB, PostgreSQL and Delta instances. It checks
read-only preflight, approval gating, bounded writes, checkpointed interruption,
fresh-session recovery, unchanged baseline/checkpoints, final verification,
idempotent completion and untouched out-of-scope rows.

Plugin authors can reuse `exercise_storage(factory, inspect, plan, ledger_path)`
from the source distribution's conformance tests. Supply a factory that opens
a fresh adapter in the requested read-only mode, the equivalent four-row
fixture and typed plan, and an independent full-table inspector (including
the out-of-scope row). Run backend-specific conflict, permissions, type and
transaction tests as well; this one scenario does not certify a plugin.
An adapter should implement streaming reads to avoid the legacy buffering
fallback described in [workload limits](workload-limits.md).

## Plan and ledger compatibility

- Saved plans use format 1. The canonical bytes and digest of previously
  saved, explicit plans are unchanged. Newly created manifests get the new
  conservative row defaults; they do not change an existing approval.
- SQLite ledgers previously used `PRAGMA user_version=0`. The new controller
  recognizes this layout and marks it as version 1. It does not replace
  plans, run IDs, event hashes, checkpoints, baseline values, or approval
  policy. Unknown schema versions are rejected before schema initialization.
- The only explicitly compatible core transition is **0.1.0 → 0.2.0** for
  DuckDB, PostgreSQL and local Delta. The 0.2.0 verifier compares the original
  evidence, allowing only that core version difference. Adapter versions,
  backend signature, table/target, row counts, schema and fingerprints must
  still match. New workload limits apply and can block a legacy run.
  Plugin and other core version transitions are not implicitly accepted.
- Unsigned runs retain their original mode. A controlled run still requires
  the original pinned trust configuration. No software upgrade can supply
  missing approval, bypass a canary, or create a replacement baseline.
- Downgrading an active ledger is unsupported. Preserve a verified snapshot
  and the previous runner before the first upgrade open. Do not alternate
  controllers or restore a stale ledger over a newer run.

## Upgrade rehearsal

CI builds the previous 0.1.0 wheel from commit
`edeb0887d2ea1e21cbfce72e9dfcb0276dc76857`, installs it in a separate
environment with the **same adapter dependency versions**, and runs:

```sh
PARTITIONGUARD_PREVIOUS_PYTHON=/path/to/previous/bin/python \
PARTITIONGUARD_REQUIRE_INFRA=1 python -m pytest tests/upgrade_rehearsal.py
```

Each adapter's previous controller prepares, approves, writes and stops at a
verified canary. The 0.2.0 controller reopens the original ledger and approves
continuation, then resumes the same plan. The test compares the original
baseline, plan, run ID and checkpoints with the final records and verifies
the final data, including the untouched outside row. All three rehearsals
passed locally on 2026-11-09.

For an operational upgrade, freeze writers and pause the old controller,
make and verify a SQLite snapshot using backup tooling, and retain the
original plan, trust and runtime versions. Install only the new core wheel,
check the new controller's status, then explicitly resume if the same
target and baseline still verify. If compatibility or scope checks fail,
stop; preserve both copies and inspect committed work before recovery.
