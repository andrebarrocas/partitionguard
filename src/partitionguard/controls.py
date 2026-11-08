"""Cooperative, plan-scoped pause requests and temporary CLI signal handlers."""

from __future__ import annotations

import json
import os
import signal
import stat
import tempfile
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .models import canonical_json


class PauseRequest:
    def __init__(self, ledger_path, plan_id):
        ledger = Path(ledger_path).expanduser().resolve()
        self.plan_id = plan_id
        self.path = ledger.with_name(f"{ledger.name}.{plan_id}.pause")

    def create(self, operator):
        if not operator.strip() or len(operator) > 256:
            raise ValueError("Pause requires an operator identity of 1–256 characters")
        payload = {
            "plan_id": self.plan_id,
            "operator": operator,
            "token": uuid.uuid4().hex,
            "requested_at": datetime.now(timezone.utc).isoformat(),
        }
        # Link a completely written file into place without overwriting a request.
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=self.path.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            try:
                stream.write(canonical_json(payload))
                stream.flush()
                os.fsync(stream.fileno())
                try:
                    os.link(temporary, self.path)
                except FileExistsError:
                    raise ValueError("A pause request is already pending") from None
            finally:
                temporary.unlink(missing_ok=True)
        return payload

    def read(self):
        try:
            descriptor = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError("Pause request must be a regular file")
                data = stream.read(4097)
        except FileNotFoundError:
            return None
        if len(data) > 4096:
            raise ValueError("Pause request exceeds its size limit")
        value = json.loads(data)
        if (
            not isinstance(value, dict)
            or value.get("plan_id") != self.plan_id
            or not isinstance(value.get("token"), str)
            or len(value["token"]) != 32
            or not isinstance(value.get("operator"), str)
            or not 1 <= len(value["operator"].strip()) <= 256
        ):
            raise ValueError("Invalid plan-scoped pause request")
        return value

    def acknowledge(self, token):
        current = self.read()
        if current and current["token"] == token:
            self.path.unlink()


@contextmanager
def graceful_signals():
    """Signals request a boundary pause; they never claim to abort committed work."""
    requested = threading.Event()
    previous = {}
    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, lambda *_: requested.set())
    try:
        yield requested.is_set
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
