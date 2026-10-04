import json
import os
import uuid
from datetime import date

import pytest
from hypothesis import given
from hypothesis import strategies as st

from partitionguard.adapters import open_adapter, quote
from partitionguard.models import (
    Bounds,
    Check,
    Column,
    Contract,
    MigrationSpec,
    Operation,
    Plan,
    Risk,
)
from partitionguard.verification import (
    _fingerprint,
    capture_baseline,
    verify,
    verify_conversion,
    verify_operation,
)


@pytest.fixture(params=["duckdb", "postgres"])
def example(request, tmp_path):
    name = request.param
    connection = str(tmp_path / "verify.duckdb")
    if name == "postgres":
        connection = os.environ.get("PARTITIONGUARD_TEST_POSTGRES")
        if not connection:
            pytest.skip("Set PARTITIONGUARD_TEST_POSTGRES for a real PostgreSQL integration test")
    table = "pg_verify_" + uuid.uuid4().hex
    adapter = open_adapter(name, connection, read_only=False)
    adapter.execute(f"CREATE TABLE {quote(table)} (id INTEGER, day DATE, code TEXT, amount BIGINT)")
    for row in [
        (1, date(2026, 1, 1), "10", 10),
        (2, date(2026, 1, 2), "20", 20),
        (3, date(2026, 1, 3), "outside", 30),
    ]:
        p = adapter.placeholder
        adapter.execute(f"INSERT INTO {quote(table)} VALUES ({p}, {p}, {p}, {p})", row)
    current = Contract(
        product="events",
        version="1",
        owner="test",
        table=table,
        columns=(
            Column(name="id", data_type="INTEGER", nullable=False),
            Column(name="day", data_type="DATE"),
            Column(name="code", data_type="STRING"),
            Column(name="amount", data_type="BIGINT"),
        ),
        primary_key=("id",),
    )
    proposed = current.model_copy(
        update={
            "version": "2",
            "columns": current.columns + (Column(name="code_v2", data_type="INTEGER"),),
        }
    )
    operation = Operation(source="code", target="code_v2", data_type="INTEGER")
    migration = MigrationSpec(
        bounds=Bounds(column="day", start="2026-01-01", end="2026-01-03"), operations=(operation,)
    )
    plan = Plan(
        adapter=name,
        current=current,
        proposed=proposed,
        migration=migration,
        risk=Risk(level="HIGH"),
    )
    try:
        yield adapter, plan
    finally:
        adapter.execute(f"DROP TABLE IF EXISTS {quote(table)}")
        adapter.close()


def populate(adapter, plan):
    operation = plan.migration.operations[0]
    adapter.add_column(plan.current.table, operation)
    for partition in plan.migration.bounds.partitions():
        with adapter.transaction():
            adapter.backfill(plan.current.table, operation, plan.migration.bounds, partition)


def test_baseline_and_parity_ignore_out_of_scope_bad_values(example):
    adapter, plan = example
    baseline = capture_baseline(adapter, plan)
    assert baseline["total_rows"] == 2
    assert "outside" not in json.dumps(baseline)
    assert verify_conversion(adapter, plan)["passed"]
    populate(adapter, plan)
    assert capture_baseline(adapter, plan) == baseline
    assert verify(adapter, plan, baseline)["passed"]
    assert adapter.fetch(f"SELECT code_v2 FROM {quote(plan.current.table)} WHERE id = 3") == [
        (None,)
    ]


def test_target_corruption_and_source_drift_are_separate_failures(example):
    adapter, plan = example
    baseline = capture_baseline(adapter, plan)
    populate(adapter, plan)
    adapter.execute(f"UPDATE {quote(plan.current.table)} SET code_v2 = 99 WHERE id = 1")
    result = verify(adapter, plan, baseline)
    assert not result["passed"]
    assert any(
        not check["passed"] and check["name"].startswith("parity:") for check in result["checks"]
    )
    adapter.execute(f"UPDATE {quote(plan.current.table)} SET code = '11' WHERE id = 1")
    result = verify(adapter, plan, baseline)
    assert any(
        not check["passed"] and check["name"] == "source_fingerprint" for check in result["checks"]
    )


def test_reconcile_one_partition_does_not_require_later_work(example):
    adapter, plan = example
    operation = plan.migration.operations[0]
    adapter.add_column(plan.current.table, operation)
    adapter.backfill(plan.current.table, operation, plan.migration.bounds, "2026-01-01")
    assert verify_operation(adapter, plan, operation, "2026-01-01")["passed"]
    assert not verify_operation(adapter, plan, operation, "2026-01-02")["passed"]


def test_key_uniqueness_is_global_across_partitions(example):
    adapter, plan = example
    adapter.execute(f"UPDATE {quote(plan.current.table)} SET id = 1 WHERE id = 2")
    baseline = capture_baseline(adapter, plan)
    populate(adapter, plan)
    result = verify(adapter, plan, baseline)
    assert not result["passed"]
    assert any(
        check["name"] == "target:primary_key" and not check["passed"] for check in result["checks"]
    )


@pytest.mark.parametrize("bad", ["1.5", " 1", "1e2", "2147483648", "secret-invalid-value"])
def test_conversion_preflight_fails_without_mutation_or_value_leak(example, bad):
    adapter, plan = example
    adapter.execute(
        f"UPDATE {quote(plan.current.table)} SET code = {adapter.placeholder} WHERE id = 1", (bad,)
    )
    result = verify_conversion(adapter, plan)
    assert not result["passed"]
    assert "code_v2" not in adapter.schema(plan.current.table)
    assert json.dumps(bad) not in json.dumps(result)


def test_total_cap_and_missing_partition_are_rejected(example):
    adapter, plan = example
    small = plan.migration.bounds.model_copy(update={"max_total_rows": 1})
    capped = plan.model_copy(
        update={"migration": plan.migration.model_copy(update={"bounds": small})}
    )
    with pytest.raises(ValueError, match="total row cap"):
        capture_baseline(adapter, capped)
    adapter.execute(f"DELETE FROM {quote(plan.current.table)} WHERE id = 2")
    with pytest.raises(ValueError, match="empty"):
        capture_baseline(adapter, plan)


@pytest.mark.parametrize(
    "assertion, expected",
    [
        ("SELECT COUNT(*) = 2 FROM {scope}", True),
        ("SELECT SUM(code_v2) = 30 FROM {scope}", True),
        ("SELECT COUNT(*) = 3 FROM {scope}", False),
        ("SELECT 1 FROM {scope}", False),
        ("SELECT TRUE FROM {scope}", False),
        ("SELECT MAX('pg_scope') = 'pg_scope' FROM {scope}", True),
        ("SELECT COUNT(*) > 0 FROM {scope}; SELECT 1", False),
        ("SELECT COUNT(*) > 0 FROM {scope} JOIN private_data ON TRUE", False),
        ("SELECT malicious_function() FROM {scope}", False),
        ("SELECT COUNT(*) > 0 FROM {scope} WHERE random() > 0.5", False),
        ("SELECT COUNT(*) > 0 FROM {scope} FOR UPDATE", False),
    ],
)
def test_custom_sql_is_scalar_scoped_and_restricted(example, assertion, expected):
    adapter, plan = example
    plan = plan.model_copy(
        update={
            "migration": plan.migration.model_copy(
                update={"checks": (Check(kind="sql", sql=assertion),)}
            )
        }
    )
    baseline = capture_baseline(adapter, plan)
    populate(adapter, plan)
    result = verify(adapter, plan, baseline)
    assert result["passed"] is expected


def test_policy_checks_and_thresholds(example):
    adapter, plan = example
    checks = (
        Check(kind="aggregate", column="code_v2", reference="amount"),
        Check(kind="null_rate", column="code_v2", reference="code", threshold=0),
        Check(kind="range", column="code_v2", values=(10, 20)),
        Check(kind="enum", column="code_v2", values=(10, 20)),
        Check(kind="unique", column="code_v2"),
    )
    plan = plan.model_copy(
        update={"migration": plan.migration.model_copy(update={"checks": checks})}
    )
    baseline = capture_baseline(adapter, plan)
    populate(adapter, plan)
    assert verify(adapter, plan, baseline)["passed"]


def test_aggregate_does_not_round_away_large_integer_difference(example):
    adapter, plan = example
    adapter.execute(
        f"UPDATE {quote(plan.current.table)} SET amount = 9007199254740993 WHERE id = 1"
    )
    adapter.execute(f"UPDATE {quote(plan.current.table)} SET amount = 0 WHERE id = 2")
    # Directly test the public policy through a same-type replacement migration.
    operation = Operation(source="amount", target="code_v2", data_type="BIGINT", transform="copy")
    proposed = plan.proposed.model_copy(
        update={"columns": plan.current.columns + (Column(name="code_v2", data_type="BIGINT"),)}
    )
    spec = plan.migration.model_copy(
        update={
            "operations": (operation,),
            "checks": (Check(kind="aggregate", column="code_v2", reference="amount"),),
        }
    )
    plan = plan.model_copy(update={"proposed": proposed, "migration": spec})
    baseline = capture_baseline(adapter, plan)
    populate(adapter, plan)
    adapter.execute(
        f"UPDATE {quote(plan.current.table)} SET code_v2 = 9007199254740992 WHERE id = 1"
    )
    result = verify(adapter, plan, baseline)
    assert any(
        check["name"] == "policy:aggregate:code_v2" and not check["passed"]
        for check in result["checks"]
    )


@given(st.lists(st.tuples(st.integers(), st.one_of(st.none(), st.text())), max_size=50))
def test_source_fingerprint_is_a_multiset_not_order_dependent(rows):
    assert _fingerprint(rows) == _fingerprint(list(reversed(rows)))
    if rows:
        assert _fingerprint(rows) != _fingerprint(rows + rows[:1])


def test_fingerprint_preserves_types_and_ieee_values():
    assert _fingerprint([(1,)]) != _fingerprint([("1",)])
    assert _fingerprint([(True,)]) != _fingerprint([(1,)])
    assert _fingerprint([(float("nan"), float("inf"))]) == _fingerprint(
        [(float("nan"), float("inf"))]
    )


def test_baseline_binds_the_runtime_semantics(example, monkeypatch):
    adapter, plan = example
    first = capture_baseline(adapter, plan)
    assert first["runtime_signature"]["adapter"] == adapter.name
    monkeypatch.setattr("partitionguard.__version__", "999.0.0")
    second = capture_baseline(adapter, plan)
    assert first != second
    assert first["partitions"] == second["partitions"]
    populate(adapter, plan)
    assert not verify(adapter, plan, first)["passed"]


def test_postgres_assertion_cannot_resolve_a_shadowed_builtin(example):
    adapter, plan = example
    if adapter.name != "postgres":
        pytest.skip("PostgreSQL function overload resolution")
    schema = "shadow_" + uuid.uuid4().hex
    adapter.execute(f"CREATE SCHEMA {quote(schema)}")
    try:
        adapter.execute(
            f"CREATE FUNCTION {quote(schema)}.count(TEXT) RETURNS BIGINT LANGUAGE SQL AS 'SELECT 0::BIGINT'"
        )
        adapter.execute(f"SET search_path = {quote(schema)}, public")
        plan = plan.model_copy(
            update={
                "migration": plan.migration.model_copy(
                    update={
                        "checks": (Check(kind="sql", sql="SELECT COUNT(code) = 2 FROM {scope}"),)
                    }
                )
            }
        )
        baseline = capture_baseline(adapter, plan)
        populate(adapter, plan)
        assert verify(adapter, plan, baseline)["passed"]
    finally:
        adapter.execute("SET search_path = public")
        adapter.execute(f"DROP SCHEMA {quote(schema)} CASCADE")
