import pytest

from partitionguard import plugins


class Entry:
    name = "test"

    def __init__(self, plugin):
        self.plugin = plugin

    def load(self):
        return self.plugin


def test_explicit_load_and_version(monkeypatch):
    class Good:
        API_VERSION = 1

    monkeypatch.setattr(plugins, "entry_points", lambda **k: [Entry(Good)])
    assert plugins.load_plugin("storage", "test") is Good
    assert plugins.available("storage") == ("test",)
    Good.API_VERSION = 2
    with pytest.raises(ValueError, match="API_VERSION"):
        plugins.load_plugin("storage", "test")


def test_duplicates_and_unknown_roles(monkeypatch):
    monkeypatch.setattr(plugins, "entry_points", lambda **k: [Entry(object), Entry(object)])
    with pytest.raises(ValueError, match="found 2"):
        plugins.load_plugin("storage", "test")
    with pytest.raises(ValueError):
        plugins.available("random")


def test_builtin_planning_never_loads_plugin_entrypoints(monkeypatch):
    from partitionguard.planning import compatibility_profile

    monkeypatch.setattr(
        plugins, "entry_points", lambda **kwargs: pytest.fail("Builtin needs no plugins")
    )
    assert compatibility_profile("duckdb") == "duckdb"
    assert compatibility_profile("postgres") == "postgres"
    assert compatibility_profile("delta") == "delta"


@pytest.mark.parametrize("profile", [None, "unknown", "", 1])
def test_storage_plugin_requires_explicit_supported_compatibility_profile(monkeypatch, profile):
    from partitionguard.planning import compatibility_profile

    class MissingProfile:
        API_VERSION = 1
        COMPATIBILITY_PROFILE = profile

    monkeypatch.setattr(plugins, "entry_points", lambda **kwargs: [Entry(MissingProfile)])
    with pytest.raises(ValueError, match="COMPATIBILITY_PROFILE"):
        compatibility_profile("test")


def test_storage_plugin_uses_declared_profile_for_compatibility(monkeypatch):
    from partitionguard.models import Column, Contract
    from partitionguard.planning import diff

    class DeltaProfile:
        API_VERSION = 1
        COMPATIBILITY_PROFILE = "delta"

    monkeypatch.setattr(plugins, "entry_points", lambda **kwargs: [Entry(DeltaProfile)])
    current = Contract(
        product="p",
        version="1",
        owner="o",
        table="t",
        columns=(Column(name="id", data_type="INTEGER"),),
    )
    proposed = current.model_copy(
        update={"version": "2", "columns": (Column(name="id", data_type="BIGINT"),)}
    )
    assert diff(current, proposed, "test")[0].compatibility == "BREAKING"
    DeltaProfile.COMPATIBILITY_PROFILE = "duckdb"
    assert diff(current, proposed, "test")[0].compatibility == "SAFE"


def test_selected_storage_plugin_executes_real_migration(monkeypatch, tmp_path):
    duckdb = pytest.importorskip("duckdb")
    from partitionguard.adapters import open_adapter
    from partitionguard.adapters.sql import DuckDBAdapter
    from partitionguard.engine import Engine
    from partitionguard.ledger import Ledger
    from partitionguard.models import Bounds, Column, Contract, MigrationSpec, Operation
    from partitionguard.planning import plan, validate_plan

    class SelectedAdapter(DuckDBAdapter):
        API_VERSION = 1
        COMPATIBILITY_PROFILE = "duckdb"
        name = "test"

    monkeypatch.setattr(plugins, "entry_points", lambda **kwargs: [Entry(SelectedAdapter)])
    database = tmp_path / "plugin.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute("CREATE TABLE sample(day DATE, value VARCHAR)")
        connection.execute("INSERT INTO sample VALUES ('2026-01-01', '10')")
    current = Contract(
        product="p",
        version="1",
        owner="o",
        table="sample",
        columns=(Column(name="day", data_type="DATE"), Column(name="value", data_type="STRING")),
        partition_by=("day",),
    )
    proposed = current.model_copy(
        update={
            "version": "2",
            "columns": current.columns + (Column(name="value_v2", data_type="INTEGER"),),
        }
    )
    migration = MigrationSpec(
        bounds=Bounds(column="day", start="2026-01-01", end="2026-01-02"),
        operations=(Operation(source="value", target="value_v2", data_type="INTEGER"),),
    )
    selected = plan(current, proposed, "test", migration)
    assert selected.adapter == "test"
    assert not selected.blockers
    validate_plan(selected)
    with (
        open_adapter("test", str(database), read_only=False) as adapter,
        Ledger(tmp_path / "ledger.sqlite") as ledger,
    ):
        engine = Engine(adapter, ledger)
        engine.dry_run(selected)
        engine.approve(selected, selected.id, "reviewer")
        result = engine.apply(selected, "operator", maintenance_window=True)
        assert result["status"] == "COMPLETE"
        assert adapter.fetch("SELECT value_v2 FROM sample") == [(10,)]
