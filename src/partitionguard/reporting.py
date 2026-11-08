"""Portable, deliberately filtered snapshots of locally checked execution evidence."""

from __future__ import annotations

from datetime import datetime, timezone
from html import escape

from .ledger import Ledger
from .models import Plan, canonical_json
from .planning import validate_plan

LIMITATIONS = [
    "Snapshot of recorded evidence; exporting does not reverify the live dataset.",
    "Local ledger integrity was checked, but identities are self-reported and evidence is not signed.",
    "Additive migration stage only; consumer cutover and legacy retirement are not certified.",
    "Connection identity, source values, fingerprints, SQL, check observations and raw errors are omitted.",
    "Product, column, dependency and operator names remain visible; review before sharing.",
]


def _verification(result):
    if not isinstance(result, dict):
        return None
    checks = [
        {key: check.get(key) for key in ("name", "partition", "passed")}
        for check in result.get("checks", [])
    ]
    return {
        "passed": result.get("passed") is True,
        "checks": checks,
        "scope": "canary" if result.get("scope") == "canary" else "full",
    }


def build_report(plan: Plan, ledger: Ledger) -> dict:
    """Read verified projections, exporting only an explicit allowlist of fields."""
    validate_plan(plan)
    run = ledger.get(plan.id)  # Checks the event chain and all persisted projections.
    if run is None:
        raise ValueError("No prepared run for this plan")
    if run["plan"] != plan.model_dump(mode="json"):
        raise ValueError("Saved plan content does not match")
    timeline = []
    latest = _verification(run["verification"])
    if latest and latest["scope"] == "canary":
        latest = None
    canary = None
    for event in ledger.events(plan.id):
        detail = event["detail"]
        item = {
            "timestamp": event["timestamp"],
            "kind": event["kind"],
            "detail": {
                key: detail[key]
                for key in (
                    "status",
                    "operator",
                    "approved_digest",
                    "attempt",
                    "action_id",
                    "partition",
                    "resume",
                    "skipped",
                    "duration_seconds",
                    "error_type",
                    "review_hash",
                    "reason",
                    "requested_by",
                    "max_duration_seconds",
                )
                if key in detail
            },
        }
        result = (
            detail.get("result") if event["kind"] == "VERIFICATION" else detail.get("verification")
        )
        if isinstance(result, dict):
            item["verification"] = _verification(result)
            if result.get("scope") == "canary":
                canary = item["verification"]
            # A partition check cannot replace the final policy result.
            if result.get("scope") != "canary" and event["kind"] in {
                "VERIFICATION",
                "STATE",
                "IDEMPOTENT_RECHECK",
                "EXECUTION_FINISHED",
            }:
                latest = item["verification"]
        timeline.append(item)
    migration = plan.migration
    return {
        "report_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "plan_id": plan.id,
        "run_id": run["run_id"],
        "product": plan.current.product,
        "source_version": plan.current.version,
        "target_version": plan.proposed.version,
        "adapter": plan.adapter,
        "risk": plan.risk.level,
        "impact": plan.impact.model_dump(mode="json"),
        "status": run["status"],
        "attempts": run["attempts"],
        "started_at": run["started_at"],
        "finished_at": run["finished_at"],
        "scope": migration.bounds.model_dump(mode="json") if migration else None,
        "operations": [op.model_dump(mode="json") for op in migration.operations]
        if migration
        else [],
        "partitions": [
            {"partition": partition, "source_row_count": evidence["row_count"]}
            for partition, evidence in sorted(run["baseline"]["partitions"].items())
        ],
        "checkpoints": [
            {"action_id": action_id, "completed_at": checkpoint["completed_at"]}
            for action_id, checkpoint in run["checkpoints"].items()
        ],
        "latest_recorded_verification": latest,
        "canary_verification": canary,
        "execution_policy": {
            **{
                key: getattr(migration.execution, key)
                for key in (
                    "canary_partitions",
                    "max_duration_seconds",
                    "statement_timeout_seconds",
                    "lock_timeout_seconds",
                )
            },
            "canary_checks": [
                {"kind": check.kind, "column": check.column, "columns": list(check.columns)}
                for check in migration.execution.canary_checks
            ],
        }
        if migration and migration.execution
        else None,
        "control": {
            key: run["control"][key] for key in ("canary_review_hash", "continuation_approved")
        },
        "timeline": timeline,
        "limitations": list(LIMITATIONS),
    }


def render_html(report: dict) -> str:
    """Standalone HTML with no scripts, remote assets, or unescaped user content."""

    def text(value):
        return escape(str(value), quote=True)

    def block(value):
        return "<pre>" + text(canonical_json(value)) + "</pre>"

    rows = "".join(
        "<tr><td>"
        + text(event["timestamp"])
        + "</td><td>"
        + text(event["kind"])
        + "</td><td>"
        + block({key: value for key, value in event.items() if key not in {"timestamp", "kind"}})
        + "</td></tr>"
        for event in report["timeline"]
    )
    sections = "".join(
        "<section><h2>" + title + "</h2>" + block(report[key]) + "</section>"
        for title, key in (
            ("Approved scope", "scope"),
            ("Operations", "operations"),
            ("Declared impact", "impact"),
            ("Partitions", "partitions"),
            ("Persisted checkpoints", "checkpoints"),
            ("Latest recorded verification", "latest_recorded_verification"),
            ("Canary verification", "canary_verification"),
            ("Execution policy", "execution_policy"),
            ("Continuation approval", "control"),
        )
    )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'\">"
        "<title>PartitionGuard migration evidence</title><style>"
        "body{font:16px/1.5 system-ui,sans-serif;background:#f4f6fa;color:#182438;max-width:1100px;margin:2rem auto;padding:0 1rem}"
        "section,header{background:white;border:1px solid #d9e0eb;border-radius:10px;padding:1.3rem;margin:1rem 0}"
        "h1,h2{line-height:1.2}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}"
        "table{width:100%;border-collapse:collapse}td,th{text-align:left;vertical-align:top;padding:.6rem;border-bottom:1px solid #d9e0eb}"
        "</style></head><body><header><p>PARTITIONGUARD · MIGRATION EVIDENCE</p><h1>"
        + text(report["product"])
        + "</h1><p>"
        + text(report["source_version"])
        + " → "
        + text(report["target_version"])
        + " · <strong>"
        + text(report["status"])
        + "</strong> · Attempts: "
        + text(report["attempts"])
        + "</p>"
        + block(
            {
                key: report[key]
                for key in (
                    "plan_id",
                    "run_id",
                    "adapter",
                    "risk",
                    "generated_at",
                    "started_at",
                    "finished_at",
                )
            }
        )
        + "</header><section><h2>Evidence boundaries</h2><ul>"
        + "".join("<li>" + text(item) + "</li>" for item in report["limitations"])
        + "</ul></section>"
        + sections
        + "<section><h2>Execution timeline</h2><table><thead><tr><th>Time (UTC)</th><th>Event</th><th>Evidence</th></tr></thead><tbody>"
        + rows
        + "</tbody></table></section></body></html>\n"
    )
