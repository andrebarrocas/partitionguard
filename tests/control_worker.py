"""Run the public CLI with a synchronized transaction barrier for signal tests."""

import sys
import threading
from pathlib import Path

from partitionguard.adapters.sql import SQLAdapter
from partitionguard.cli import app

plan_file, database, ledger, marker = sys.argv[1:]
original = SQLAdapter.backfill


def barrier(self, *args):
    result = original(self, *args)
    Path(marker).write_text("{}", encoding="utf-8")
    while not Path(marker + ".continue").exists():
        threading.Event().wait(0.02)
    return result


SQLAdapter.backfill = barrier
sys.argv = [
    "partitionguard",
    "migrate",
    plan_file,
    "--connection",
    database,
    "--ledger",
    ledger,
    "--execute",
    "--maintenance-window",
    "--operator",
    "signal-test",
    "--json",
]
app()
