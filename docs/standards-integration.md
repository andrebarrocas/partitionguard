# Standards integration

Phase 0 design policy, reviewed 2026-10-04. This document defines intended interoperability and its limits; implementation support must be checked against the release's parser and tests.

## ODCS is the contract source

Use the Open Data Contract Standard (ODCS), maintained by Bitol, for contract input. ODCS separates the standard's `apiVersion` from a contract's `version`; `kind` is `DataContract`, and `id` provides stable identity. Current main documentation shows `v3.2.0`; that observation is not permission to accept every future v3 feature. [Fundamentals](https://github.com/bitol-io/open-data-contract-standard/blob/main/docs/fundamentals.md)

ODCS covers considerably more than a migration engine needs. Start with a documented relational subset. Do not advertise full ODCS validation or certification when only normalization is implemented. A future release should pin and test an upstream schema version; the normative standard takes precedence if its companion JSON Schema differs. [Standard overview](https://github.com/bitol-io/open-data-contract-standard/blob/main/docs/README.md)

## Normalization contract

| ODCS input | Intended internal meaning |
| --- | --- |
| `id`, `name`, `version` | Stable contract identity, display name, source version. |
| `schema` object | One explicitly selected relational table. |
| `physicalName`, `name` | Physical identifier and logical name remain distinct. |
| `properties[].logicalType`, `physicalType` | Logical family plus engine-specific type. |
| `required` | Nullability expectation. |
| `primaryKey`, `primaryKeyPosition` | Ordered primary-key membership. |
| `partitioned`, `partitionKeyPosition` | Ordered partition-column declaration. |
| `enum`, `unique` | Supported value and uniqueness constraints. |
| `description`, `tags` | Descriptive metadata. |

These fields come from ODCS's object/property schema model. The execution adapter must decide physical-type compatibility; logical similarity alone cannot establish cast safety. [Schema](https://github.com/bitol-io/open-data-contract-standard/blob/main/docs/schema.md)

If multiple schema objects exist, require explicit selection or reject the contract. Never silently select the first. Nested arrays, objects, maps, vectors, reference resolution, expressions, and engine-specific types need dedicated support before execution. Unknown executable semantics must produce an unsupported-feature error; descriptive metadata may be retained without interpretation.

Preserve a digest of original source bytes and the normalized representation in the plan. This makes normalization changes visible and ties approval to actual input. Do not rewrite the user's contract as a side effect of planning.

## Quality and service commitments

ODCS includes textual, library, SQL, and custom quality definitions. Its library metrics include row counts, nulls, duplicates, and invalid values; SQL rules compare a scalar result. These formats provide interchange, not automatic support for every engine. [Quality definitions](https://github.com/bitol-io/open-data-contract-standard/blob/main/docs/data-quality.md)

Only supported, tested rules can count toward verification. A prose quality requirement is not executable evidence. Unsupported required rules block execution until represented by a supported assertion or delegated to an explicitly configured verification plugin. Record units, null behavior, comparison operator, scope, and thresholds. Do not translate percent thresholds into fractions implicitly or compare aggregates over different populations.

SLA, owner, retention, and business-key information may be normalized where the adapter explicitly supports their shape. Remaining commitments stay visible as unsupported or externally enforced requirements. No release should claim enforcement from mere parsing. There is no new contract language in this repository: an internal Python model is an implementation detail.

## Separate execution intent

Keep bounds, batch limits, conversion intent, execution target, adapter settings, consumer gates, and verification policy in a separate migration manifest. These describe one operational change rather than redefine the data contract. A manifest should reference the contract and target object with digests. Engine secrets belong in environment variables or secret providers, not contract files, plans, or ledgers.

ODCS permits named custom properties. Use namespaced properties only when integration requires them; document their meaning and do not suggest they are standard ODCS fields. [Custom properties](https://github.com/bitol-io/open-data-contract-standard/blob/main/docs/custom-other-properties.md)

## Lineage exchange

OpenLineage identifies datasets and jobs by namespace/name and records runs as observations. Its events distinguish runtime execution from design metadata. An optional importer can convert declared inputs and outputs into dependency edges while preserving identity and provenance. [Object model](https://openlineage.io/docs/spec/object-model/)

An imported edge says that a relationship was declared or observed; it does not prove that all consumers have been found or migrated. Store ingestion time and source identity, flag stale/incomplete inputs, and never invent missing edges. DataHub and OpenMetadata are future information providers through the same graph-input interface, not dependencies of the core execution engine.

## Compatibility claims and tests

Each release must publish supported ODCS versions, accepted semantic fields, rejected constructs, and whether upstream-schema validation is performed. Fixtures should include minimal official-shape contracts, composite keys, physical names, multiple objects, nested unsupported fields, unsupported required quality rules, and changing standard versions. Round-trip preservation should only be claimed if tested. The contract adapter remains separate from migration planning and storage adapters so other established formats can be added without changing execution guarantees.
