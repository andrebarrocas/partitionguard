# Security policy

This is an early 0.1.0 release candidate. No independent security audit or production certification is claimed. Security fixes target the current development branch and latest supported release once a public release exists.

## Reporting

Use the repository's private vulnerability-reporting feature once the owner has enabled it. Do not publish credentials, source rows, exploit details against a live system, or customer data in a public issue. If private reporting is not yet enabled, ask the repository owner for a private channel using a minimal non-sensitive message. The owner must enable private reporting and designate a security contact before public release; no mailbox or service-level commitment is fabricated here.

A useful private report identifies the affected version, adapter, reproducible synthetic example, impact, and expected behavior. Maintainers should coordinate remediation and disclosure with the reporter rather than require a public exploit before investigating.

## Trust boundaries

- An approved plan is bound to its content digest and prepared target. It is not a cryptographic signature from an independent authority.
- The SQLite ledger uses hash-linked events to detect accidental edits. Someone controlling the database and software can rewrite the entire chain. Export digests to independent storage when stronger evidence is required.
- Installed plugins are trusted executable Python with the process's permissions. Entry-point API checks are compatibility checks, not a sandbox.
- Database credentials should be scoped to required tables and kept outside plans, committed configuration, and shared logs.
- Custom assertions use a restricted scalar SQL form and bounded relation. SQL parsing is only one layer; use read-only database permissions for verification whenever practical.
- Maintenance windows are an operational requirement. Advisory locks do not stop unrelated writers. Source fingerprints and Delta version checks detect relevant drift; they do not create a universal distributed transaction.
- Migration metadata may expose table, field, dependency, and operator names. Review ledger/plan exports before sharing them even though source values are not persisted.

Destructive cleanup, arbitrary SQL migrations, automatic consumer cutover, and remote Delta storage are not implemented. Unsupported operations should fail before writes. Report any route around these limits as a safety defect.

## Dependency and workflow hygiene

Dependencies are version-bounded and optional engine SDKs are extras. GitHub Actions are pinned to reviewed commit IDs and Dependabot can propose updates. Publishing requires an explicit manual workflow and repository-controlled environments. The project never requests cloud credentials simply to run contract analysis or local tests.
