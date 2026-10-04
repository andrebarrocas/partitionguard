"""Adversarial boundary tests: untrusted files and unvalidated Python objects."""

from datetime import date

import pytest
import yaml
from hypothesis import given
from hypothesis import strategies as st

from partitionguard.contracts import load_contract, read_yaml
from partitionguard.models import (
    Bounds,
    Check,
    Column,
    Contract,
    Dependency,
    Impact,
    MigrationSpec,
    Operation,
)
from partitionguard.planning import plan, validate_plan


def inputs():
    current = Contract(
        product="orders",
        version="1",
        owner="platform",
        table="orders",
        columns=(
            Column(name="id", data_type="INTEGER"),
            Column(name="day", data_type="DATE"),
            Column(name="value", data_type="STRING"),
        ),
        primary_key=("id",),
        partition_by=("day",),
    )
    proposed = current.model_copy(
        update={
            "version": "2",
            "columns": current.columns + (Column(name="value_v2", data_type="INTEGER"),),
        }
    )
    migration = MigrationSpec(
        bounds=Bounds(column="day", start=date(2026, 1, 1), end=date(2026, 1, 2)),
        operations=(Operation(source="value", target="value_v2", data_type="INTEGER"),),
    )
    return current, proposed, migration


@pytest.mark.parametrize(
    "patch",
    [
        {"max_rows_per_partition": -1},
        {"max_total_rows": 0},
        {"max_partitions": 0},
        {"end": date(2025, 1, 1)},
        {"column": "day; DROP TABLE orders"},
    ],
)
def test_unvalidated_bounds_cannot_reach_planning(patch):
    current, proposed, migration = inputs()
    forged = migration.model_copy(update={"bounds": migration.bounds.model_copy(update=patch)})
    with pytest.raises(ValueError):
        plan(current, proposed, migration=forged)


def test_unvalidated_source_keys_cannot_reach_planning():
    current, proposed, migration = inputs()
    current = current.model_copy(update={"primary_key": ("absent_column",)})
    with pytest.raises(ValueError, match="existing column"):
        plan(current, proposed, migration=migration)


@pytest.mark.parametrize(
    "patch",
    [
        {"transform": "random"},
        {"target": "value"},
        {"data_type": "INTEGER; DROP TABLE orders"},
    ],
)
def test_unvalidated_operation_cannot_reach_planning(patch):
    current, proposed, migration = inputs()
    forged = migration.model_copy(
        update={"operations": (migration.operations[0].model_copy(update=patch),)}
    )
    with pytest.raises(ValueError):
        plan(current, proposed, migration=forged)


def test_unvalidated_check_threshold_cannot_reach_planning():
    current, proposed, migration = inputs()
    check = Check(kind="null_rate", column="value_v2").model_copy(update={"threshold": 2.0})
    with pytest.raises(ValueError, match="fraction"):
        plan(current, proposed, migration=migration.model_copy(update={"checks": (check,)}))


def test_validate_plan_rejects_nested_model_copy_bypass():
    current, proposed, migration = inputs()
    original = plan(current, proposed, migration=migration)
    bounds = migration.bounds.model_copy(update={"max_rows_per_partition": -1})
    forged = original.model_copy(
        update={"migration": migration.model_copy(update={"bounds": bounds})}
    )
    with pytest.raises(ValueError):
        validate_plan(forged)


def odcs():
    return {
        "apiVersion": "v3.0.2",
        "kind": "DataContract",
        "id": "orders-contract",
        "name": "orders",
        "version": "1",
        "schema": [
            {
                "name": "orders",
                "properties": [
                    {"name": "id", "logicalType": "integer", "primaryKey": True},
                    {"name": "day", "logicalType": "date", "partitioned": True},
                    {"name": "value", "logicalType": "string"},
                ],
            }
        ],
    }


def contract_file(tmp_path, content):
    path = tmp_path / "contract.yaml"
    path.write_text(yaml.safe_dump(content))
    return path


@pytest.mark.parametrize(
    "field,value",
    [
        ("retention", {"days": 1}),
        ("additionalProperties", False),
        ("transformSourceObjects", ["other_table"]),
        ("transformLogic", "DELETE FROM orders"),
    ],
)
def test_object_semantics_never_disappear_silently(tmp_path, field, value):
    data = odcs()
    data["schema"][0][field] = value
    contract = load_contract(contract_file(tmp_path, data))
    assert any(field in unsupported for unsupported in contract.unsupported)
    analysis = plan(contract, contract)
    assert analysis.blockers
    assert analysis.risk.level == "BLOCKING"


def test_property_transform_lineage_is_not_claimed_supported(tmp_path):
    data = odcs()
    data["schema"][0]["properties"][2]["transformSourceObjects"] = ["undeclared_source"]
    contract = load_contract(contract_file(tmp_path, data))
    assert any("transformSourceObjects" in unsupported for unsupported in contract.unsupported)


@pytest.mark.parametrize("position", [True, 0, -1, "1", 1.5])
def test_key_positions_require_positive_integers(tmp_path, position):
    data = odcs()
    data["schema"][0]["properties"][0]["primaryKeyPosition"] = position
    with pytest.raises(ValueError, match="positive integer"):
        load_contract(contract_file(tmp_path, data))


@pytest.mark.parametrize("schema", [[42], [{"name": "orders", "properties": [42]}]])
def test_malformed_object_shapes_are_input_errors(tmp_path, schema):
    data = odcs()
    data["schema"] = schema
    with pytest.raises(ValueError, match="must be an object"):
        load_contract(contract_file(tmp_path, data))


def test_recursive_yaml_is_rejected(tmp_path):
    path = tmp_path / "recursive.yaml"
    path.write_text("value: &recursive\n  nested: *recursive\n")
    with pytest.raises((ValueError, yaml.YAMLError)):
        read_yaml(path)


def test_yaml_depth_limit(tmp_path):
    nested = {"value": 1}
    for _ in range(66):
        nested = {"nested": nested}
    path = contract_file(tmp_path, nested)
    with pytest.raises(ValueError, match="structure limits"):
        read_yaml(path)


def test_yaml_mapping_keys_cannot_be_coerced_into_contract_fields(tmp_path):
    path = tmp_path / "keys.yaml"
    path.write_text("true: yes\n")
    with pytest.raises(ValueError, match="keys must be strings"):
        read_yaml(path)


@st.composite
def declared_graphs(draw):
    count = draw(st.integers(min_value=0, max_value=12))
    names = [f"node_{index}" for index in range(count)]
    nodes = []
    for name in names:
        upstream = draw(
            st.sets(st.sampled_from(["orders", "unknown_external", *names]), max_size=4)
        )
        nodes.append(
            Dependency(
                name=name,
                upstream=tuple(sorted(upstream)),
                kind=draw(st.sampled_from(["product", "sql_model", "quality"])),
                sla=draw(st.booleans()),
            )
        )
    return tuple(nodes)


@given(declared_graphs())
def test_unchanged_contract_has_no_blast_radius_for_any_declared_graph(nodes):
    current, _, _ = inputs()
    current = current.model_copy(update={"dependencies": nodes})
    unchanged = plan(current, current)
    assert unchanged.impact == Impact()
    assert unchanged.risk.level == "LOW"


def test_owner_metadata_changes_do_not_activate_downstream_sla_risk():
    current, _, _ = inputs()
    current = current.model_copy(
        update={
            "dependencies": (
                Dependency(name="report", upstream=("orders",), kind="sql_model", sla=True),
            )
        }
    )
    proposed = current.model_copy(update={"owner": "new-owner", "version": "2"})
    changed = plan(current, proposed)
    assert changed.impact == Impact()
    assert changed.risk.level == "LOW"


def test_dependency_edit_is_a_warning_without_claiming_data_blast_radius():
    current, _, _ = inputs()
    proposed = current.model_copy(
        update={
            "version": "2",
            "dependencies": (
                Dependency(name="report", upstream=("orders",), kind="sql_model", sla=True),
            ),
        }
    )
    changed = plan(current, proposed)
    assert changed.impact == Impact()
    assert changed.risk.level == "MEDIUM"
    assert [change.kind for change in changed.changes] == ["dependencies"]


def test_proposed_lineage_cannot_erase_existing_consumers_during_data_change():
    current, proposed, migration = inputs()
    current = current.model_copy(
        update={
            "dependencies": (
                Dependency(name="report", upstream=("orders",), kind="sql_model", sla=True),
            )
        }
    )
    proposed = proposed.model_copy(
        update={
            "dependencies": (
                Dependency(name="report", upstream=(), kind="quality", sla=False),
                Dependency(name="report_consumer", upstream=("report",)),
            )
        }
    )
    changed = plan(current, proposed, migration=migration)
    assert changed.impact.direct == ("report",)
    assert changed.impact.transitive == ("report_consumer",)
    assert changed.impact.transformations == ("report",)
    assert changed.impact.quality_checks == ("report",)
    assert changed.impact.slas == ("report",)


def test_duplicate_dependency_declarations_are_not_silently_overwritten():
    current, proposed, migration = inputs()
    forged = current.model_copy(
        update={
            "dependencies": (
                Dependency(name="report", upstream=("orders",)),
                Dependency(name="report", upstream=()),
            )
        }
    )
    with pytest.raises(ValueError, match="Duplicate dependency"):
        plan(forged, proposed, migration=migration)


@pytest.mark.parametrize(
    "target_type", ["public.dangerous_domain", "dangerous_type", "TEXT[]", "VARCHAR(4)"]
)
def test_custom_assertion_cannot_invoke_user_defined_casts(target_type):
    from partitionguard.verification import _custom_sql

    current, proposed, migration = inputs()
    value = plan(current, proposed, migration=migration)

    class NeverQuery:
        dialect = "postgres"
        placeholder = "%s"

        def fetch(self, *args, **kwargs):
            pytest.fail("An unsafe type cast must be rejected before database evaluation")

    with pytest.raises(ValueError):
        _custom_sql(
            NeverQuery(), value, f"SELECT COUNT(CAST(value AS {target_type})) > 0 FROM {{scope}}"
        )
