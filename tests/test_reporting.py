import json

import pytest
from test_engine import case as case
from typer.testing import CliRunner

from partitionguard.cli import app
from partitionguard.engine import InjectedFailure
from partitionguard.ledger import LedgerIntegrityError
from partitionguard.planning import save_plan
from partitionguard.reporting import build_report, render_html


def test_report_prepared_and_approved_are_not_verified(case):
    engine, _, ledger, plan = case
    with pytest.raises(ValueError, match="No prepared run"):
        build_report(plan, ledger)
    engine.dry_run(plan)
    before = ledger.events(plan.id)
    prepared = build_report(plan, ledger)
    assert prepared["status"] == "PREPARED"
    assert prepared["latest_recorded_verification"] is None
    assert prepared["checkpoints"] == []
    assert ledger.events(plan.id) == before  # Export adds no audit/lifecycle events.
    engine.approve(plan, plan.id, "reviewer")
    approved = build_report(plan, ledger)
    assert approved["status"] == "APPROVED"
    assert approved["latest_recorded_verification"] is None
    assert approved["timeline"][-1]["detail"]["approved_digest"] == plan.id


def test_report_retains_failure_recovery_and_subsequent_corruption(case):
    engine, adapter, ledger, plan = case
    engine.dry_run(plan)
    engine.approve(plan, plan.id, "reviewer")
    with pytest.raises(InjectedFailure):
        engine.apply(plan, "operator", maintenance_window=True, fail_after=1)
    failed = build_report(plan, ledger)
    assert failed["status"] == "FAILED"
    assert len(failed["checkpoints"]) == 2
    assert failed["latest_recorded_verification"] is None
    engine.apply(plan, "operator", resume=True, maintenance_window=True)
    complete = build_report(plan, ledger)
    assert complete["status"] == "COMPLETE"
    assert complete["attempts"] == 2
    assert complete["latest_recorded_verification"]["passed"] is True
    kinds = [event["kind"] for event in complete["timeline"]]
    assert "EXECUTION_FAILED" in kinds
    assert "ACTION_RECONCILED" in kinds
    assert len(complete["checkpoints"]) == 4
    adapter.execute("UPDATE shipments SET carrier_v2=999 WHERE id=1")
    # A report is a recorded snapshot, not an implicit live verification.
    assert build_report(plan, ledger)["status"] == "COMPLETE"
    assert engine.verify(plan)["passed"] is False
    corrupt = build_report(plan, ledger)
    assert corrupt["status"] == "VERIFICATION_FAILED"
    assert corrupt["latest_recorded_verification"]["passed"] is False
    assert any(
        check["passed"] is False for check in corrupt["latest_recorded_verification"]["checks"]
    )


def test_report_filters_sensitive_payloads_and_escapes_html(case):
    engine, _, ledger, plan = case
    engine.dry_run(plan)
    operator = '<script>alert("operator")</script>'
    engine.approve(plan, plan.id, operator)
    secret = "password=do-not-export-this"
    ledger.record_event(
        plan.id, "DIAGNOSTIC", {"sql": secret, "error": secret, "extra": {"password": secret}}
    )
    ledger.record_event(
        plan.id,
        "VERIFICATION",
        {
            "result": {
                "passed": False,
                "checks": [
                    {
                        "name": "custom_assertion",
                        "partition": None,
                        "passed": False,
                        "observed": secret,
                        "expected": secret,
                    }
                ],
            }
        },
    )
    evidence = build_report(plan, ledger)
    encoded = json.dumps(evidence)
    assert secret not in encoded
    assert ledger.get(plan.id)["target_identity"] not in encoded
    assert all(
        partition["fingerprint"] not in encoded
        for partition in ledger.get(plan.id)["baseline"]["partitions"].values()
    )
    html = render_html(evidence)
    assert operator not in html
    assert "&lt;script&gt;" in html
    assert "<script" not in html
    assert "Content-Security-Policy" in html
    assert "not signed" in html
    assert secret not in html


def test_report_rejects_corrupt_ledger(case):
    engine, _, ledger, plan = case
    engine.dry_run(plan)
    ledger.connection.execute("UPDATE runs SET status='COMPLETE'")
    ledger.connection.commit()
    with pytest.raises(LedgerIntegrityError):
        build_report(plan, ledger)


@pytest.mark.parametrize("format", ["html", "json"])
def test_cli_export_needs_no_connection_and_preserves_existing_files(
    case, tmp_path, monkeypatch, format
):
    engine, _, ledger, plan = case
    engine.dry_run(plan)
    plan_file = tmp_path / "plan.json"
    save_plan(plan, plan_file)
    ledger_path = ledger.path
    ledger.close()
    monkeypatch.delenv("PARTITIONGUARD_CONNECTION", raising=False)
    runner = CliRunner()
    destination = tmp_path / f"report.{format}"
    args = [
        "report",
        str(plan_file),
        "--ledger",
        str(ledger_path),
        "--format",
        format,
        "--output",
        str(destination),
    ]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    content = destination.read_text()
    if format == "json":
        assert json.loads(content)["plan_id"] == plan.id
    else:
        assert "<!doctype html>" in content
        assert plan.id in content
    assert runner.invoke(app, args).exit_code == 2
    assert destination.read_text() == content
    original = plan_file.read_bytes()
    assert runner.invoke(app, [*args[:-1], str(plan_file)]).exit_code == 2
    assert plan_file.read_bytes() == original


def test_cli_report_invalid_inputs_create_no_artifact(tmp_path):
    runner = CliRunner()
    destination = tmp_path / "report.html"
    args = [
        "report",
        str(tmp_path / "missing-plan.json"),
        "--ledger",
        str(tmp_path / "missing.sqlite"),
        "--output",
        str(destination),
    ]
    assert runner.invoke(app, args).exit_code == 2
    assert runner.invoke(app, [*args, "--format", "pdf"]).exit_code == 2
    assert not destination.exists()
    assert not (tmp_path / "missing.sqlite").exists()


@pytest.mark.parametrize("corrupt", [False, True])
def test_cli_does_not_export_missing_or_corrupt_run(case, tmp_path, corrupt):
    engine, _, ledger, plan = case
    plan_file = tmp_path / "plan.json"
    destination = tmp_path / "untrusted.html"
    save_plan(plan, plan_file)
    if corrupt:
        engine.dry_run(plan)
        ledger.connection.execute("UPDATE runs SET status='COMPLETE'")
        ledger.connection.commit()
    ledger_path = ledger.path
    ledger.close()
    result = CliRunner().invoke(
        app,
        ["report", str(plan_file), "--ledger", str(ledger_path), "--output", str(destination)],
    )
    assert result.exit_code == (4 if corrupt else 2), result.output
    assert not destination.exists()
