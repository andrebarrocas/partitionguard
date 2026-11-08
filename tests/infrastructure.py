"""Owned disposable PostgreSQL only: restart tests never stop a supplied DSN."""

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_engine import migration_plan

from partitionguard.adapters import open_adapter, quote
from partitionguard.engine import Engine
from partitionguard.ledger import Ledger
from partitionguard.planning import save_plan


@pytest.fixture(scope="session")
def owned_postgres(tmp_path_factory):
    binary = shutil.which("initdb")
    if binary is None:
        for candidate in sorted(Path("/usr/lib/postgresql").glob("*/bin/initdb"), reverse=True):
            binary = str(candidate)
            break
    if binary is None:
        if os.environ.get("PARTITIONGUARD_REQUIRE_INFRA"):
            pytest.fail("Install PostgreSQL server binaries for required infrastructure tests")
        pytest.skip("PostgreSQL server binaries required for owned restart tests")
    directory = tmp_path_factory.mktemp("owned-postgres")
    data = directory / "data"
    bindir = Path(binary).parent
    subprocess.run(
        [
            binary,
            "-D",
            str(data),
            "-A",
            "trust",
            "-U",
            "partitionguard",
            "--no-locale",
            "--encoding=UTF8",
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    def control(command, *args):
        subprocess.run(
            [str(bindir / "pg_ctl"), "-D", str(data), "-w", "-t", "20", command, *args],
            check=True,
            capture_output=True,
            timeout=30,
        )

    control(
        "start",
        "-l",
        str(directory / "postgres.log"),
        "-o",
        f"-h 127.0.0.1 -p {port} -c unix_socket_directories='' -c fsync=on",
    )
    try:
        yield SimpleNamespace(
            dsn=f"host=127.0.0.1 port={port} user=partitionguard dbname=postgres sslmode=disable",
            port=port,
            restart=lambda: control(
                "restart", "-m", "immediate", "-l", str(directory / "postgres.log")
            ),
        )
    finally:
        control("stop", "-m", "immediate")


@pytest.fixture
def pg_case(owned_postgres, tmp_path):
    plan = migration_plan(adapter="postgres", table="infra_" + uuid.uuid4().hex)
    ledger_path = tmp_path / "ledger.sqlite"
    plan_file = tmp_path / "plan.json"
    save_plan(plan, plan_file)
    dsn = owned_postgres.dsn
    with open_adapter("postgres", dsn, read_only=False) as adapter:
        adapter.execute(
            f"CREATE TABLE {quote(plan.current.table)} (id INTEGER, day DATE, carrier TEXT)"
        )
        adapter.execute(
            f"INSERT INTO {quote(plan.current.table)} VALUES (1,'2026-01-01','10'),(2,'2026-01-02','20'),(3,'2026-01-03','30'),(4,'2026-02-01','40')"
        )
        with Ledger(ledger_path) as ledger:
            engine = Engine(adapter, ledger)
            engine.dry_run(plan)
            engine.approve(plan, plan.id, "reviewer")
    try:
        yield SimpleNamespace(
            plan=plan,
            dsn=dsn,
            ledger=ledger_path,
            file=plan_file,
            directory=tmp_path,
            server=owned_postgres,
        )
    finally:
        with open_adapter("postgres", dsn, read_only=False) as adapter:
            adapter.execute(f"DROP TABLE IF EXISTS {quote(plan.current.table)}")


def wait_marker(process, marker):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and process.poll() is None:
        try:
            return json.loads(marker.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            time.sleep(0.02)
    raise AssertionError("Worker failed to reach its synchronized barrier")


def start_worker(case, barrier):
    marker = case.directory / (barrier + ".ready")
    process = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).with_name("process_worker.py")),
            str(case.file),
            case.dsn,
            str(case.ledger),
            barrier,
            str(marker),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return process, marker


def stop_worker(process):
    if process.poll() is None:
        process.kill()
    return process.communicate(timeout=10)


def resume_and_assert(case):
    with open_adapter("postgres", case.dsn, read_only=False) as adapter:
        with Ledger(case.ledger) as ledger:
            engine = Engine(adapter, ledger)
            run = ledger.get(case.plan.id)
            assert run["status"] in {"RUNNING", "FAILED", "VERIFICATION_FAILED", "PAUSED"}
            with pytest.raises(Exception, match="explicit resume"):
                engine.apply(case.plan, "operator", maintenance_window=True)
            baseline = run["baseline"]
            result = engine.apply(case.plan, "operator", resume=True, maintenance_window=True)
            assert result["status"] == "COMPLETE"
            assert result["verification"]["passed"] is True
            assert result["baseline"] == baseline
            assert len(result["checkpoints"]) == 4
            assert ledger.verify_integrity(case.plan.id)
        assert adapter.fetch(
            f"SELECT id,carrier,carrier_v2 FROM {quote(case.plan.current.table)} ORDER BY id"
        ) == [(1, "10", 10), (2, "20", 20), (3, "30", 30), (4, "40", None)]
