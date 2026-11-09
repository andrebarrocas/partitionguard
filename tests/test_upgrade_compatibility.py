import sqlite3

import pytest

from partitionguard.ledger import Ledger, LedgerError
from partitionguard.verification import baseline_matches


def test_compatibility_only_allows_known_core_change_not_source_or_runtime_drift():
    runtime = {"core": "0.2.0", "adapter": "duckdb", "dependencies": {"duckdb": "1.5.6"}}
    current = {"runtime_signature": runtime, "partitions": {"date": {"fingerprint": "original"}}}
    approved = {**current, "runtime_signature": {**runtime, "core": "0.1.0"}}
    assert baseline_matches(current, approved)
    assert approved["runtime_signature"]["core"] == "0.1.0"
    assert not baseline_matches(current, {**approved, "partitions": {}})
    assert not baseline_matches(
        current, {**approved, "runtime_signature": {**runtime, "core": "0.0.1"}}
    )
    assert not baseline_matches(
        current,
        {
            **approved,
            "runtime_signature": {**runtime, "core": "0.1.0", "dependencies": {"duckdb": "1.4.0"}},
        },
    )


def test_future_ledger_version_refused_without_rewriting(tmp_path):
    path = tmp_path / "future.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=99")
    with pytest.raises(LedgerError, match="Unsupported ledger schema version"):
        Ledger(path)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 99
        assert not db.execute("SELECT name FROM sqlite_master").fetchall()
