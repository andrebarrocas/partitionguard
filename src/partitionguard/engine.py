"""Approved bounded execution with deterministic crash replay."""

from __future__ import annotations

import time
from contextlib import nullcontext
from typing import Any

from .ledger import Ledger
from .models import Plan, normalize_type
from .planning import validate_plan
from .verification import capture_baseline, verify, verify_conversion, verify_operation


class EngineError(RuntimeError):
    """A safety precondition prevents execution."""


class SourceDriftError(EngineError):
    """The original approved source is no longer unchanged."""


class VerificationFailed(EngineError):
    """The required verification policy did not pass."""


class InjectedFailure(EngineError):
    """Python-only failure injection for recovery tests and tutorials."""


class Engine:
    def __init__(self, adapter: Any, ledger: Ledger):
        self.adapter = adapter
        self.ledger = ledger

    def _adapter_evidence(self) -> dict:
        provider = getattr(self.adapter, "evidence", None)
        if provider is None:
            return {}
        # Commit versions are audit evidence, never source-baseline fields:
        # this controller's own writes legitimately advance table versions.
        evidence = provider()
        return {
            key: evidence[key]
            for key in ("version", "table_id", "atomicity", "historical")
            if key in evidence
        }

    def _validate(self, plan: Plan) -> None:
        validate_plan(plan)
        if plan.blockers:
            raise EngineError("Plan is blocked: " + "; ".join(plan.blockers))
        if plan.adapter != self.adapter.name:
            raise EngineError("Adapter does not match the approved plan")
        if plan.migration is None:
            raise EngineError("Execution requires an explicit bounded migration recipe")
        if hasattr(self.adapter, "configure_scope"):
            self.adapter.configure_scope(plan.current.table, plan.migration.bounds)

    def _run(self, plan: Plan) -> dict:
        run = self.ledger.get(plan.id)
        if not run:
            raise EngineError("Run dry-run before approval or execution")
        if run["plan"] != plan.model_dump(mode="json"):
            raise EngineError("Saved plan content does not match")
        if run["target_identity"] != self.adapter.identity:
            raise EngineError("Connection target does not match the prepared target")
        return run

    def _source_unchanged(self, plan: Plan, run: dict) -> None:
        observed = capture_baseline(self.adapter, plan)
        if observed != run["baseline"]:
            raise SourceDriftError(
                "Source drift detected; the approved baseline cannot be replaced. Investigate and replan."
            )

    def _targets_absent(self, plan: Plan) -> None:
        schema = self.adapter.schema(plan.current.table)
        existing = [op.target for op in plan.migration.operations if op.target in schema]
        if existing:
            raise EngineError(
                "Replacement columns already exist at initial admission: " + ", ".join(existing)
            )

    def _reconcile_target_ownership(self, plan: Plan) -> None:
        """Do not adopt a foreign column after a failed admission attempt.

        An add action may have committed before its checkpoint. Its durable
        start event establishes that the original executor attempted that DDL.
        No such event means an existing replacement is outside this run.
        """
        started = {
            event["detail"].get("action_id")
            for event in self.ledger.events(plan.id)
            if event["kind"] == "ACTION_STARTED"
        }
        schema = self.adapter.schema(plan.current.table)
        unowned = [
            operation.target
            for operation in plan.migration.operations
            if operation.target in schema and f"add:{operation.target}" not in started
        ]
        if unowned:
            raise EngineError(
                "Cannot adopt replacement columns without this run's prior action evidence: "
                + ", ".join(unowned)
            )

    def dry_run(self, plan: Plan) -> dict:
        """Read the target and persist evidence. Never mutate the dataset."""
        self._validate(plan)
        existing = self.ledger.get(plan.id)
        # Read-only adapters need no writer lock. The baseline is reconciled
        # again under the writer lock and maintenance window before execution.
        with nullcontext() if self.adapter.read_only else self.adapter.lock(plan.current.table):
            if existing:
                run = self._run(plan)
                self._source_unchanged(plan, run)
                return run
            self._targets_absent(plan)
            baseline = capture_baseline(self.adapter, plan)
            conversion = verify_conversion(self.adapter, plan)
            if conversion.get("passed") is not True:
                raise EngineError(
                    "Dry-run conversion preflight failed: " + str(conversion.get("checks", []))
                )
            run = self.ledger.prepare(
                plan.id, plan.model_dump(mode="json"), baseline, self.adapter.identity
            )
            self.ledger.record_event(
                plan.id,
                "DRY_RUN",
                {
                    "conversion": conversion,
                    "read_only": True,
                    "adapter_evidence": self._adapter_evidence(),
                },
            )
            return run

    def approve(self, plan: Plan, digest: str, operator: str) -> dict:
        self._validate(plan)
        if digest != plan.id:
            raise EngineError("Approval must match the full immutable plan digest")
        self._run(plan)
        return self.ledger.approve(plan.id, operator)

    def apply(
        self,
        plan: Plan,
        operator: str,
        resume: bool = False,
        maintenance_window: bool = False,
        fail_after: int | None = None,
    ) -> dict:
        """Apply an approved additive stage; resume reconciles before skipping.

        ``fail_after`` injects a failure after N committed/checkpointed backfill
        operations. It is deliberately unavailable as a CLI argument.
        """
        self._validate(plan)
        if not operator.strip():
            raise EngineError("An explicit operator identity is required")
        if not maintenance_window:
            raise EngineError(
                "Explicit maintenance-window acknowledgement is required; freeze relevant writers"
            )
        if self.adapter.read_only:
            raise EngineError("Execution requires an explicitly writable connection")
        if fail_after is not None and (isinstance(fail_after, bool) or fail_after < 1):
            raise EngineError("Failure injection count must be positive")
        run = self._run(plan)
        if run["approved_digest"] != plan.id:
            raise EngineError("The exact plan has not been approved")
        recoverable = {"FAILED", "VERIFICATION_FAILED", "RUNNING", "VERIFYING"}
        if run["status"] in recoverable and not resume:
            raise EngineError("Partial or failed execution requires explicit resume")
        if run["status"] not in recoverable | {"APPROVED", "COMPLETE"}:
            raise EngineError("Run is not approved for execution")
        started = time.monotonic()
        with self.adapter.lock(plan.current.table):
            try:
                self._source_unchanged(plan, run)
                if run["status"] == "COMPLETE":
                    result = verify(self.adapter, plan, run["baseline"])
                    if result.get("passed") is not True:
                        self.ledger.transition(
                            plan.id,
                            "VERIFICATION_FAILED",
                            operator=operator,
                            verification=result,
                            error="Previously completed target failed re-verification",
                        )
                        raise VerificationFailed(
                            "Previously completed target failed re-verification"
                        )
                    self.ledger.record_event(
                        plan.id, "IDEMPOTENT_RECHECK", {"verification": result}
                    )
                    return self.ledger.get(plan.id)
                if run["status"] == "APPROVED":
                    self._targets_absent(plan)
                else:
                    self._reconcile_target_ownership(plan)
                self.ledger.transition(plan.id, "RUNNING", operator=operator)
                self.ledger.record_event(
                    plan.id,
                    "EXECUTION_STARTED",
                    {
                        "operator": operator,
                        "resume": resume,
                        "maintenance_window": True,
                        "idempotency": "deterministic replacement assignment from frozen source",
                    },
                )
                completed = run["checkpoints"]
                for operation in plan.migration.operations:
                    action_id = f"add:{operation.target}"
                    schema = self.adapter.schema(plan.current.table)
                    if action_id in completed and schema.get(operation.target) == normalize_type(
                        operation.data_type
                    ):
                        self.ledger.record_event(
                            plan.id, "ACTION_RECONCILED", {"action_id": action_id, "skipped": True}
                        )
                        continue
                    self.ledger.record_event(plan.id, "ACTION_STARTED", {"action_id": action_id})
                    with self.adapter.transaction():
                        self.adapter.add_column(plan.current.table, operation)
                    # A crash here leaves a committed action without a checkpoint.
                    # add_column must reconcile its type and be safe to replay.
                    self.ledger.checkpoint(
                        plan.id,
                        action_id,
                        {
                            "kind": "add_column",
                            "target": operation.target,
                            "adapter_evidence": self._adapter_evidence(),
                        },
                    )
                applied = 0
                for partition in plan.migration.bounds.partitions():
                    for operation in plan.migration.operations:
                        action_id = f"backfill:{operation.target}:{partition}"
                        if action_id in completed:
                            check = verify_operation(self.adapter, plan, operation, partition)
                            if check.get("passed") is True:
                                self.ledger.record_event(
                                    plan.id,
                                    "ACTION_RECONCILED",
                                    {
                                        "action_id": action_id,
                                        "skipped": True,
                                        "verification": check,
                                    },
                                )
                                continue
                            self.ledger.record_event(
                                plan.id,
                                "ACTION_REPLAY_REQUIRED",
                                {
                                    "action_id": action_id,
                                    "verification": check,
                                },
                            )
                        self.ledger.record_event(
                            plan.id,
                            "ACTION_STARTED",
                            {
                                "action_id": action_id,
                                "partition": partition,
                            },
                        )
                        action_started = time.monotonic()
                        with self.adapter.transaction():
                            self.adapter.backfill(
                                plan.current.table, operation, plan.migration.bounds, partition
                            )
                            check = verify_operation(self.adapter, plan, operation, partition)
                            if check.get("passed") is not True:
                                raise VerificationFailed(
                                    f"Partition verification failed for {action_id}: {check}"
                                )
                        # Checkpoint follows target commit, never precedes it.
                        self.ledger.checkpoint(
                            plan.id,
                            action_id,
                            {
                                "kind": "backfill",
                                "partition": partition,
                                "target": operation.target,
                                "verification": check,
                                "duration_seconds": time.monotonic() - action_started,
                                "adapter_evidence": self._adapter_evidence(),
                            },
                        )
                        applied += 1
                        if fail_after is not None and applied >= fail_after:
                            raise InjectedFailure(
                                f"Injected failure after {applied} committed backfill operation(s)"
                            )
                self.ledger.transition(plan.id, "VERIFYING", operator=operator)
                result = verify(self.adapter, plan, run["baseline"])
                if result.get("passed") is not True:
                    self.ledger.transition(
                        plan.id,
                        "VERIFICATION_FAILED",
                        operator=operator,
                        verification=result,
                        error="Required final verification did not pass",
                    )
                    raise VerificationFailed(
                        "Required final verification did not pass; inspect verification evidence"
                    )
                self.ledger.record_event(
                    plan.id,
                    "EXECUTION_FINISHED",
                    {
                        "duration_seconds": time.monotonic() - started,
                        "verification": result,
                        "adapter_evidence": self._adapter_evidence(),
                    },
                )
                return self.ledger.transition(
                    plan.id, "COMPLETE", operator=operator, verification=result
                )
            except BaseException as exc:
                # Driver messages may embed row values, SQL, or connection
                # strings. Persist classifications and our own safe messages.
                safe_error = (
                    str(exc)
                    if isinstance(exc, EngineError)
                    else "Adapter or ledger operation failed; inspect the local exception without publishing credentials or source rows"
                )
                current = self.ledger.get(plan.id)
                if current and current["status"] != "VERIFICATION_FAILED":
                    status = (
                        "VERIFICATION_FAILED" if isinstance(exc, VerificationFailed) else "FAILED"
                    )
                    self.ledger.transition(
                        plan.id,
                        status,
                        operator=operator,
                        error=f"{type(exc).__name__}: {safe_error}",
                    )
                self.ledger.record_event(
                    plan.id,
                    "EXECUTION_FAILED",
                    {
                        "error_type": type(exc).__name__,
                        "error": safe_error,
                        "duration_seconds": time.monotonic() - started,
                        "recovery": "Inspect failure, restore quiescent source, then resume the same approved plan. Do not replace its baseline.",
                    },
                )
                raise

    def verify(self, plan: Plan) -> dict:
        """Re-evaluate policy without writing target data or erasing failed history."""
        self._validate(plan)
        run = self._run(plan)
        with nullcontext() if self.adapter.read_only else self.adapter.lock(plan.current.table):
            try:
                result = verify(self.adapter, plan, run["baseline"])
            except Exception as exc:
                result = {
                    "passed": False,
                    "checks": [
                        {
                            "name": "verification_error",
                            "passed": False,
                            "error": type(exc).__name__,
                        }
                    ],
                }
            self.ledger.record_event(plan.id, "VERIFICATION", {"result": result})
            if result.get("passed") is not True and run["status"] != "PREPARED":
                self.ledger.transition(
                    plan.id,
                    "VERIFICATION_FAILED",
                    verification=result,
                    error="Required verification failed or errored",
                )
            return result
