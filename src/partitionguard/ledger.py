"""Durable execution evidence with one local writer and hash-linked events.

The event hash chain detects accidental modification; it is not protection from
an administrator who can rewrite the database and all hashes.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class LedgerError(RuntimeError):
    """The ledger cannot safely satisfy a request."""


class LedgerIntegrityError(LedgerError):
    """Persisted evidence no longer matches its hash chain or projections."""


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Ledger:
    """One recoverable run per immutable plan, with append-only attempt evidence."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_file = self.path.with_suffix(self.path.suffix + ".lock").open("a+b")
        try:
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._lock_file.close()
            raise LedgerError("Ledger is in use by another controller") from exc
        try:
            self.connection = sqlite3.connect(str(self.path), timeout=5)
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA synchronous=FULL")
            self.connection.execute("PRAGMA foreign_keys=ON")
            self.connection.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    plan_id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
                    plan TEXT NOT NULL, baseline TEXT NOT NULL,
                    target_identity TEXT NOT NULL, status TEXT NOT NULL,
                    operator TEXT, approved_digest TEXT, created_at TEXT NOT NULL,
                    started_at TEXT, finished_at TEXT, updated_at TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    error TEXT, verification TEXT
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    plan_id TEXT NOT NULL REFERENCES runs(plan_id),
                    timestamp TEXT NOT NULL, kind TEXT NOT NULL, detail TEXT NOT NULL,
                    previous_hash TEXT NOT NULL, hash TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_plan ON events(plan_id, id);
                CREATE TABLE IF NOT EXISTS checkpoints (
                    plan_id TEXT NOT NULL REFERENCES runs(plan_id),
                    action_id TEXT NOT NULL, detail TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    PRIMARY KEY (plan_id, action_id)
                );
                CREATE TRIGGER IF NOT EXISTS events_no_update
                    BEFORE UPDATE ON events BEGIN
                    SELECT RAISE(ABORT, 'events are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS events_no_delete
                    BEFORE DELETE ON events BEGIN
                    SELECT RAISE(ABORT, 'events are append-only'); END;
            """)
        except BaseException:
            if hasattr(self, "connection"):
                self.connection.close()
            self._lock_file.close()
            raise

    def __enter__(self) -> Ledger:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def close(self) -> None:
        if not self._lock_file.closed:
            self.connection.close()
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
            self._lock_file.close()

    def _append(
        self, plan_id: str, kind: str, detail: dict, *, timestamp: str | None = None
    ) -> None:
        run = self.connection.execute(
            "SELECT run_id FROM runs WHERE plan_id=?", (plan_id,)
        ).fetchone()
        if run is None:
            raise LedgerError("Unknown migration plan")
        detail = {**detail, "run_id": run["run_id"]}
        previous = self.connection.execute(
            "SELECT hash FROM events WHERE plan_id=? ORDER BY id DESC LIMIT 1",
            (plan_id,),
        ).fetchone()
        event = {
            "plan_id": plan_id,
            "timestamp": timestamp if timestamp is not None else _now(),
            "kind": kind,
            "detail": detail,
            "previous_hash": previous["hash"] if previous else "",
        }
        fingerprint = hashlib.sha256(_json(event).encode()).hexdigest()
        self.connection.execute(
            "INSERT INTO events(plan_id,timestamp,kind,detail,previous_hash,hash) "
            "VALUES(?,?,?,?,?,?)",
            (plan_id, event["timestamp"], kind, _json(detail), event["previous_hash"], fingerprint),
        )

    def events(self, plan_id: str) -> list[dict]:
        rows = self.connection.execute(
            "SELECT * FROM events WHERE plan_id=? ORDER BY id", (plan_id,)
        ).fetchall()
        return [{**dict(row), "detail": json.loads(row["detail"])} for row in rows]

    def verify_integrity(self, plan_id: str) -> bool:
        row = self.connection.execute("SELECT * FROM runs WHERE plan_id=?", (plan_id,)).fetchone()
        if row is None:
            raise LedgerError("Unknown migration plan")
        events = self.events(plan_id)
        if not events or events[0]["kind"] != "PREPARED":
            raise LedgerIntegrityError("Missing preparation evidence")
        prepared = events[0]["detail"]
        if not prepared.get("run_id"):
            raise LedgerIntegrityError("Missing preparation run identity")
        metadata = {
            "run_id": prepared["run_id"],
            "created_at": events[0]["timestamp"],
            "updated_at": events[0]["timestamp"],
            "started_at": None,
            "finished_at": None,
        }
        previous = ""
        status = None
        approved_digest = None
        operator = None
        attempts = 0
        verification = None
        error = None
        checkpoints: dict[str, dict] = {}
        for event in events:
            payload = {
                key: event[key]
                for key in ("plan_id", "timestamp", "kind", "detail", "previous_hash")
            }
            expected = hashlib.sha256(_json(payload).encode()).hexdigest()
            if event["previous_hash"] != previous or event["hash"] != expected:
                raise LedgerIntegrityError("Execution event hash chain is invalid")
            previous = event["hash"]
            if event["detail"].get("run_id") != metadata["run_id"]:
                raise LedgerIntegrityError("Execution event run identity is inconsistent")
            if "status" in event["detail"]:
                status = event["detail"]["status"]
            if event["kind"] == "APPROVED":
                approved_digest = event["detail"]["approved_digest"]
                operator = event["detail"]["operator"]
            if event["kind"] == "STATE":
                operator = event["detail"]["operator"]
                attempts = event["detail"]["attempt"]
                verification = event["detail"].get("verification")
                error = event["detail"].get("error")
                if status == "RUNNING" and metadata["started_at"] is None:
                    metadata["started_at"] = event["timestamp"]
                metadata["finished_at"] = (
                    event["timestamp"]
                    if status in {"COMPLETE", "FAILED", "VERIFICATION_FAILED"}
                    else None
                )
            if event["kind"] in {"PREPARED", "APPROVED", "STATE"}:
                metadata["updated_at"] = event["timestamp"]
                if any(
                    key not in event["detail"] or event["detail"][key] != value
                    for key, value in metadata.items()
                ):
                    raise LedgerIntegrityError("Run metadata does not match lifecycle evidence")
            if event["kind"] == "CHECKPOINT":
                if event["detail"].get("completed_at") != event["timestamp"]:
                    raise LedgerIntegrityError("Checkpoint timestamp does not match its event")
                checkpoints[event["detail"]["action_id"]] = {
                    "detail": event["detail"]["detail"],
                    "completed_at": event["detail"]["completed_at"],
                }
        if (
            prepared["plan"] != json.loads(row["plan"])
            or prepared["baseline"] != json.loads(row["baseline"])
            or prepared["target_identity"] != row["target_identity"]
            or status != row["status"]
            or approved_digest != row["approved_digest"]
            or operator != row["operator"]
            or attempts != row["attempts"]
            or verification != (json.loads(row["verification"]) if row["verification"] else None)
            or error != row["error"]
            or any(row[key] != value for key, value in metadata.items())
        ):
            raise LedgerIntegrityError("Run state does not match its execution evidence")
        stored_checkpoints = {
            item["action_id"]: {
                "detail": json.loads(item["detail"]),
                "completed_at": item["completed_at"],
            }
            for item in self.connection.execute(
                "SELECT action_id,detail,completed_at FROM checkpoints WHERE plan_id=?", (plan_id,)
            )
        }
        if checkpoints != stored_checkpoints:
            raise LedgerIntegrityError("Checkpoints do not match execution evidence")
        return True

    def get(self, plan_id: str) -> dict | None:
        row = self.connection.execute("SELECT * FROM runs WHERE plan_id=?", (plan_id,)).fetchone()
        if row is None:
            return None
        self.verify_integrity(plan_id)
        value = dict(row)
        for field in ("plan", "baseline", "verification"):
            value[field] = json.loads(value[field]) if value[field] is not None else None
        value["migration_id"] = plan_id
        value["checkpoints"] = {
            item["action_id"]: {
                "detail": json.loads(item["detail"]),
                "completed_at": item["completed_at"],
            }
            for item in self.connection.execute(
                "SELECT action_id,detail,completed_at FROM checkpoints WHERE plan_id=? ORDER BY action_id",
                (plan_id,),
            )
        }
        return value

    def history(self) -> list[dict]:
        ids = self.connection.execute(
            "SELECT plan_id FROM runs ORDER BY created_at,plan_id"
        ).fetchall()
        return [self.get(row["plan_id"]) for row in ids]

    def prepare(self, plan_id: str, plan: dict, baseline: dict, target_identity: str) -> dict:
        existing = self.get(plan_id)
        if existing:
            if any(
                existing[key] != value
                for key, value in (
                    ("plan", plan),
                    ("baseline", baseline),
                    ("target_identity", target_identity),
                )
            ):
                raise LedgerError("An existing approved baseline or target cannot be replaced")
            return existing
        timestamp = _now()
        with self.connection:
            self.connection.execute(
                "INSERT INTO runs(plan_id,run_id,plan,baseline,target_identity,status,created_at,updated_at) "
                "VALUES(?,?,?,?,?,'PREPARED',?,?)",
                (
                    plan_id,
                    str(uuid.uuid4()),
                    _json(plan),
                    _json(baseline),
                    target_identity,
                    timestamp,
                    timestamp,
                ),
            )
            self._append(
                plan_id,
                "PREPARED",
                {
                    "status": "PREPARED",
                    "plan": plan,
                    "baseline": baseline,
                    "target_identity": target_identity,
                    "created_at": timestamp,
                    "updated_at": timestamp,
                    "started_at": None,
                    "finished_at": None,
                },
                timestamp=timestamp,
            )
        return self.get(plan_id)

    def approve(self, plan_id: str, operator: str) -> dict:
        run = self.get(plan_id)
        if not run or run["status"] not in {"PREPARED", "APPROVED"}:
            raise LedgerError("Only a prepared plan can be approved")
        if not operator.strip():
            raise LedgerError("An explicit operator identity is required")
        timestamp = _now()
        with self.connection:
            self.connection.execute(
                "UPDATE runs SET status='APPROVED',operator=?,approved_digest=?,updated_at=? WHERE plan_id=?",
                (operator, plan_id, timestamp, plan_id),
            )
            self._append(
                plan_id,
                "APPROVED",
                {
                    "status": "APPROVED",
                    "operator": operator,
                    "approved_digest": plan_id,
                    "created_at": run["created_at"],
                    "updated_at": timestamp,
                    "started_at": run["started_at"],
                    "finished_at": run["finished_at"],
                },
                timestamp=timestamp,
            )
        return self.get(plan_id)

    def transition(
        self,
        plan_id: str,
        status: str,
        *,
        operator: str | None = None,
        error: str | None = None,
        verification: dict | None = None,
    ) -> dict:
        run = self.get(plan_id)
        if not run:
            raise LedgerError("Unknown migration plan")
        allowed = {
            "PREPARED": {"FAILED"},
            "APPROVED": {"RUNNING", "FAILED", "VERIFICATION_FAILED"},
            "RUNNING": {"RUNNING", "VERIFYING", "FAILED", "VERIFICATION_FAILED"},
            "VERIFYING": {"COMPLETE", "FAILED", "VERIFICATION_FAILED", "RUNNING"},
            "FAILED": {"RUNNING", "FAILED", "VERIFICATION_FAILED"},
            "VERIFICATION_FAILED": {"RUNNING", "VERIFICATION_FAILED", "FAILED"},
            "COMPLETE": {"COMPLETE", "VERIFICATION_FAILED", "FAILED"},
        }
        if status not in allowed.get(run["status"], set()):
            raise LedgerError(f"Invalid execution transition {run['status']} -> {status}")
        if status == "COMPLETE" and (not verification or verification.get("passed") is not True):
            raise LedgerError("Completion requires passing verification evidence")
        timestamp = _now()
        attempts = run["attempts"] + (1 if status == "RUNNING" else 0)
        started = run["started_at"] or (timestamp if status == "RUNNING" else None)
        finished = timestamp if status in {"COMPLETE", "FAILED", "VERIFICATION_FAILED"} else None
        with self.connection:
            self.connection.execute(
                "UPDATE runs SET status=?,operator=?,updated_at=?,started_at=?,finished_at=?,"
                "attempts=?,error=?,verification=? WHERE plan_id=?",
                (
                    status,
                    operator or run["operator"],
                    timestamp,
                    started,
                    finished,
                    attempts,
                    error,
                    _json(verification) if verification is not None else None,
                    plan_id,
                ),
            )
            self._append(
                plan_id,
                "STATE",
                {
                    "status": status,
                    "operator": operator or run["operator"],
                    "attempt": attempts,
                    "error": error,
                    "verification": verification,
                    "created_at": run["created_at"],
                    "updated_at": timestamp,
                    "started_at": started,
                    "finished_at": finished,
                },
                timestamp=timestamp,
            )
        return self.get(plan_id)

    def record_event(self, plan_id: str, kind: str, detail: dict) -> None:
        if not self.get(plan_id):
            raise LedgerError("Unknown migration plan")
        if kind in {"PREPARED", "APPROVED", "STATE", "CHECKPOINT"} or "status" in detail:
            raise LedgerError("Lifecycle evidence must use its dedicated ledger operation")
        with self.connection:
            self._append(plan_id, kind, detail)

    def checkpoint(self, plan_id: str, action_id: str, detail: dict) -> None:
        run = self.get(plan_id)
        if not run or run["status"] != "RUNNING":
            raise LedgerError("Checkpoints require an executing run")
        timestamp = _now()
        with self.connection:
            self.connection.execute(
                "INSERT INTO checkpoints(plan_id,action_id,detail,completed_at) VALUES(?,?,?,?) "
                "ON CONFLICT(plan_id,action_id) DO UPDATE SET detail=excluded.detail,completed_at=excluded.completed_at",
                (plan_id, action_id, _json(detail), timestamp),
            )
            self._append(
                plan_id,
                "CHECKPOINT",
                {"action_id": action_id, "detail": detail, "completed_at": timestamp},
                timestamp=timestamp,
            )
