# Controlled approvals and evidence

Use a dedicated, restricted POSIX runner account for writes. Install a reviewed
wheel and a trusted OpenSSH `/usr/bin/ssh-keygen` with `-Y verify`. The
runner administrator provisions a root- or runner-owned allowed-signers file
that is not group/world writable. Map each reviewer principal to its own public
key. Keep reviewer private keys off the runner. See the
[OpenSSH allowed-signers format](https://man.openbsd.org/ssh-keygen#ALLOWED_SIGNERS).

Configure `PARTITIONGUARD_TRUST_FILE` and `PARTITIONGUARD_CONNECTION` in the
protected runner service environment. Give requesters access to a reviewed
job interface, not the runner shell, environment, writable code/plugin path,
trust configuration, target credential, or ledger directory. Restrict targets
to the runner's assigned database and tables. The program authenticates the
reviewer key; it cannot secure an uncontrolled account or authenticate an
arbitrary self-reported executor label. Provision the deployment separately
and rehearse it without production credentials.

## Review and sign

1. Prepare the reviewed immutable plan against the frozen intended target with
   `partitionguard migrate plan.json --ledger private/ledger.sqlite` (no execute).
2. Export the exact receipt:
   `partitionguard approval-receipt plan.json --ledger private/ledger.sqlite --operator reviewer > receipt.json`.
   Review the full plan, its digest, target identity, adapter/table, prepared
   baseline digest, exact bounds, and run ID. Check that the target identity
   corresponds to the intended protected runner connection. The receipt
   intentionally does not contain the connection URI.
3. On the reviewer's own machine, sign the bytes, including the newline:
   `ssh-keygen -Y sign -f REVIEWER_KEY -n partitionguard-approval-v1 receipt.json`.
   Send the resulting signature through the protected job interface.
4. On the runner:
   `partitionguard approve plan.json --ledger private/ledger.sqlite --digest FULL_DIGEST --operator reviewer --signature receipt.json.sig`.
   The runner rebuilds the receipt from the verified ledger and checks the
   signature and principal against its pinned trust configuration.
5. Execute with the normal explicit flags. For a canary, repeat the signing
   process with `approval-receipt --continuation` and
   `approve-continuation --signature ...`. The continuation receipt includes
   the exact canary review hash. An initial signature cannot approve it.

The trust-file digest is pinned when preparing the run. Removing the controlled
configuration, changing trust bytes, changing scope/plan/target/baseline, or
moving a signature to a different run cannot authorize execution. Suspend
affected runs on a key compromise or trust change; rotate trust outside the
active run and replan or obtain an explicitly reviewed recovery path. No
live revocation or automatic trust migration is claimed. Existing unsigned
runs cannot be silently promoted to controlled mode. The standalone mode
retains its original self-reported approvals and is for trusted local operators.

## Credential and privilege boundaries

- Use a dedicated PostgreSQL role with CONNECT on the intended database and
  USAGE in the intended schema; read-only preparation/verification requires
  SELECT on the relevant tables. Writing requires target ownership for ALTER
  TABLE and appropriate SELECT/UPDATE privileges for assignment. Keep the owner
  role scoped to the assigned relations; avoid superuser, CREATE ROLE,
  BYPASSRLS, and unrelated schema/database privileges. The runner service,
  rather than the signing reviewer, holds execution privileges.
- Use a protected password file or runner secret store; avoid DSNs containing
  passwords in arguments, shell history, examples, CI output, and traces.
  Controlled invocations refuse a connection flag. For remote PostgreSQL,
  configure certificate and hostname verification. The adapter identity is
  a binding to its recorded endpoint/database or local object identity, not
  a substitute for authenticating the server. Keep DuckDB/Delta file access
  limited to the assigned dataset and its containing directories.
- Keep raw plans, ledgers, status/history, and unfiltered verification data
  private. They may contain SQL, schema identifiers and aggregate observations.
  Use `report` for filtered evidence; it omits SQL, row/observation values,
  connection identity, and raw driver errors. Operator and product labels
  remain visible. Controlled approval records contain the verified principal
  and hashes; preserve signed receipts, signatures and the reviewed trust
  version separately if independent verification is required.

## Ledger backup, restore and retention

Controlled mode checks a runner-owned ledger directory without group/world
access and private regular database/lock/SQLite sidecar files. Use mode 0700
for the directory and 0600 for files, with protected ancestors and private
encrypted scratch/storage. Do not share credentials with other processes
running as the same OS identity.

Pause/stop the controller before taking a snapshot. Run
`partitionguard ledger-backup --ledger private/ledger.sqlite --output private/snapshot.sqlite`.
It checks SQLite and the event chains, uses the
[SQLite Backup API](https://www.sqlite.org/backup.html), and refuses to replace
an existing file. Do not copy only a live SQLite main file. Encrypt backups,
store copies outside the runner under restricted retention policy, and retain
the immutable plan, original trust policy, and signed receipts for the same
audit period. Set retention and deletion schedules with the operating team;
there is no automatic purge or administrator-proof audit store.

Restore to a **new** private file with the same backup command (snapshot as
source). Disable the old controller first; do not run both ledger copies.
Check the expected run ID, approved plan, baseline, target, and snapshot
freshness. For a partial run, resume against the unchanged source with
`--resume`; never generate a replacement baseline. If the snapshot predates
ownership evidence for an existing replacement, stop and investigate rather
than adopting it. Preserve or reissue any pending pause request explicitly.

## Independent review gate

Independent sign-off is outstanding. Give a reviewer this threat model, the
approval/ledger code, signed and unsigned mode tests, and a disposable runner.
Ask them to try signature replay, cross-target/plan and canary substitution,
principal impersonation, trust downgrade, file/backup tampering, credential
leaks, and recovery after rotation. Review the actual OS permissions, database
roles and request interface as well as the Python code. Record findings and
sign-off before relying on this controlled configuration for production.
