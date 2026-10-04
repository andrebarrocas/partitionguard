"""Reproducible fixtures; never consulted by the planner."""

from copy import deepcopy
from pathlib import Path

import yaml


def initialize(directory: Path):
    directory = Path(directory)
    names = (
        "current.yaml",
        "proposed.yaml",
        "migration.yaml",
        "breaking.yaml",
        "rename.yaml",
        "rename-migration.yaml",
    )
    if any((directory / n).exists() for n in names):
        raise ValueError("Example files already exist; choose another directory")
    directory.mkdir(parents=True, exist_ok=True)
    current = {
        "apiVersion": "v3.2.0",
        "kind": "DataContract",
        "id": "shipment-events",
        "name": "shipment_events",
        "version": "17",
        "status": "active",
        "team": {"name": "Logistics data platform"},
        "schema": [
            {
                "name": "shipment_events",
                "physicalType": "table",
                "properties": [
                    {
                        "name": "event_id",
                        "logicalType": "integer",
                        "physicalType": "BIGINT",
                        "primaryKey": True,
                        "required": True,
                    },
                    {
                        "name": "event_date",
                        "logicalType": "date",
                        "partitioned": True,
                        "required": True,
                    },
                    {"name": "carrier_code", "logicalType": "string"},
                    {
                        "name": "amount",
                        "logicalType": "number",
                        "physicalType": "DOUBLE",
                        "required": True,
                    },
                ],
            }
        ],
        "customProperties": [
            {
                "property": "partitionguard",
                "value": {
                    "dependencies": [
                        {
                            "name": "shipment_summary",
                            "kind": "sql_model",
                            "upstream": ["shipment_events"],
                        },
                        {
                            "name": "carrier_performance",
                            "kind": "sql_model",
                            "upstream": ["shipment_events"],
                        },
                        {
                            "name": "weekly_logistics_report",
                            "kind": "sql_model",
                            "upstream": ["shipment_summary"],
                        },
                        {
                            "name": "service_level_metrics",
                            "kind": "product",
                            "upstream": ["carrier_performance"],
                            "sla": True,
                        },
                    ]
                },
            }
        ],
    }
    proposed = deepcopy(current)
    proposed["version"] = "18-expand"
    proposed["schema"][0]["properties"].append(
        {"name": "carrier_code_v2", "logicalType": "integer", "required": False}
    )
    breaking = deepcopy(current)
    breaking["version"] = "18"
    breaking["schema"][0]["properties"][2]["logicalType"] = "integer"
    migration = {
        "bounds": {
            "column": "event_date",
            "start": "2026-01-01",
            "end": "2026-01-04",
            "max_partitions": 3,
            "max_rows_per_partition": 100,
            "max_total_rows": 300,
        },
        "operations": [
            {
                "source": "carrier_code",
                "target": "carrier_code_v2",
                "data_type": "INTEGER",
                "transform": "cast",
            }
        ],
        "checks": [
            {"kind": "not_null", "column": "carrier_code_v2"},
            {"kind": "sql", "sql": "SELECT COUNT(*) = 6 FROM {scope}"},
        ],
    }
    renamed = deepcopy(current)
    renamed["version"] = "18-rename-expand"
    renamed["schema"][0]["properties"].append({"name": "carrier_id", "logicalType": "string"})
    rename_migration = deepcopy(migration)
    rename_migration["operations"] = [
        {
            "source": "carrier_code",
            "target": "carrier_id",
            "data_type": "STRING",
            "transform": "copy",
        }
    ]
    rename_migration["checks"] = [{"kind": "not_null", "column": "carrier_id"}]
    for name, value in zip(
        names, (current, proposed, migration, breaking, renamed, rename_migration)
    ):
        (directory / name).write_text(yaml.safe_dump(value, sort_keys=False))
