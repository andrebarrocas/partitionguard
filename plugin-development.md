# Developing a PartitionGuard plugin

The control plane owns plan identity, bounds, approval, state transitions, checkpoints, and required evidence. An integration should make those guarantees work for one engine. Optional SDK imports belong inside its factory, so contract planning remains usable without installing every vendor SDK.

## Entry points and API version

Plugins are explicitly selected from Python package entry points. The factory must be callable and declare `API_VERSION = 1`. Duplicate names and different API versions fail loading; installation order never selects a winner.

```toml
[project.entry-points."partitionguard.storage"]
mywarehouse = "mywarehouse_partitionguard:WarehouseAdapter"
```

```python
class WarehouseAdapter:
    API_VERSION = 1
    COMPATIBILITY_PROFILE = "postgres"
    name = "mywarehouse"

    def __init__(self, connection, read_only=True):
        # Import your optional SDK here, open one target, and set identity.
        ...
```

The code above declares the loading contract; it is not a working adapter. Storage factories must declare `COMPATIBILITY_PROFILE` as one of `duckdb`, `postgres`, or `delta`. Choose a profile only when the engine's tested type/conversion guarantees match; an unknown profile fails planning. A new compatibility policy requires an explicit core change and conformance tests rather than a plugin silently defining safety. See the actual [SQL adapters](src/partitionguard/adapters/sql.py), [Delta adapter](src/partitionguard/adapters/delta.py), and [adapter tests](tests/test_adapters.py) for complete implementations.

Entry-point groups exist for `contracts`, `storage`, `catalogs`, `queries`, `lineage`, `execution`, and `verification`. Storage selection is wired into planning/execution. Contract plugins are selected with `--contract-format` on validation and planning commands; their `load(path, object_name=None)` output is validated as an internal `Contract`. `lineage-import --provider NAME` selects a lineage factory whose `ingest(source)` returns normalized dependency information. The catalog, query, execution, and verification groups are discovery/loading seams and protocol definitions; their presence does not mean the CLI orchestrates arbitrary plugins in those roles. Add concrete lifecycle wiring and tests before claiming support for another role.

## Storage interface

An adapter supplies:

| Member | Required behavior |
| --- | --- |
| `name`, `identity`, `read_only` | Stable adapter name, non-secret physical target identity, explicit mutation permission. |
| `dialect`, `placeholder`, `quote()`, `sql_type()` | Safely generated verification SQL using the documented canonical types. |
| `schema(table)` | Canonical physical column types; reject unsupported nested/precision semantics. |
| `fetch(sql, parameters=())` | One read statement; return rows for bounded core queries. |
| `execute(sql, parameters=())` | An internal SQL implementation detail where applicable; never a general CLI command. Reject writes when read-only. |
| `transaction()` | Context with declared atomicity and failure behavior. SQL adapters roll back on any `BaseException`; Delta documents one atomic mutation and no post-commit rollback. |
| `lock(table)` | Exclude cooperating migration writers and document who can bypass it. |
| `add_column(table, operation)` | Add a nullable replacement representation; idempotently accept only the expected existing type. |
| `backfill(table, operation, bounds, partition)` | Deterministic copy/cast for one approved partition, respecting caps and source preservation. |
| `close()` and context management | Release connections and resources. |

Optional `configure_scope(table, bounds)` is called before preflight; Delta uses it to prune the read dataset and validate physical partitioning. Optional `validate_operation(table, operation)` rejects unsupported conversions before approval. Optional `evidence()` can expose a non-secret engine version/receipt for audit events without changing the source baseline. See [the exact internal interface](docs/implementation-contract.md) and current engine code when developing against a particular version.

## Required safety evidence

An adapter contribution needs real integration tests for schema inspection, unsupported types, read-only access, bounded updates, row caps, source drift, target identity, wrong pre-existing target types, failed conversions, and mandatory verification. Exercise both failure before commit and interruption after commit but before the local checkpoint. Show that replay produces the same data and that completed checkpoints are verified before being skipped.

State the actual transaction boundary, physical rewrite cost, supported engine/SDK versions, concurrency assumptions, and recovery limits. For example, a partition SQL predicate does not prove the database used an index. A mocked cloud response does not prove the actual service accepts the request. Never label an SDK wrapper as a complete AWS, Spark, or catalog integration.

Canonical plans contain no connection secrets. Adapter exceptions may contain query text, credentials, or source values: the engine persists sanitized classifications for unknown driver errors. Do not add raw driver messages to audit events.

## Trust boundary

Plugins execute installed Python code with process privileges. Version checks and interfaces are not a sandbox. A malicious plugin can bypass intended behavior, so operators must review and trust their plugin packages. The core does not automatically install plugins or trust remote lineage as authority to execute a migration.
