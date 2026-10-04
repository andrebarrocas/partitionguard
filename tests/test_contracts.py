import pytest
import yaml

from partitionguard.contracts import load_contract, read_yaml


def document():
    return {
        "apiVersion": "v3.2.0",
        "kind": "DataContract",
        "id": "test",
        "name": "events",
        "version": "1",
        "team": {"name": "Data"},
        "schema": [
            {
                "name": "events",
                "properties": [
                    {"name": "id", "logicalType": "integer", "primaryKey": True, "required": True},
                    {"name": "day", "logicalType": "date", "partitioned": True},
                    {"name": "status", "logicalType": "string", "enum": [{"value": "active"}]},
                ],
            }
        ],
    }


def write(tmp_path, d):
    path = tmp_path / "contract.yaml"
    path.write_text(yaml.safe_dump(d))
    return path


def test_odcs_real_shapes(tmp_path):
    c = load_contract(write(tmp_path, document()))
    assert c.primary_key == ("id",)
    assert c.partition_by == ("day",)
    assert c.column_map["status"].enum == ("active",)
    assert c.owner == "Data"
    assert not c.column_map["id"].nullable
    assert len(c.metadata["source_sha256"]) == 64


def test_physical_names(tmp_path):
    d = document()
    d["schema"][0]["physicalName"] = "warehouse.events"
    d["schema"][0]["properties"][0]["physicalName"] = "event_id"
    c = load_contract(write(tmp_path, d))
    assert c.table == "warehouse.events" and c.primary_key == ("event_id",)


@pytest.mark.parametrize("version", ["v2.0.0", "v3.99.0", "latest"])
def test_unknown_standard_version(tmp_path, version):
    d = document()
    d["apiVersion"] = version
    with pytest.raises(ValueError):
        load_contract(write(tmp_path, d))


def test_multischema_requires_selection(tmp_path):
    d = document()
    d["schema"].append({"name": "another", "properties": [{"name": "x", "logicalType": "date"}]})
    p = write(tmp_path, d)
    with pytest.raises(ValueError, match="exactly one"):
        load_contract(p)
    assert load_contract(p, "another").table == "another"


@pytest.mark.parametrize(
    "key,value",
    [
        ("logicalTypeOptions", {"minimum": 1}),
        ("quality", [{"type": "text"}]),
        ("transformLogic", "random()"),
    ],
)
def test_unsupported_semantics_visible(tmp_path, key, value):
    d = document()
    d["schema"][0]["properties"][0][key] = value
    assert load_contract(write(tmp_path, d)).unsupported


def test_invalid_enum_and_boolean(tmp_path):
    d = document()
    d["schema"][0]["properties"][2]["enum"] = ["active"]
    with pytest.raises(ValueError, match="enum"):
        load_contract(write(tmp_path, d))
    d = document()
    d["schema"][0]["properties"][0]["required"] = "false"
    with pytest.raises(ValueError, match="boolean"):
        load_contract(write(tmp_path, d))


def test_yaml_duplicate_keys_rejected(tmp_path):
    p = tmp_path / "x.yaml"
    p.write_text("version: 1\nversion: 2\n")
    with pytest.raises(ValueError, match="Duplicate"):
        read_yaml(p)


def test_nested_unsupported_type(tmp_path):
    d = document()
    d["schema"][0]["properties"][0]["logicalType"] = "array"
    with pytest.raises(ValueError, match="Unsupported physical"):
        load_contract(write(tmp_path, d))


def test_no_source_mutation(tmp_path):
    p = write(tmp_path, document())
    before = p.read_bytes()
    load_contract(p)
    assert p.read_bytes() == before


def test_missing_id_rejected(tmp_path):
    d = document()
    del d["id"]
    with pytest.raises(ValueError):
        load_contract(write(tmp_path, d))
