"""Subprocess-only crash barriers; no failure injection is exposed in the CLI."""

import sys
import threading
from pathlib import Path

from partitionguard.adapters import open_adapter
from partitionguard.engine import Engine
from partitionguard.ledger import Ledger
from partitionguard.planning import load_plan


def main():
    plan_file, database, ledger_file, barrier, marker = sys.argv[1:]
    plan = load_plan(Path(plan_file))

    def stop():
        Path(marker).write_text("ready", encoding="utf-8")
        threading.Event().wait()  # Parent sends SIGKILL; no Python cleanup can run.

    with open_adapter("duckdb", database, read_only=False) as adapter:
        with Ledger(ledger_file) as ledger:
            checkpoint = ledger.checkpoint
            backfill = adapter.backfill

            def checkpoint_barrier(plan_id, action_id, detail):
                if action_id.startswith("backfill:") and barrier == "before_checkpoint":
                    stop()
                result = checkpoint(plan_id, action_id, detail)
                if action_id.startswith("backfill:") and barrier == "after_checkpoint":
                    stop()
                return result

            def transaction_barrier(*args):
                result = backfill(*args)
                if barrier == "inside_transaction":
                    stop()
                return result

            ledger.checkpoint = checkpoint_barrier
            adapter.backfill = transaction_barrier
            Engine(adapter, ledger).apply(plan, "crash-test", maintenance_window=True)


if __name__ == "__main__":
    main()
