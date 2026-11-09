import json
import subprocess

import pytest
from test_cli import demo as demo
from test_cli import invoke, make_plan
from test_engine import case as case
from typer.testing import CliRunner

from partitionguard.approval import NAMESPACE
from partitionguard.cli import app
from partitionguard.ledger import Ledger, LedgerError
from partitionguard.models import ExecutionPolicy
from partitionguard.planning import load_plan, plan, save_plan


def setup(demo, monkeypatch, canary=False):
    directory, database = demo
    plan_file, digest = make_plan(directory)
    if canary:
        old = load_plan(plan_file)
        value = plan(
            old.current,
            old.proposed,
            old.adapter,
            old.migration.model_copy(update={"execution": ExecutionPolicy(canary_partitions=1)}),
        )
        save_plan(value, plan_file)
        digest = value.id
    key = directory / "reviewer-key"
    subprocess.run(
        ["/usr/bin/ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True
    )
    trust = directory / "signers"
    trust.write_text("reviewer " + key.with_suffix(".pub").read_text())
    monkeypatch.setenv("PARTITIONGUARD_TRUST_FILE", str(trust))
    monkeypatch.setenv("PARTITIONGUARD_CONNECTION", str(database))
    private = directory / "private"
    ledger = private / "ledger.sqlite"
    args = [plan_file, "--ledger", ledger]
    invoke(["migrate", *args, "--json"])
    return args, digest, key, ledger


def sign(args, key, suffix, continuation=False):
    flags = ["--continuation"] if continuation else []
    receipt = invoke(["approval-receipt", *args, "--operator", "reviewer", *flags]).stdout
    destination = key.parent / suffix
    destination.write_text(receipt)
    subprocess.run(
        ["/usr/bin/ssh-keygen", "-Y", "sign", "-f", str(key), "-n", NAMESPACE, str(destination)],
        check=True,
        capture_output=True,
    )
    return destination.with_suffix(".sig"), json.loads(receipt)


def test_signed_approvals_bind_exact_run_and_canary_and_survive_backup(demo, monkeypatch):
    args, digest, key, ledger = setup(demo, monkeypatch, canary=True)
    approve = ["approve", *args, "--digest", digest, "--operator", "reviewer"]
    invoke(approve, 6)
    signature, receipt = sign(args, key, "initial")
    assert receipt["scope"]["start"] == "2026-01-01"
    other = ledger.with_name("other.sqlite")
    other_args = [args[0], "--ledger", other]
    invoke(["migrate", *other_args])
    invoke(
        [
            "approve",
            *other_args,
            "--digest",
            digest,
            "--operator",
            "reviewer",
            "--signature",
            signature,
        ],
        6,
    )
    invoke([*approve, "--signature", signature])
    execution = ["--execute", "--maintenance-window", "--operator", "runner", "--json"]
    assert json.loads(invoke(["migrate", *args, *execution], 7).stdout)["status"] == "CANARY_PAUSED"
    cont = [
        "approve-continuation",
        *args,
        "--digest",
        digest,
        "--operator",
        "reviewer",
        "--signature",
    ]
    invoke([*cont, signature], 6)
    signature, receipt = sign(args, key, "continuation", continuation=True)
    assert receipt["canary_review"]
    invoke([*cont, signature])
    backup = ledger.with_name("snapshot.sqlite")
    invoke(["ledger-backup", "--ledger", ledger, "--output", backup])
    assert backup.stat().st_mode & 0o777 == 0o600
    assert ledger.stat().st_mode & 0o777 == 0o600
    with Ledger(backup, secure=True) as snapshot:
        assert snapshot.get(digest)["run_id"] == receipt["run_id"]
        assert snapshot.verify_integrity(digest)
    # Restore to a new file, resume only there.
    restored = ledger.with_name("restored.sqlite")
    invoke(["ledger-backup", "--ledger", backup, "--output", restored])
    secret = "postgresql://runner:private-db-password@warehouse"
    with monkeypatch.context() as patch:

        def driver_failure(*args):
            raise ValueError(secret)

        patch.setattr("partitionguard.adapters.sql.SQLAdapter.backfill", driver_failure)
        failed = invoke(["migrate", args[0], "--ledger", restored, *execution, "--resume"], 2)
        assert "private-db-password" not in failed.output
    with Ledger(restored, secure=True) as journal:
        assert "private-db-password" not in str(journal.events(digest))
    report = restored.with_name("report.json")
    invoke(["report", args[0], "--ledger", restored, "--format", "json", "--output", report])
    rendered = report.read_text()
    assert "private-db-password" not in rendered
    assert "approval_authentication" in rendered
    assert "BEGIN SSH SIGNATURE" not in rendered
    result = invoke(["migrate", args[0], "--ledger", restored, *execution, "--resume"])
    assert json.loads(result.stdout)["status"] == "COMPLETE"
    monkeypatch.delenv("PARTITIONGUARD_TRUST_FILE")
    invoke(["migrate", *args, *execution, "--resume"], 6)


def test_writable_or_linked_controlled_ledgers_are_rejected(tmp_path):
    public = tmp_path / "public"
    public.mkdir()
    public.chmod(0o755)
    with pytest.raises(LedgerError, match="private"):
        Ledger(public / "ledger", secure=True)
    actual = tmp_path / "actual"
    actual.touch()
    linked = tmp_path / "linked"
    linked.symlink_to(actual)
    with pytest.raises(LedgerError, match="symbolic"):
        Ledger(linked, secure=True)


def test_connection_error_never_prints_raw_driver_dsn(demo, monkeypatch):
    directory, _ = demo
    plan_file, _ = make_plan(directory)
    secret = "postgresql://runner:secret-password@warehouse"

    def failed(*args, **kwargs):
        raise ValueError(secret)

    monkeypatch.setattr("partitionguard.adapters.open_adapter", failed)
    result = CliRunner().invoke(app, ["migrate", str(plan_file), "--connection", secret])
    assert result.exit_code != 0
    assert "secret-password" not in result.output
