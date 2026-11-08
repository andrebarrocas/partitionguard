"""Subprocess-only crash barriers; no failure injection is exposed in the CLI."""

import json
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
        pid = adapter._connection.info.backend_pid if plan.adapter == "postgres" else None
        Path(marker).write_text(json.dumps({"backend_pid": pid}), encoding="utf-8")
        while not Path(marker + ".continue").exists():
            threading.Event().wait(0.02)

    with open_adapter(plan.adapter, database, read_only=False) as adapter:
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
            engine = Engine(adapter, ledger)
            if barrier == "lock":
                original = engine._source_unchanged

                def locked_source(*args):
                    stop()
                    return original(*args)

                engine._source_unchanged = locked_source
            engine.apply(plan, "crash-test", maintenance_window=True)


if __name__ == "__main__":
    main()
