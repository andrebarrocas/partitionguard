"""Reproduce a failed tutorial run after one committed partition; use synthetic data."""

import argparse

from partitionguard.adapters import open_adapter
from partitionguard.engine import Engine, InjectedFailure
from partitionguard.ledger import Ledger
from partitionguard.models import canonical_json
from partitionguard.planning import load_plan

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("plan")
parser.add_argument("database")
parser.add_argument("ledger")
args = parser.parse_args()
plan = load_plan(args.plan)
with (
    open_adapter(plan.adapter, args.database, read_only=False) as adapter,
    Ledger(args.ledger) as ledger,
):
    engine = Engine(adapter, ledger)
    try:
        engine.apply(plan, operator="tutorial", maintenance_window=True, fail_after=1)
    except InjectedFailure:
        state = ledger.get(plan.id)
        print(
            canonical_json(
                {
                    "status": state["status"],
                    "checkpointed_actions": sorted(state["checkpoints"]),
                    "message": "Synthetic interruption after one committed partition; resume uses the same plan.",
                }
            )
        )
    else:
        raise RuntimeError("Expected injected failure")
