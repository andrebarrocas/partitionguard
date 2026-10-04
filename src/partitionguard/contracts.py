"""Read a deliberately documented ODCS relational subset without mutating sources."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml

from .models import Check, Column, Contract, Dependency, MigrationSpec


class UniqueLoader(yaml.SafeLoader):
    pass


def _mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise ValueError("YAML object keys must be strings")
        if key in result:
            raise ValueError(f"Duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def read_yaml(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    if path.stat().st_size > 2_000_000:
        raise ValueError("Input exceeds 2 MB safety limit")
    document = yaml.load(path.read_text(), Loader=UniqueLoader)
    if not isinstance(document, dict):
        raise ValueError("Expected a YAML/JSON object")
    visited = 0
    active = set()

    def check_shape(value, depth=0):
        nonlocal visited
        visited += 1
        if visited > 100_000 or depth > 64:
            raise ValueError("Input exceeds YAML structure limits")
        if isinstance(value, (dict, list)):
            if id(value) in active:
                raise ValueError("Recursive YAML aliases are unsupported")
            active.add(id(value))
            for child in value.values() if isinstance(value, dict) else value:
                check_shape(child, depth + 1)
            active.remove(id(value))

    check_shape(document)
    return document


def _extensions(data):
    custom = data.get("customProperties") or []
    if not isinstance(custom, list):
        raise ValueError("ODCS customProperties must be an array")
    matches = [
        p["value"] for p in custom if isinstance(p, dict) and p.get("property") == "partitionguard"
    ]
    if len(matches) > 1:
        raise ValueError("Duplicate partitionguard custom property")
    value = matches[0] if matches else {}
    if not isinstance(value, dict):
        raise ValueError("partitionguard custom property must be an object")
    allowed = {"dependencies", "businessKeys", "compatibility"}
    if set(value) - allowed:
        raise ValueError(f"Unknown partitionguard properties: {sorted(set(value) - allowed)}")
    return value


def load_contract(path: str | Path, object_name: str | None = None) -> Contract:
    data = read_yaml(path)
    if data.get("kind") != "DataContract" or data.get("apiVersion") not in {
        "v3.0.0",
        "v3.0.1",
        "v3.0.2",
        "v3.1.0",
        "v3.2.0",
    }:
        raise ValueError(
            "Expected ODCS DataContract with supported apiVersion v3.0.x/v3.1.0/v3.2.0"
        )
    if not isinstance(data.get("id"), str) or not data["id"]:
        raise ValueError("ODCS id is required")
    if not isinstance(data.get("version"), str):
        raise ValueError("ODCS version must be a quoted string")
    objects = data.get("schema")
    if not isinstance(objects, list) or not objects:
        raise ValueError("ODCS schema must contain relational objects")
    if any(not isinstance(obj, dict) for obj in objects):
        raise ValueError("Every ODCS schema object must be an object")
    if object_name:
        objects = [o for o in objects if o.get("name") == object_name]
    if len(objects) != 1:
        raise ValueError("Select exactly one ODCS object with --object-name")
    obj = objects[0]
    if obj.get("logicalType", "object") != "object" or obj.get("physicalType", "table") != "table":
        raise ValueError("Only relational table objects are supported")
    extension = _extensions(data)
    unsupported = []
    descriptive = {
        "apiVersion",
        "kind",
        "id",
        "name",
        "version",
        "schema",
        "status",
        "domain",
        "dataProduct",
        "tenant",
        "description",
        "tags",
        "customProperties",
        "team",
        "teamId",
        "support",
        "roles",
        "pricing",
        "servers",
        "authoritativeDefinitions",
        "slaProperties",
        "servicelevels",
        "quality",
        "references",
    }
    unsupported.extend(f"Unrecognized contract field: {k}" for k in sorted(set(data) - descriptive))
    object_keys = {
        "id",
        "name",
        "physicalName",
        "physicalType",
        "logicalType",
        "description",
        "businessName",
        "properties",
        "quality",
        "relationships",
        "references",
        "authoritativeDefinitions",
        "tags",
        "customProperties",
        "dataGranularityDescription",
        "status",
        "deprecated",
        "synonyms",
        "classification",
        "criticalDataElement",
        "examples",
        "transformLogic",
        "transformDescription",
        "transformSourceObjects",
    }
    unsupported.extend(
        f"object.{key}: unrecognized semantics" for key in sorted(set(obj) - object_keys)
    )
    for level, content in (("contract", data), ("object", obj)):
        for semantic in (
            "quality",
            "relationships",
            "references",
            "transformLogic",
            "transformSourceObjects",
        ):
            if content.get(semantic):
                unsupported.append(f"{level}.{semantic}: required semantics need a contract plugin")
    columns, primary, partitions, checks = [], [], [], []
    props = obj.get("properties")
    if not isinstance(props, list) or not props:
        raise ValueError("Relational object requires properties")
    if any(not isinstance(prop, dict) for prop in props):
        raise ValueError("Every ODCS property must be an object")
    passive_property_keys = {
        "id",
        "name",
        "physicalName",
        "physicalType",
        "logicalType",
        "required",
        "description",
        "enum",
        "unique",
        "primaryKey",
        "primaryKeyPosition",
        "partitioned",
        "partitionKeyPosition",
        "businessName",
        "classification",
        "criticalDataElement",
        "tags",
        "examples",
        "deprecated",
        "synonyms",
        "authoritativeDefinitions",
        "customProperties",
        "quality",
        "logicalTypeOptions",
        "items",
        "map",
        "properties",
        "transformLogic",
        "transformDescription",
        "transformSourceObjects",
        "semanticType",
        "encryptedName",
    }
    for prop in props:
        name = prop.get("physicalName") or prop.get("name")
        if not name:
            raise ValueError("Every property needs a name")
        unsupported.extend(
            f"{name}.{k}: unrecognized semantics" for k in sorted(set(prop) - passive_property_keys)
        )
        for semantic in (
            "quality",
            "logicalTypeOptions",
            "items",
            "map",
            "properties",
            "transformLogic",
            "transformSourceObjects",
        ):
            if prop.get(semantic):
                unsupported.append(f"{name}.{semantic}: unsupported required semantics")
        if prop.get("semanticType", "column") != "column":
            unsupported.append(f"{name}.semanticType: only physical columns supported")
        for boolean in ("required", "primaryKey", "partitioned", "unique"):
            if boolean in prop and not isinstance(prop[boolean], bool):
                raise ValueError(f"{name}.{boolean} must be a boolean")
        for position in ("primaryKeyPosition", "partitionKeyPosition"):
            if position in prop and (type(prop[position]) is not int or prop[position] < 1):
                raise ValueError(f"{name}.{position} must be a positive integer")
        enum = prop.get("enum") or []
        if not isinstance(enum, list) or any(
            not isinstance(x, dict) or "value" not in x for x in enum
        ):
            raise ValueError("ODCS enum entries must be objects containing value")
        columns.append(
            Column(
                name=name,
                data_type=prop.get("physicalType") or prop.get("logicalType", ""),
                nullable=not prop.get("required", False),
                description=prop.get("description") or "",
                enum=tuple(x["value"] for x in enum),
            )
        )
        if prop.get("primaryKey"):
            primary.append((prop.get("primaryKeyPosition", len(primary) + 1), name))
        if prop.get("partitioned"):
            partitions.append((prop.get("partitionKeyPosition", len(partitions) + 1), name))
        if prop.get("unique"):
            checks.append(Check(kind="unique", column=name))
    sla = data.get("slaProperties") or data.get("servicelevels") or {}
    if sla:
        unsupported.append(
            "SLA execution semantics require an explicit supported freshness check/plugin"
        )
    team = data.get("team") or {}
    owner = team.get("name", "unspecified") if isinstance(team, dict) else str(team)
    return Contract(
        product=data.get("name") or data["id"],
        version=data["version"],
        owner=owner,
        table=obj.get("physicalName") or obj["name"],
        columns=tuple(columns),
        primary_key=tuple(n for _, n in sorted(primary)),
        partition_by=tuple(n for _, n in sorted(partitions)),
        business_keys=tuple(extension.get("businessKeys", [])),
        dependencies=tuple(Dependency.model_validate(v) for v in extension.get("dependencies", [])),
        quality=tuple(checks),
        sla=sla if isinstance(sla, dict) else {"properties": sla},
        retention={},
        compatibility=extension.get("compatibility", "backward"),
        metadata={
            "odcs_id": data["id"],
            "api_version": data["apiVersion"],
            "object_name": obj["name"],
            "description": data.get("description"),
            "source_sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
        },
        unsupported=tuple(sorted(unsupported)),
    )


def load_migration(path: str | Path) -> MigrationSpec:
    return MigrationSpec.model_validate(read_yaml(path))
