# Initial release acceptance

Tests must demonstrate observable safety boundaries on real storage engines.

1. A contracts-only diff creates no database and requires no credentials.
2. Identical input produces the same plan digest across processes.
3. An absent migration, unsupported semantic contract field, physical key change,
   in-place incompatible type change, or destructive removal fails CI.
4. Explicit half-open dates and finite partition/row limits are mandatory.
5. A dry run reads existing data, detects conversion failure, and writes only the
   local ledger. It must not add a target column.
6. Apply without matching approval, a maintenance-window assertion, or the
   originally prepared destination fails before writing.
7. Writes retain originals and touch only selected partition rows. Full table DDL
   is disclosed separately; partition predicates do not imply indexed scans.
8. Inject failure after a committed partition; recovery preserves successful work.
   Inject interruption before checkpoint; deterministic replay remains correct.
9. Source data changed after preparation invalidates approval and blocks execution.
10. Verification checks all selected partitions and required assertions. Deliberate
    corruption or SQL assertion failure cannot leave a COMPLETE state.
11. Ledger events include operator, run/plan identity, timestamps, action, attempt,
    failure, checkpoint, and evidence. Hash-chain verification catches accidental
    edits, without claiming malicious-administrator resistance.
12. Package builds, CLI commands, examples, tutorials and CI are exercised. Tutorial
    outputs are captured from a reproducible script, not authored examples.

External user validation remains a release follow-up. Passing local tests does
not establish behavior at production scale or under arbitrary concurrent writers.
