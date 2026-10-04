"""Pure deterministic planning; no SDK, SQL connection, clock, or LLM dependency."""

from __future__ import annotations

from collections import deque
from pathlib import Path

from .contracts import read_yaml
from .models import (
    Action,
    Change,
    Contract,
    Dependency,
    Impact,
    MigrationSpec,
    Plan,
    Risk,
    canonical_json,
)

SUPPORTED_ADAPTERS = {"duckdb", "postgres", "delta"}
CAST_PAIRS = {
    (t, t) for t in ("STRING", "INTEGER", "BIGINT", "DOUBLE", "BOOLEAN", "DATE", "TIMESTAMP")
} | {
    ("STRING", "INTEGER"),
    ("STRING", "BIGINT"),
    ("INTEGER", "BIGINT"),
    ("INTEGER", "STRING"),
    ("BIGINT", "STRING"),
}


def compatibility_profile(adapter: str) -> str:
    """Resolve an explicitly selected trusted storage plugin's rule profile.

    Reusing a profile is an explicit plugin author's compatibility claim, not
    automatic certification. The core still owns all planning safety gates.
    Builtin adapter selection never imports third-party entry points.
    """
    if adapter in SUPPORTED_ADAPTERS:
        return adapter
    from .plugins import load_plugin

    factory = load_plugin("storage", adapter)
    profile = getattr(factory, "COMPATIBILITY_PROFILE", None)
    if profile not in SUPPORTED_ADAPTERS:
        raise ValueError(
            f"Storage plugin {adapter!r} must declare a supported COMPATIBILITY_PROFILE"
        )
    return profile


def diff(current: Contract, proposed: Contract, adapter: str = "duckdb") -> tuple[Change, ...]:
    profile = compatibility_profile(adapter)
    old, new = current.column_map, proposed.column_map
    changes = []

    def add(kind, path, before, after, level, reason):
        changes.append(
            Change(
                kind=kind, path=path, before=before, after=after, compatibility=level, reason=reason
            )
        )

    for name in sorted(set(old) | set(new)):
        a, b = old.get(name), new.get(name)
        path = f"columns.{name}"
        if a is None:
            add(
                "add_column",
                path,
                None,
                b.model_dump(mode="json"),
                "SAFE" if b.nullable else "BREAKING",
                "Nullable addition preserves existing projections"
                if b.nullable
                else "Required new values need a migration and coverage proof",
            )
        elif b is None:
            add(
                "drop_column",
                path,
                a.model_dump(mode="json"),
                None,
                "BREAKING",
                "Existing readers may reference removed field",
            )
        else:
            if a.data_type != b.data_type:
                widening = (
                    a.data_type == "INTEGER"
                    and b.data_type == "BIGINT"
                    and profile in {"duckdb", "postgres"}
                )
                add(
                    "type_change",
                    path + ".type",
                    a.data_type,
                    b.data_type,
                    "SAFE" if widening else "BREAKING",
                    f"{adapter}: integer widening preserves values; typed clients still need review"
                    if widening
                    else f"{adapter}: representation change has no general backwards-compatibility guarantee",
                )
            if a.nullable != b.nullable:
                add(
                    "nullability",
                    path + ".nullable",
                    a.nullable,
                    b.nullable,
                    "BREAKING" if a.nullable else "WARNING",
                    "Existing nulls require coverage and remediation"
                    if a.nullable
                    else "Producers may now emit nulls to strict consumers",
                )
            if a.enum != b.enum:
                removed = bool(a.enum) and bool(b.enum) and not set(a.enum) <= set(b.enum)
                introduced = not a.enum and bool(b.enum)
                add(
                    "enum",
                    path + ".enum",
                    a.enum,
                    b.enum,
                    "BREAKING" if removed or introduced else "WARNING",
                    "Allowed values became stricter"
                    if removed or introduced
                    else "Enumerated value policy changed; review consumers",
                )
            if a.description != b.description:
                add(
                    "metadata",
                    path + ".description",
                    a.description,
                    b.description,
                    "SAFE",
                    "Descriptive metadata only",
                )
    for attr, level, reason in (
        ("primary_key", "BREAKING", "Row identity changed"),
        ("business_keys", "BREAKING", "Business identity changed"),
        (
            "partition_by",
            "BREAKING",
            "Partition rewrite semantics require an explicit supported migration",
        ),
        (
            "quality",
            "WARNING",
            "Quality requirements changed; review stricter acceptance thresholds",
        ),
        ("sla", "WARNING", "Service commitment changed; workload and freshness need review"),
        ("retention", "BREAKING", "Retention changes can remove historical data"),
        ("table", "BREAKING", "Physical dataset identity changed"),
        ("dependencies", "WARNING", "Declared dependency information changed"),
        ("compatibility", "WARNING", "Compatibility policy changed"),
        ("owner", "SAFE", "Ownership metadata changed"),
    ):
        a, b = getattr(current, attr), getattr(proposed, attr)
        if a != b:
            # Use JSON mode for nested models; tuples become JSON arrays at serialization.
            add(
                attr,
                attr,
                current.model_dump(mode="json")[attr],
                proposed.model_dump(mode="json")[attr],
                level,
                reason,
            )
    if current.metadata.get("description") != proposed.metadata.get("description"):
        add(
            "metadata",
            "description",
            current.metadata.get("description"),
            proposed.metadata.get("description"),
            "SAFE",
            "Descriptive metadata only",
        )
    return tuple(changes)


class DependencyGraph:
    def __init__(self, nodes: tuple[Dependency, ...]):
        self.nodes = {n.name: n for n in nodes}
        if len(self.nodes) != len(nodes):
            raise ValueError("Duplicate dependency node names")
        self.children: dict[str, set[str]] = {}
        for node in nodes:
            for source in node.upstream:
                self.children.setdefault(source, set()).add(node.name)

    def impact(self, product: str, changed_columns: set[str] | None = None) -> Impact:
        direct = set(self.children.get(product, set()))
        # Column hints can narrow direct edges only; transitive semantics are conservative.
        if changed_columns:
            direct = {
                n
                for n in direct
                if not self.nodes[n].columns or set(self.nodes[n].columns) & changed_columns
            }
        seen, queue = {product}, deque(sorted(direct))
        reached = set()
        while queue:
            name = queue.popleft()
            if name in seen:
                continue
            seen.add(name)
            reached.add(name)
            queue.extend(sorted(self.children.get(name, set()) - seen))
        unknown = {
            p
            for n in self.nodes.values()
            for p in n.upstream
            if p != product and p not in self.nodes
        }
        selected = [self.nodes[n] for n in reached]
        return Impact(
            direct=tuple(sorted(direct - {product})),
            transitive=tuple(sorted(reached - direct)),
            transformations=tuple(
                sorted(n.name for n in selected if n.kind in {"sql_model", "transformation"})
            ),
            quality_checks=tuple(sorted(n.name for n in selected if n.kind == "quality")),
            slas=tuple(sorted(n.name for n in selected if n.sla)),
            unknown=tuple(sorted(unknown)),
        )


def classify_risk(changes, impact: Impact, migration: MigrationSpec | None, blockers=()) -> Risk:
    reasons, level = [], "LOW"
    if any(c.compatibility == "BREAKING" for c in changes):
        level = "HIGH"
        reasons.append("Breaking contract changes")
    if any(c.compatibility == "WARNING" for c in changes):
        level = "MEDIUM" if level == "LOW" else level
        reasons.append("Contract requirements or consumer expectations changed")
    if impact.direct or impact.transitive:
        level = "MEDIUM" if level == "LOW" else level
        reasons.append(
            f"{len(impact.direct) + len(impact.transitive)} declared downstream nodes affected"
        )
    if impact.slas:
        level = "HIGH"
        reasons.append("Downstream SLA commitments are affected")
    if impact.unknown:
        level = "HIGH"
        reasons.append("Dependency graph contains unknown upstream nodes")
    if migration and migration.operations:
        level = "MEDIUM" if level == "LOW" else level
        reasons.append("Historical data writes require explicit approval")
        if len(migration.bounds.partitions()) > 100:
            level = "HIGH"
            reasons.append("More than 100 partition batches")
    if blockers:
        level = "BLOCKING"
        reasons.extend(blockers)
    return Risk(
        level=level,
        reasons=tuple(
            reasons or ["No material compatibility or workload risk detected within declared scope"]
        ),
    )


def plan(
    current: Contract, proposed: Contract, adapter="duckdb", migration: MigrationSpec | None = None
) -> Plan:
    # Pydantic model_copy/model_construct deliberately bypass validation, and
    # frozen models may contain mutable metadata dictionaries. Revalidate at
    # this public safety boundary instead of trusting Python object provenance.
    current = Contract.model_validate(current.model_dump(mode="json"))
    proposed = Contract.model_validate(proposed.model_dump(mode="json"))
    migration = (
        MigrationSpec.model_validate(migration.model_dump(mode="json"))
        if migration is not None
        else None
    )
    changes = diff(current, proposed, adapter)
    blockers = []
    if current.product != proposed.product or current.metadata.get(
        "odcs_id"
    ) != proposed.metadata.get("odcs_id"):
        blockers.append("Source and target must describe the same data product")
    if changes and current.version == proposed.version:
        blockers.append("Changed contracts must use a new version")
    for text in sorted(set(current.unsupported + proposed.unsupported)):
        blockers.append(text)
    if current.table != proposed.table:
        blockers.append("Moving physical tables is unsupported")
    # Retain both contracts' consumer evidence. A proposed declaration cannot
    # erase an existing edge during the same change whose impact we assess.
    nodes: dict[str, Dependency] = {}
    node_roles: dict[str, set[str]] = {}
    for node in current.dependencies + proposed.dependencies:
        node_roles.setdefault(node.name, set()).add(node.kind)
        previous = nodes.get(node.name)
        if previous is not None:
            node = Dependency(
                name=node.name,
                kind=previous.kind,
                upstream=tuple(sorted(set(previous.upstream) | set(node.upstream))),
                columns=(
                    tuple(sorted(set(previous.columns) | set(node.columns)))
                    if previous.columns and node.columns
                    else ()
                ),
                sla=previous.sla or node.sla,
            )
        nodes[node.name] = node
    material_changes = [
        change
        for change in changes
        if change.kind not in {"metadata", "owner", "dependencies", "compatibility"}
    ]
    changed_columns = {
        c.path.split(".")[1] for c in material_changes if c.path.startswith("columns.")
    }
    broad_change = any(not c.path.startswith("columns.") for c in material_changes)
    impact = (
        DependencyGraph(tuple(nodes.values())).impact(
            current.product, None if broad_change else changed_columns
        )
        if material_changes
        else Impact()
    )
    if material_changes:
        reached = set(impact.direct) | set(impact.transitive)
        impact = impact.model_copy(
            update={
                "transformations": tuple(
                    sorted(
                        name
                        for name in reached
                        if node_roles[name] & {"sql_model", "transformation"}
                    )
                ),
                "quality_checks": tuple(
                    sorted(name for name in reached if "quality" in node_roles[name])
                ),
            }
        )
    added = set(proposed.column_map) - set(current.column_map)
    physical = [
        c
        for c in changes
        if c.kind
        in {
            "drop_column",
            "type_change",
            "nullability",
            "primary_key",
            "business_keys",
            "partition_by",
            "retention",
            "enum",
        }
    ]
    for change in physical:
        blockers.append(
            f"{change.path}: in-place/retirement operation unsupported; use an additive stage, consumer migration, and separately reviewed retirement"
        )
    if proposed.compatibility == "strict" and any(
        c.kind != "metadata" and c.kind != "owner" for c in changes
    ):
        blockers.append("Strict compatibility policy rejects structural or requirement changes")
    if proposed.compatibility == "backward" and any(c.compatibility == "BREAKING" for c in changes):
        blockers.append("Backward compatibility policy rejects breaking changes")
    actions = []
    if migration is None:
        if added or physical:
            blockers.append(
                "Required migration manifest is absent; supply bounded operations and verification policy"
            )
    else:
        b = migration.bounds
        if (
            current.column_map.get(b.column) is None
            or current.column_map[b.column].data_type != "DATE"
        ):
            blockers.append("Bounds require an existing DATE column")
        if current.partition_by != (b.column,) or proposed.partition_by != current.partition_by:
            blockers.append(
                "Initial execution requires one unchanged declared date partition column"
            )
        targets = {op.target for op in migration.operations}
        if targets != added:
            blockers.append(
                "Every added column must have one operation; targets must be new additive columns"
            )
        for op in migration.operations:
            source, target = current.column_map.get(op.source), proposed.column_map.get(op.target)
            if not source or not target:
                blockers.append(f"{op.target}: source or target missing from contracts")
                continue
            if target.data_type != op.data_type:
                blockers.append(f"{op.target}: operation type differs from target contract")
            if not target.nullable:
                blockers.append(
                    f"{op.target}: initial additive stage must be nullable outside bounded scope"
                )
            if op.transform == "copy" and source.data_type != target.data_type:
                blockers.append(f"{op.target}: copy requires identical types; use explicit cast")
            if (source.data_type, target.data_type) not in CAST_PAIRS:
                blockers.append(
                    f"{op.target}: untested cast pair {source.data_type}->{target.data_type}"
                )
            actions.append(
                Action(
                    id=f"add:{op.target}",
                    kind="add_column",
                    operation=op,
                    description=f"Add nullable {op.target} {op.data_type}; table-level DDL may lock the table",
                )
            )
            for partition in b.partitions():
                actions.append(
                    Action(
                        id=f"backfill:{op.target}:{partition}",
                        kind="backfill",
                        operation=op,
                        partition=partition,
                        description=f"Assign {op.target} from {op.source} for {partition}; deterministic {op.transform}",
                    )
                )
        for check in migration.checks:
            names = set(check.columns) | {v for v in (check.column, check.reference) if v}
            if not names <= set(proposed.column_map):
                blockers.append("Verification check references columns absent from target")
        if not migration.operations and not changes:
            # Explicit verification-only plans remain useful, with no writes.
            actions.append(
                Action(
                    id="verify",
                    kind="verify",
                    description="Verify the explicitly bounded existing dataset",
                )
            )
        else:
            actions.append(
                Action(
                    id="verify",
                    kind="verify",
                    description="Verify schema, counts, source stability, partitions, keys, conversions and all required policy checks",
                )
            )
    unique_blockers = tuple(dict.fromkeys(blockers))
    return Plan(
        adapter=adapter,
        current=current,
        proposed=proposed,
        changes=changes,
        impact=impact,
        risk=classify_risk(changes, impact, migration, unique_blockers),
        migration=migration,
        actions=tuple(actions),
        blockers=unique_blockers,
    )


def validate_plan(value: Plan) -> None:
    original = canonical_json(value)
    value = Plan.model_validate(value.model_dump(mode="json"))
    if canonical_json(value) != original:
        raise ValueError("Plan contains noncanonical values; regenerate it with validated inputs")
    rebuilt = plan(value.current, value.proposed, value.adapter, value.migration)
    if canonical_json(rebuilt) != canonical_json(value):
        raise ValueError(
            "Plan contains inconsistent derived actions, risk, impact, or blockers; regenerate it"
        )


def save_plan(value: Plan, path: str | Path):
    Path(path).write_text(
        canonical_json({"plan_id": value.id, "plan": value.model_dump(mode="json")}) + "\n"
    )


def load_plan(path: str | Path) -> Plan:
    value = read_yaml(path)
    if set(value) != {"plan_id", "plan"}:
        raise ValueError("Expected an immutable plan artifact with plan_id and plan")
    parsed = Plan.model_validate(value["plan"])
    if parsed.id != value["plan_id"]:
        raise ValueError("Plan digest mismatch; file changed since generation")
    validate_plan(parsed)
    return parsed
