"""Hold a real adapter/ledger lock until the parent kills this process."""

import sys
import threading
from pathlib import Path

from partitionguard.adapters import open_adapter
from partitionguard.ledger import Ledger

adapter_name, connection, ledger_file, marker = sys.argv[1:]
with open_adapter(adapter_name, connection, read_only=False) as adapter:
    with Ledger(ledger_file):
        with adapter.lock("events"):
            Path(marker).write_text("{}", encoding="utf-8")
            threading.Event().wait()
