import json

import duckdb
import pytest
from typer.testing import CliRunner

from partitionguard.cli import app
from partitionguard.examples import initialize

runner = CliRunner()


@pytest.fixture
def demo(tmp_path):
    initialize(tmp_path)
    db = tmp_path / "db.duckdb"
    with duckdb.connect(str(db)) as conn:
        conn.execute(
            "CREATE TABLE shipment_events(event_id BIGINT, event_date DATE, carrier_code VARCHAR, amount DOUBLE)"
        )
        conn.execute(
            "INSERT INTO shipment_events SELECT i, DATE '2026-01-01' + CAST((i-1)//2 AS INTEGER), CAST(i AS VARCHAR), i::DOUBLE FROM range(1,7) t(i)"
        )
    return tmp_path, db


def invoke(args, code=0):
    result = runner.invoke(app, list(map(str, args)))
    assert result.exit_code == code, result.output + str(result.exception)
    return result


def make_plan(folder, name="proposed.yaml", manifest="migration.yaml"):
    path = folder / "plan.json"
    result = invoke(
        [
            "plan",
            folder / "current.yaml",
            folder / name,
            "--migration",
            folder / manifest,
            "--output",
            path,
            "--json",
        ]
    )
    return path, json.loads(result.stdout)["plan_id"]


def test_help_init_and_version(tmp_path):
    assert "migrate" in invoke(["--help"]).output
    result = runner.invoke(app, ["--version"])
    # --version must work without a subcommand.
    assert result.exit_code == 0, result.output
    assert "0.1.0" in result.output
    invoke(["init", tmp_path / "example"])
    invoke(["init", tmp_path / "example"], 2)
    assert not list(tmp_path.rglob("*.duckdb"))


def test_ci_policy_json_and_summary(demo):
    folder, _ = demo
    p, _ = make_plan(folder)
    summary = folder / "summary.md"
    result = invoke(
        [
            "plan",
            folder / "current.yaml",
            folder / "breaking.yaml",
            "--ci",
            "--json",
            "--summary",
            summary,
        ],
        3,
    )
    assert json.loads(result.stdout)["plan"]["risk"]["level"] == "BLOCKING"
    assert "Blocked" in summary.read_text()
    assert p.exists()


def test_analysis_commands_read_only(demo):
    folder, db = demo
    invoke(["validate", folder / "current.yaml", "--json"])
    assert (
        "SAFE"
        in invoke(["diff", folder / "current.yaml", folder / "proposed.yaml", "--json"]).output
    )
    result = invoke(["impact", folder / "current.yaml", folder / "proposed.yaml", "--json"])
    assert len(json.loads(result.stdout)["direct"]) == 2
    assert len(list(folder.glob("*.duckdb"))) == 1


def test_complete_cli_lifecycle_defaults_safe(demo):
    folder, db = demo
    p, digest = make_plan(folder)
    args = [p, "--connection", db, "--ledger", folder / "ledger.sqlite", "--json"]
    invoke(["migrate", *args, "--execute", "--operator", "test", "--maintenance-window"], 6)
    result = invoke(["migrate", *args])
    assert json.loads(result.stdout)["status"] == "PREPARED"
    with duckdb.connect(str(db), read_only=True) as conn:
        assert "carrier_code_v2" not in [
            r[0] for r in conn.execute("DESCRIBE shipment_events").fetchall()
        ]
    invoke(["approve", *args, "--digest", "wrong", "--operator", "test"], 6)
    invoke(["approve", *args, "--digest", digest, "--operator", "test"])
    invoke(["migrate", *args, "--execute", "--operator", "test"], 6)
    result = invoke(["backfill", *args, "--execute", "--operator", "test", "--maintenance-window"])
    assert json.loads(result.stdout)["status"] == "COMPLETE"
    assert json.loads(invoke(["verify", *args]).stdout)["passed"]
    status = invoke(["status", p, "--ledger", folder / "ledger.sqlite", "--json"])
    assert json.loads(status.stdout)["status"] == "COMPLETE"
    assert json.loads(invoke(["history", "--ledger", folder / "ledger.sqlite", "--json"]).stdout)
    with duckdb.connect(str(db)) as conn:
        conn.execute("UPDATE shipment_events SET carrier_code_v2=-1 WHERE event_id=1")
    assert not json.loads(invoke(["verify", *args], 5).stdout)["passed"]


def test_resume_without_execute_and_missing_connection(demo, monkeypatch):
    folder, _ = demo
    p, _ = make_plan(folder)
    monkeypatch.delenv("PARTITIONGUARD_CONNECTION", raising=False)
    invoke(["migrate", p, "--resume"], 2)
    invoke(["migrate", p], 2)


def test_contract_plugin_selected_explicitly(demo, monkeypatch):
    from partitionguard import plugins
    from partitionguard.contracts import load_contract

    class ContractReader:
        API_VERSION = 1

        def load(self, path, object_name=None):
            return load_contract(path, object_name)

    monkeypatch.setattr(plugins, "load_plugin", lambda role, name: ContractReader)
    folder, _ = demo
    result = invoke(["validate", folder / "current.yaml", "--contract-format", "example", "--json"])
    assert json.loads(result.stdout)["product"] == "shipment_events"


def test_lineage_cli_offline_output(tmp_path):
    event = {
        "eventType": "COMPLETE",
        "eventTime": "2026-01-01T12:00:00Z",
        "producer": "example",
        "run": {"runId": "example-run"},
        "job": {"namespace": "jobs", "name": "summary"},
        "inputs": [{"namespace": "warehouse", "name": "events"}],
        "outputs": [{"namespace": "warehouse", "name": "summary"}],
    }
    path = tmp_path / "event.json"
    path.write_text(json.dumps(event))
    out = tmp_path / "lineage.json"
    result = invoke(["lineage-import", path, "--output", out])
    assert json.loads(result.stdout) == json.loads(out.read_text())
    assert json.loads(result.stdout)["complete"] is False


def test_lineage_mapping_size_checked_before_parse(tmp_path):
    path, mapping = tmp_path / "events.json", tmp_path / "mapping.json"
    path.write_text("{}")
    mapping.write_text(" " * 10_000_001)
    assert (
        "mapping file exceeds" in invoke(["lineage-import", path, "--mapping", mapping], 2).output
    )


def test_aws_cli_forwards_explicit_bounds(monkeypatch):
    from partitionguard.aws import AWSInventory

    calls = []

    class Inventory:
        def inspect(self, *args):
            calls.append(args)
            return {"execution_supported": False}

    monkeypatch.setattr(AWSInventory, "connect", lambda region: Inventory())
    result = invoke(
        [
            "aws-inspect",
            "database",
            "events",
            "--bucket",
            "example",
            "--prefix",
            "data/",
            "--max-objects",
            "7",
            "--max-bytes",
            "900",
            "--max-pages",
            "2",
        ]
    )
    assert calls == [("database", "events", "example", "data/", 7, 900, None, 2)]
    assert json.loads(result.stdout)["execution_supported"] is False


def test_plan_human_output_is_derived(demo):
    folder, _ = demo
    result = invoke(
        [
            "plan",
            folder / "current.yaml",
            folder / "proposed.yaml",
            "--migration",
            folder / "migration.yaml",
        ]
    )
    assert "3 daily partitions" in result.output
    assert "Affected transformations: 3" in result.output
    assert "INTEGER nullable" in result.output
