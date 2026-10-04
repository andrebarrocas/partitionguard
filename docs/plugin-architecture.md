# Plugin architecture

The core owns plan identity, compatibility/risk policy, lifecycle transitions, execution approval, and the evidence model. Plugins provide narrowly scoped integration capabilities. A plugin cannot override safety policy by reporting an action as successful without its required evidence.

## Extension points

| Role | Responsibility | Required boundary |
|---|---|---|
| Contract format | Load and normalize a document, expose provenance and unsupported fields. | No writes while parsing; unsupported safety semantics stay visible. |
| Storage / query engine | Inspect physical schema, gather scope evidence, execute typed supported operations, query verification inputs. | Declare transaction, type, bounds, and concurrency capabilities. |
| Catalog | Resolve table/product metadata. | Metadata is not authority for writes; can implement the storage inspection or lineage role. |
| Lineage provider | Return normalized dependency nodes/edges with source and coverage. | No fabricated edges; freshness and incomplete coverage are explicit. |
| Execution backend | Run supported typed actions and expose attempts/checkpoints. | Preserve approved plan identity, idempotency, and error semantics. |
| Verification backend | Evaluate typed checks and return observations plus PASS/FAIL/ERROR. | No unsupported required check may silently pass. |

Use Python package entry points with versioned protocols. The concrete entry-point group names and signatures belong in the contributor reference alongside implementation; plugin packages must target a documented API version. Registry collisions fail clearly rather than selecting a plugin based on installation order.

## Capability negotiation

The current storage plugin factory declares `API_VERSION = 1` and an explicit `COMPATIBILITY_PROFILE` of `duckdb`, `postgres`, or `delta`. That opt-in selects tested core compatibility rules; it is a plugin-author claim, not certification of a different engine. The plan retains the selected plugin name, and the engine still applies its safety gates. Builtin selection never loads third-party entry points. A plugin with an unknown/missing profile fails closed. Future capability descriptors can extend this small interface when real adapters need them. Planning and preflight compare requirements with capabilities. Unsupported operation/check/type fails closed before execution.

Avoid a universal `execute(sql)` contract at the safety boundary. Prefer typed `inspect`, `baseline`, `add_column`, `backfill_partition`, and `verify` operations or equivalent structured interfaces. Where an adapter internally generates SQL, it must quote identifiers and parameterize values. Credentials stay in local environment/connection configuration and are not serialized into plans, logs, or ledgers.

## Trust and packaging

Plugins are locally installed Python code and therefore trusted code, not sandboxed untrusted extensions. Loading one may execute Python. The tool must not discover and install arbitrary remote plugins automatically. Optional vendor SDKs belong to extras/plugin packages, keeping contract analysis usable without them.

Each adapter documents tested engine/SDK versions, required permissions, transaction scope, physical I/O implications, consistency assumptions, and unsupported capabilities. Contract adapter support for ODCS is versioned and partial when necessary; it must not be advertised as full specification conformance without validation evidence.

## Conformance suite

A useful plugin contract tests: deterministic plan inputs; unsupported operation rejection; identifier handling; finite-bound enforcement; source drift; preexisting target field conflicts; failure before/after target commit; repeated assignments; checkpoint reconciliation; check errors; empty partitions; and credential redaction. Integration tests use the real engine when practical. Mocked service tests demonstrate request construction, not real service compatibility.

## Ecosystem direction

DuckDB and PostgreSQL establish the relational contract first. A focused delta-rs adapter then proves table inspection, transaction/version awareness, bounded additive changes or backfills actually supported by the library, conflict detection, and historical/post-migration verification. It must disclose file rewrite costs and must not imply relational transactional DDL semantics.

AWS integration follows only with testable capability scope. S3 metadata, Glue catalog resolution, Athena read-only verification, Redshift transactional operations, and Spark execution are distinct integration concerns; an `aws` switch is not an implementation of all of them. OpenLineage/DataHub/OpenMetadata are dependency sources, not new catalog subsystems.
