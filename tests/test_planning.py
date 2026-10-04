import json
from datetime import date, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from partitionguard.models import Bounds, Column, Dependency, Operation
from partitionguard.planning import (
    DependencyGraph,
    classify_risk,
    diff,
    load_plan,
    plan,
    save_plan,
    validate_plan,
)


def test_add_nullable_safe(contracts):
    assert diff(*contracts)[0].compatibility == "SAFE"


def test_breaking_drop_and_type(contracts):
    current, _ = contracts
    drop = current.model_copy(update={"version": "2", "columns": current.columns[:-1]})
    assert diff(current, drop)[0].compatibility == "BREAKING"
    change = current.model_copy(
        update={
            "version": "2",
            "columns": current.columns[:-1] + (Column(name="code", data_type="INTEGER"),),
        }
    )
    assert diff(current, change)[0].compatibility == "BREAKING"
    assert plan(current, change).blockers


def test_widening_adapter_aware(contracts):
    current, _ = contracts
    a = current.model_copy(
        update={
            "columns": (Column(name="id", data_type="INTEGER", nullable=False),)
            + current.columns[1:]
        }
    )
    assert diff(a, current, "postgres")[0].compatibility == "SAFE"
    assert diff(a, current, "delta")[0].compatibility == "BREAKING"


def test_plan_has_executable_bounded_actions(contracts, migration):
    p = plan(*contracts, migration=migration)
    assert not p.blockers
    assert [a.id for a in p.actions] == [
        "add:code_v2",
        "backfill:code_v2:2026-01-01",
        "backfill:code_v2:2026-01-02",
        "backfill:code_v2:2026-01-03",
        "verify",
    ]
    assert p.risk.level == "MEDIUM"
    validate_plan(p)


def test_missing_manifest_blocked(contracts):
    assert plan(*contracts).risk.level == "BLOCKING"


def test_forged_actions_and_digest_rejected(contracts, migration, tmp_path):
    p = plan(*contracts, migration=migration)
    with pytest.raises(ValueError):
        validate_plan(p.model_copy(update={"actions": ()}))
    f = tmp_path / "plan.json"
    save_plan(p, f)
    assert load_plan(f) == p
    d = json.loads(f.read_text())
    d["plan"]["migration"]["bounds"]["max_total_rows"] += 1
    f.write_text(json.dumps(d))
    with pytest.raises(ValueError, match="digest"):
        load_plan(f)


def test_digest_changes_for_verification_policy(contracts, migration):
    from partitionguard.models import Check

    p = plan(*contracts, migration=migration)
    m = migration.model_copy(
        update={"checks": (Check(kind="null_rate", column="code_v2", threshold=0.1),)}
    )
    assert plan(*contracts, migration=m).id != p.id


def test_key_and_partition_and_retention_changes_blocked(contracts, migration):
    a, b = contracts
    for name, value in [
        ("primary_key", ("code",)),
        ("partition_by", ("id",)),
        ("retention", {"days": 7}),
    ]:
        assert plan(a, b.model_copy(update={name: value}), migration=migration).blockers


def test_nullability_and_enum(contracts):
    a, _ = contracts
    b = a.model_copy(
        update={
            "columns": a.columns[:-1]
            + (Column(name="code", data_type="STRING", nullable=False, enum=("1",)),)
        }
    )
    assert all(c.compatibility == "BREAKING" for c in diff(a, b))


@pytest.mark.parametrize(
    "update",
    [
        {"end": date(2026, 1, 1)},
        {"start": date(2027, 1, 1)},
        {"max_partitions": 1},
        {"max_total_rows": 0},
    ],
)
def test_invalid_bounds(update):
    params = dict(column="day", start=date(2026, 1, 1), end=date(2026, 1, 4))
    params.update(update)
    with pytest.raises(ValueError):
        Bounds(**params)


def test_unsafe_target_and_cast(contracts, migration):
    with pytest.raises(ValueError):
        Operation(source="code", target="code", data_type="INTEGER")
    a, b = contracts
    b = b.model_copy(
        update={"columns": b.columns[:-1] + (Column(name="code_v2", data_type="DOUBLE"),)}
    )
    m = migration.model_copy(
        update={"operations": (Operation(source="code", target="code_v2", data_type="DOUBLE"),)}
    )
    assert "untested cast" in " ".join(plan(a, b, migration=m).blockers)


def test_graph_diamond_cycle_and_categories():
    nodes = (
        Dependency(name="a", upstream=("root",), kind="sql_model"),
        Dependency(name="b", upstream=("root",)),
        Dependency(name="c", upstream=("a", "b"), kind="quality", sla=True),
        Dependency(name="root", upstream=("c",)),
    )
    result = DependencyGraph(nodes).impact("root")
    assert result.direct == ("a", "b") and result.transitive == ("c",)
    assert (
        result.transformations == ("a",)
        and result.quality_checks == ("c",)
        and result.slas == ("c",)
    )
    assert classify_risk((), result, None).level == "HIGH"


def test_column_hints_and_unknowns():
    graph = DependencyGraph(
        (
            Dependency(name="a", upstream=("root",), columns=("x",)),
            Dependency(name="b", upstream=("missing",)),
        )
    )
    assert not graph.impact("root", {"y"}).direct
    assert graph.impact("root").unknown == ("missing",)


@given(st.lists(st.integers(min_value=0, max_value=20), unique=True))
def test_diff_identity_and_order_invariance(numbers):
    from partitionguard.models import Contract

    cols = tuple(Column(name=f"c{n}", data_type="INTEGER") for n in numbers) or (
        Column(name="x", data_type="INTEGER"),
    )
    c = Contract(product="p", owner="o", version="1", table="t", columns=cols)
    assert diff(c, c) == ()
    assert diff(c, c.model_copy(update={"columns": tuple(reversed(cols))})) == ()


@given(st.integers(min_value=1, max_value=120))
def test_partitions_exact_half_open_and_no_duplicates(days):
    start = date(2024, 2, 1)
    b = Bounds(column="day", start=start, end=start + timedelta(days=days))
    assert len(set(b.partitions())) == days
    assert b.partitions()[0] == start.isoformat()
    assert b.partitions()[-1] == (b.end - timedelta(days=1)).isoformat()


@given(st.lists(st.tuples(st.integers(0, 10), st.integers(0, 10)), max_size=50))
def test_reachability_matches_fixed_point(edges):
    nodes = tuple(
        Dependency(name=str(i), upstream=tuple(str(a) for a, b in edges if b == i))
        for i in range(11)
    )
    result = DependencyGraph(nodes).impact("0")
    reachable = {0}
    while True:
        expanded = reachable | {b for a, b in edges if a in reachable}
        if expanded == reachable:
            break
        reachable = expanded
    assert set(result.direct + result.transitive) == {str(i) for i in reachable - {0}}
