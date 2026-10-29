# Phase review and scope audit

This review distinguishes executable candidate features from remaining product requirements. The phase numbers describe functional milestones; they are not six public releases. Phase 0 documents were saved and reviewed before implementation. Subsequent integrated development used the full available suite as its regression gate. Individual historical phase-by-phase test totals are not reconstructed or invented.

## Milestone evidence

| Phase | Candidate implementation and review | Evidence / remaining gate |
|---|---|---|
| 0: product and architecture | Problem, requirements, competitive landscape, standards, safety models, repository layout, roadmap, and ADR. The selected wedge is bounded executable migration/recovery/verification. | Documents distinguish contract tooling/catalog/orchestration overlap and explicitly discuss nearby migration capabilities. No claim of a unique invention or complete ODCS implementation. |
| 1: contracts and compatibility | ODCS relational subset; deterministic schema/requirement differences; adapter-aware rules; text/JSON CLI; unhandled requirements block execution. | Contract and planning tests include unsupported fields, invalid inputs, repeatability, and unsafe Python model construction. Full upstream ODCS validation remains deferred. |
| 2: impact and risk | Explicit dependency traversal; cycles; direct/transitive impact; model/check/SLA categories; deterministic risk reasons. | Graph property tests; unchanged/metadata-only contracts do not invent affected consumers. Both old/new declared edges are retained for material changes. Declared lineage is not proof of complete consumer coverage. |
| 3: plans and ledger | Content-addressed typed plans; half-open date bounds and row caps; explicit approval; SQLite events/checkpoints; integrity checking. | Plan tampering, bound validation, ledger reopen/locking/hash-chain tests. Physical connection identity and source/runtime evidence bind at dry-run preparation rather than appearing as credentials in the plan. |
| 4: execute/recover/verify | Real DuckDB and PostgreSQL additive migrations; one action per transaction; persisted failures; resumed checkpoint reconciliation; required verification. | Engine, adapter, and verification tests include real PostgreSQL, failed casts, source drift with unchanged counts, commit/checkpoint crash gaps, result corruption, and failing policy. Quiescent writers remain mandatory. |
| 5: release engineering | Python package and CLI; contributor/security/license material; GitHub Actions configuration; documentation, seven runnable tutorials and a recovery demonstration; release automation. | Source is public under André Barrocas's account. Dated local results and links to remote CI are in the validation record. Package publication, hosted documentation and user adoption remain separate milestones. |
| 6: ecosystem depth | Local Delta Lake adapter with physical DATE partition scope, native atomic actions, version evidence, historical reads, conflict detection, and engine recovery. | Real Delta integration tests cover bounded writes and recovery. Remote object stores and multi-action rollback are unsupported. |

Full-suite results, tested runtime versions, package/document build results, and any skips belong in the final validation report. A skipped engine-specific test on a different backend is not a passed test for that backend. CI configuration is not evidence that GitHub Actions has run.

## Adversarial review fixes

- Revalidate nested Pydantic objects at planning and execution boundaries; `model_copy` and `model_construct` are not trusted validation.
- Surface unknown object-level contract semantics and transformation dependencies instead of silently dropping them.
- Bound YAML size, nesting, and expanded node count; reject duplicate/non-string keys and recursive aliases.
- Refuse to adopt an unrelated replacement column after a failed initial admission. Resume requires the run's durable prior action evidence.
- Reconcile completed target assignments before skipping checkpoints; replay supported assignments when their target evidence no longer matches.
- Preserve original source fingerprints, including already committed partitions, through recovery. Bind runtime versions so dependency changes cannot silently change replay semantics.
- Bind ledger state/verification/checkpoint projections to hash-linked event evidence; retain failed attempts.
- Keep raw driver errors and source row values out of persisted failure text.
- Restrict custom SQL structure, functions, and cast types; exercise PostgreSQL function shadowing and row-security boundaries.
- Require explicitly selected storage plugins to declare an API version and a known compatibility profile; exercise one through a real complete migration.

## Requirements deliberately still limited

The initial executor completes an explicitly additive, bounded stage. It cannot honestly complete an in-place rename, dropped field, consumer deployment, writer dual-write rollout, partition-layout rewrite, destructive retirement, or whole distributed migration. Plans for unsupported physical changes are blocked. A useful verified replacement representation is the release's executable result.

Quality checks cover counts, fingerprints, required schema, partition coverage, conversion parity, keys, nulls/enums, numeric ranges, aggregate tolerances, freshness, and restricted scalar SQL. They do not prove arbitrary business semantics or all statistical distributions. Source evidence reads and sorts bounded rows in memory; production-scale throughput is not established.

The local ledger is neither an authenticated approval service nor administrator-proof evidence storage. Cooperative target locks do not exclude arbitrary external writers. A source freeze acknowledgement is operational responsibility, supported by drift checks rather than proof that every external writer stopped.

Plugins are trusted Python. Contract/storage/lineage/execution/verification/catalog/query extension roles are discoverable interfaces; having a role does not imply every possible third-party backend is implemented. Compatibility profiles are explicit plugin-author claims and need their own conformance tests.

The optional OpenLineage importer consumes offline events. DataHub and OpenMetadata integrations remain future work. AWS support is limited to experimental bounded read-only S3 inventory and Glue metadata inspection with stubbed request tests; it has no live-cloud or AWS execution claim. Spark, Athena/Redshift execution, EMR, distributed scheduling, and Rust remain deferred. No LLM participates in a safety decision.

## Complexity and release assessment

Keep the small typed operation set, single-controller ledger, fixed copy/cast semantics, and adapter boundaries. Do not grow a workflow language or orchestrator to conceal unsupported consumer cutover. No dead compatibility shims or empty success stubs should be counted as features.

The result is a public-source release candidate by André Barrocas, with a runnable
recovery demonstration and automated checks. A tagged package release remains
pending. The next adoption gate is an external engineer using the documented
bounded procedure on a noncritical replica, followed by feedback and review.
Package publication, production operations and adoption are separate milestones.
