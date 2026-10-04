# Contributing to PartitionGuard

PartitionGuard exists to make a bounded data migration reviewable, recoverable, and verifiable. Changes should improve that job. A new integration must implement a useful, tested capability rather than add a vendor name to the README.

## Development

Use Python 3.11 or newer. From the repository root:

```console
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev,duckdb,postgres,delta,aws]'
python -m pytest
python -m ruff check .
python -m ruff format --check src tests examples benchmarks
python -m build
python -m mkdocs build --strict
```

PostgreSQL tests run against a disposable database selected through `PARTITIONGUARD_TEST_POSTGRES`. Without it, these cases are explicitly skipped; a skipped integration suite is not PostgreSQL validation. For example, a local disposable service can be started with:

```console
docker run --rm --detach --name partitionguard-postgres -p 127.0.0.1:55432:5432 -e POSTGRES_PASSWORD=partitionguard -e POSTGRES_DB=partitionguard_test postgres:16
export PARTITIONGUARD_TEST_POSTGRES='postgresql://postgres:partitionguard@127.0.0.1:55432/partitionguard_test'
python -m pytest
docker stop partitionguard-postgres
```

Use only a disposable test database. Tests create and remove temporary tables. CI configures PostgreSQL 16 and Python 3.11, 3.12, and 3.13; its results must actually run before claiming that matrix passed.

## Change expectations

- Explain the concrete unsafe behavior or user need being addressed.
- Preserve deterministic plans: no timestamps, random IDs, secrets, or connection strings in plan content.
- Block unsupported semantics before writes. Never infer a successful check from an exception or empty evidence.
- Add tests for observable guarantees, especially failure after commit and before checkpoint. Property tests should establish invariants rather than restate an implementation.
- Keep source rows and credentials out of logs, fixtures, and issue reports.
- Update the relevant model/adapter documentation and CHANGELOG. Label planned capabilities as planned.

Start with [architecture](docs/architecture.md), the [implementation interface](docs/implementation-contract.md), and [plugin development](plugin-development.md). Add an ADR when changing transaction, approval, compatibility, or evidence semantics. Small internal refactors do not need new architecture documents.

## Pull requests

Describe the problem, changed behavior, test evidence, and any remaining limitation. The PR template is a prompt, not a requirement to generate long prose. Keep a reviewable change focused on one user outcome. Maintainers may ask to remove an abstraction when a direct implementation is easier to understand.

## Versioning and release ownership

The package follows semantic versioning. During 0.x development, incompatible public API or plan-format changes require a minor version increase and migration notes; patch releases preserve documented behavior. A serialized plan's format version is independent of the package version. Do not silently reinterpret an approved historical plan.

The checked-in release workflow runs only on explicit dispatch. It verifies the package version and an existing matching tag, runs tests, builds distributions, and creates a draft GitHub release after the `release` environment gate. PyPI publishing is a separate opt-in input and requires the `pypi` environment and a configured trusted publisher. Repository owners must configure protected environments, publishing ownership, and private reporting contacts before the first public release. See [release operations](docs/release-operations.md).

Contributions are provided under the repository's Apache-2.0 license. Do not contribute code or data you lack permission to distribute.
