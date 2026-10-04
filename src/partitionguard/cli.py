"""Human and machine interfaces to the deterministic engine."""

from __future__ import annotations

import json
import os
from functools import wraps
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .contracts import load_contract, load_migration
from .models import canonical_json
from .planning import diff as schema_diff
from .planning import load_plan, save_plan
from .planning import plan as create_plan

app = typer.Typer(
    no_args_is_help=True,
    invoke_without_command=True,
    help="Safely plan, execute, recover, and verify data-product changes.",
)
console = Console(highlight=False)


def guarded(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (typer.Exit, typer.Abort):
            raise
        except Exception as exc:
            from .engine import EngineError, VerificationFailed

            code = (
                5
                if isinstance(exc, VerificationFailed)
                else 6
                if isinstance(exc, EngineError)
                else 2
                if isinstance(exc, (ValueError, FileNotFoundError))
                else 4
            )
            message = (
                str(exc)
                if isinstance(exc, (ValueError, EngineError, FileNotFoundError))
                else f"{type(exc).__name__}: operation failed; inspect local database diagnostics"
            )
            typer.echo(
                canonical_json(
                    {"error": message, "error_type": type(exc).__name__, "exit_code": code}
                ),
                err=True,
            )
            raise typer.Exit(code) from None

    return wrapper


def output(value, machine=False):
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if machine:
        typer.echo(canonical_json(value))
    else:
        console.print_json(json.dumps(value, default=str), indent=2)


def render_plan(p):
    console.print(
        f"[bold]PartitionGuard[/bold] · {p.current.product} · {p.current.version} → {p.proposed.version}"
    )
    console.print(f"Plan: {p.id}\nAdapter: {p.adapter} · Risk: {p.risk.level}")
    table = Table("Change", "Before", "After", "Compatibility")
    for c in p.changes:

        def display(value):
            if isinstance(value, dict) and "data_type" in value:
                return value["data_type"] + (" nullable" if value.get("nullable") else " required")
            return "—" if value is None else str(value)

        table.add_row(c.path, display(c.before), display(c.after), c.compatibility)
    console.print(table)
    console.print(f"Direct consumers: {', '.join(p.impact.direct) or 'none declared'}")
    console.print(f"Transitive consumers: {', '.join(p.impact.transitive) or 'none declared'}")
    console.print(
        f"Affected transformations: {len(p.impact.transformations)} · SLA nodes: {len(p.impact.slas)}"
    )
    console.print(
        "Impact reflects declared dependencies; consumer discovery is incomplete by design."
    )
    if p.migration:
        b = p.migration.bounds
        console.print(
            f"Backfill: {len(b.partitions())} daily partitions · [{b.start}, {b.end}) · row cap {b.max_total_rows:,}"
        )
        if p.migration.estimated_bytes is not None:
            console.print(
                f"Estimated volume: {p.migration.estimated_bytes:,} bytes (operator estimate; not a scan limit)"
            )
        console.print(
            f"Actions: {len(p.actions)} · source columns retained · writer maintenance window required"
        )
    for reason in p.risk.reasons:
        console.print(f"• {reason}")
    console.print(
        "Execution: BLOCKED"
        if p.blockers
        else "Execution: requires dry-run and explicit exact-plan approval"
    )


def summary(p):
    def cell(value):
        return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")

    lines = [
        f"## PartitionGuard: {cell(p.current.product)}",
        "",
        f"Version: {cell(p.current.version)} → {cell(p.proposed.version)}",
        f"Risk: **{p.risk.level}**",
        f"Plan: `{p.id}`",
        "",
        "| Change | Compatibility | Reason |",
        "| --- | --- | --- |",
    ]
    lines += [f"| {cell(c.path)} | {c.compatibility} | {cell(c.reason)} |" for c in p.changes]
    lines += [
        "",
        f"Direct: {cell(', '.join(p.impact.direct)) or 'none declared'}",
        f"Transitive: {cell(', '.join(p.impact.transitive)) or 'none declared'}",
        "",
        "Declared lineage only; no automatic consumer discovery.",
    ]
    if p.migration:
        lines += [
            "",
            f"Backfill: {len(p.migration.bounds.partitions())} partitions; {len(p.actions)} actions.",
        ]
    if p.blockers:
        lines += ["", "Blocked:"] + [f"- {cell(b)}" for b in p.blockers]
    return "\n".join(lines) + "\n"


@app.callback()
def main(version: Annotated[bool, typer.Option("--version", is_eager=True)] = False):
    if version:
        typer.echo(__version__)
        raise typer.Exit()


@app.command()
@guarded
def init(directory: Annotated[Path, typer.Argument()] = Path("partitionguard-example")):
    """Write ODCS examples and bounded recipes. Does not create a database."""
    from .examples import initialize

    initialize(directory)
    typer.echo(f"Created ODCS contracts and bounded migration recipes in {directory}")


def load_input(path, object_name=None, contract_format="odcs"):
    if contract_format == "odcs":
        return load_contract(path, object_name)
    from .models import Contract
    from .plugins import load_plugin

    instance = load_plugin("contracts", contract_format)()
    value = instance.load(str(path), object_name=object_name)
    return Contract.model_validate(
        value.model_dump(mode="json") if isinstance(value, Contract) else value
    )


@app.command()
@guarded
def validate(
    contract: Path,
    object_name: str | None = None,
    contract_format: str = "odcs",
    json_output: Annotated[bool, typer.Option("--json")] = False,
):
    """Normalize an ODCS relational subset; unsupported semantics fail closed."""
    c = load_input(contract, object_name, contract_format)
    output(c, json_output)
    if c.unsupported:
        raise typer.Exit(3)


@app.command()
@guarded
def diff(
    current: Path,
    proposed: Path,
    adapter: str = "duckdb",
    object_name: str | None = None,
    contract_format: str = "odcs",
    json_output: Annotated[bool, typer.Option("--json")] = False,
):
    """Compare contracts without connecting to data."""
    output(
        [
            c.model_dump(mode="json")
            for c in schema_diff(
                load_input(current, object_name, contract_format),
                load_input(proposed, object_name, contract_format),
                adapter,
            )
        ],
        json_output,
    )


@app.command()
@guarded
def plan(
    current: Path,
    proposed: Path,
    migration: Path | None = None,
    adapter: str = "duckdb",
    object_name: str | None = None,
    contract_format: str = "odcs",
    out: Annotated[Path | None, typer.Option("--output", "-o")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
    ci: bool = False,
    summary_file: Annotated[Path | None, typer.Option("--summary")] = None,
):
    """Create an immutable plan. --ci exits 3 when policy blocks the change."""
    p = create_plan(
        load_input(current, object_name, contract_format),
        load_input(proposed, object_name, contract_format),
        adapter,
        load_migration(migration) if migration else None,
    )
    if out:
        save_plan(p, out)
    if summary_file:
        summary_file.write_text(summary(p))
    if json_output:
        output({"plan_id": p.id, "plan": p.model_dump(mode="json")}, True)
    else:
        render_plan(p)
    if ci and p.blockers:
        raise typer.Exit(3)


@app.command()
@guarded
def impact(
    current: Path,
    proposed: Path,
    object_name: str | None = None,
    contract_format: str = "odcs",
    json_output: Annotated[bool, typer.Option("--json")] = False,
):
    """Report downstream impact from explicitly declared dependencies."""
    output(
        create_plan(
            load_input(current, object_name, contract_format),
            load_input(proposed, object_name, contract_format),
        ).impact,
        json_output,
    )


def engine_context(plan_file, connection, ledger, read_only):
    from contextlib import ExitStack

    from .adapters import open_adapter
    from .engine import Engine
    from .ledger import Ledger

    p = load_plan(plan_file)
    connection = connection or os.environ.get("PARTITIONGUARD_CONNECTION")
    if not connection:
        raise ValueError(
            "Set PARTITIONGUARD_CONNECTION or --connection; prefer environment for credentials"
        )
    stack = ExitStack()
    try:
        adapter = stack.enter_context(open_adapter(p.adapter, connection, read_only=read_only))
        journal = stack.enter_context(Ledger(ledger))
        return stack, p, Engine(adapter, journal)
    except BaseException:
        stack.close()
        raise


@app.command()
@guarded
def approve(
    plan_file: Path,
    digest: Annotated[str, typer.Option()],
    operator: Annotated[str, typer.Option()],
    connection: str | None = None,
    ledger: Path = Path(".partitionguard/ledger.sqlite"),
    json_output: Annotated[bool, typer.Option("--json")] = False,
):
    """Approve the full digest for a prepared target. Writes only the ledger."""
    stack, p, engine = engine_context(plan_file, connection, ledger, True)
    with stack:
        result = engine.approve(p, digest, operator)
        output(
            {
                "plan_id": p.id,
                "status": result["status"],
                "approved_digest": result["approved_digest"],
            },
            json_output,
        )


@app.command()
@app.command("backfill")
@guarded
def migrate(
    plan_file: Path,
    connection: str | None = None,
    ledger: Path = Path(".partitionguard/ledger.sqlite"),
    execute: bool = False,
    resume: bool = False,
    maintenance_window: bool = False,
    operator: str = "",
    json_output: Annotated[bool, typer.Option("--json")] = False,
):
    """Dry-run by default. Writes need --execute, approval, operator and maintenance window."""
    if resume and not execute:
        raise ValueError("--resume requires --execute")
    stack, p, engine = engine_context(plan_file, connection, ledger, not execute)
    with stack:
        result = (
            engine.apply(p, operator, resume, maintenance_window) if execute else engine.dry_run(p)
        )
        output(
            {
                "plan_id": p.id,
                "status": result["status"],
                "checkpoints": len(result.get("checkpoints", {})),
                "verification": result.get("verification"),
                "dry_run": not execute,
            },
            json_output,
        )


@app.command()
@guarded
def verify(
    plan_file: Path,
    connection: str | None = None,
    ledger: Path = Path(".partitionguard/ledger.sqlite"),
    json_output: Annotated[bool, typer.Option("--json")] = False,
):
    """Recheck required policies without changing target data."""
    stack, p, engine = engine_context(plan_file, connection, ledger, True)
    with stack:
        result = engine.verify(p)
        output(result, json_output)
        if result.get("passed") is not True:
            raise typer.Exit(5)


@app.command()
@guarded
def status(
    plan_file: Path,
    ledger: Path = Path(".partitionguard/ledger.sqlite"),
    json_output: Annotated[bool, typer.Option("--json")] = False,
):
    """Read durable run state, checkpoints and recovery information."""
    from .ledger import Ledger

    if not ledger.exists():
        raise ValueError("Ledger does not exist")
    with Ledger(ledger) as journal:
        result = journal.get(load_plan(plan_file).id)
        if result is None:
            raise ValueError("No prepared run for this plan")
        output(result, json_output)


@app.command()
@guarded
def history(
    ledger: Path = Path(".partitionguard/ledger.sqlite"),
    plan_file: Path | None = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
):
    """List runs or append-only audit events for a selected plan."""
    from .ledger import Ledger

    if not ledger.exists():
        raise ValueError("Ledger does not exist")
    with Ledger(ledger) as journal:
        output(
            journal.events(load_plan(plan_file).id) if plan_file else journal.history(), json_output
        )


@app.command("plugins")
@guarded
def plugins_list():
    """List installed extensions without loading plugin code."""
    from .plugins import ROLES, available

    output({role: available(role) for role in sorted(ROLES)}, True)


@app.command("lineage-import")
@guarded
def lineage_import(
    events: Path,
    mapping: Path | None = None,
    provider: str = "openlineage",
    out: Annotated[Path | None, typer.Option("--output")] = None,
):
    """Import offline completed lineage events with provenance; never infer missing edges."""
    from .lineage import OpenLineageProvider
    from .plugins import load_plugin

    if events.stat().st_size > 10_000_000:
        raise ValueError("Lineage file exceeds 10 MB limit")
    source = json.loads(events.read_text())
    if mapping and mapping.stat().st_size > 10_000_000:
        raise ValueError("Lineage mapping file exceeds 10 MB limit")
    names = json.loads(mapping.read_text()) if mapping else {}
    instance = (
        OpenLineageProvider() if provider == "openlineage" else load_plugin("lineage", provider)()
    )
    result = instance.ingest(source, mapping=names)
    if out:
        out.write_text(canonical_json(result) + "\n")
    output(result, True)


@app.command("aws-inspect")
@guarded
def aws_inspect(
    database: str,
    table: str,
    bucket: Annotated[str, typer.Option()],
    prefix: Annotated[str, typer.Option()],
    max_objects: int = 1000,
    max_bytes: int = 10_000_000_000,
    region: str | None = None,
    catalog_id: str | None = None,
    max_pages: int = 100,
):
    """Read a Glue schema and capped S3 prefix inventory. AWS execution is unsupported."""
    from .aws import AWSInventory

    result = AWSInventory.connect(region).inspect(
        database, table, bucket, prefix, max_objects, max_bytes, catalog_id, max_pages
    )
    output(result, True)


if __name__ == "__main__":
    app()
