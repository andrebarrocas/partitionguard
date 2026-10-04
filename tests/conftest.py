from datetime import date

import pytest

from partitionguard.models import Bounds, Column, Contract, MigrationSpec, Operation


@pytest.fixture
def contracts():
    current = Contract(
        product="events",
        owner="data",
        version="1",
        table="events",
        columns=(
            Column(name="id", data_type="BIGINT", nullable=False),
            Column(name="day", data_type="DATE", nullable=False),
            Column(name="code", data_type="STRING"),
        ),
        primary_key=("id",),
        partition_by=("day",),
    )
    proposed = current.model_copy(
        update={
            "version": "2",
            "columns": current.columns + (Column(name="code_v2", data_type="INTEGER"),),
        }
    )
    return current, proposed


@pytest.fixture
def migration():
    return MigrationSpec(
        bounds=Bounds(column="day", start=date(2026, 1, 1), end=date(2026, 1, 4)),
        operations=(Operation(source="code", target="code_v2", data_type="INTEGER"),),
    )
