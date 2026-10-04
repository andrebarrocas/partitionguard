"""Integration tests use real local Delta commits and Parquet files."""

from datetime import date, datetime

import pytest

from partitionguard.adapters.delta import DeltaAdapter
from partitionguard.models import Bounds, Operation

pa = pytest.importorskip("pyarrow")
delta = pytest.importorskip("deltalake")


@pytest.fixture
def table_path(tmp_path):
    path = tmp_path / "delta-events"
    delta.write_deltalake(
        path,
        pa.table(
            {
                "event_date": pa.array(
                    [date(2025, 1, 1), date(2025, 1, 2), date(2025, 1, 3)], pa.date32()
                ),
                "event_id": pa.array([1, 2, 3], pa.int64()),
                "carrier_code": ["10", "20", "30"],
            }
        ),
        partition_by=["event_date"],
    )
    return path


@pytest.fixture
def bounds():
    return Bounds(
        column="event_date",
        start=date(2025, 1, 1),
        end=date(2025, 1, 3),
        max_rows_per_partition=5,
        max_total_rows=10,
    )


@pytest.fixture
def operation():
    return Operation(source="carrier_code", target="carrier_code_v2", data_type="INTEGER")


def add(adapter, operation):
    with adapter.transaction():
        adapter.add_column("events", operation)


def fill(adapter, operation, bounds, day="2025-01-01"):
    with adapter.transaction():
        adapter.backfill("events", operation, bounds, day)


def test_bounded_rewrite_and_historical_snapshot(table_path, bounds, operation):
    before = delta.DeltaTable(table_path)
    out_of_scope_files = before.file_uris(
        file_pruning_predicate=[("event_date", "=", "2025-01-03")]
    )
    with DeltaAdapter(str(table_path), read_only=False) as adapter:
        adapter.configure_scope("events", bounds)
        assert adapter.fetch("SELECT count(*) FROM events") == [(2,)]
        with adapter.lock("events"):
            add(adapter, operation)
            fill(adapter, operation, bounds)
            first_commit = adapter.version
            fill(adapter, operation, bounds)
            assert adapter.version == first_commit, (
                "Idempotent replay should not rewrite matching values"
            )
            fill(adapter, operation, bounds, "2025-01-02")
        assert adapter.fetch("SELECT carrier_code_v2 FROM events ORDER BY event_id") == [
            (10,),
            (20,),
        ]
        assert adapter.schema("events")["carrier_code"] == "STRING"
        assert adapter.history()[0]["operation"] == "UPDATE"
        assert adapter.evidence()["atomicity"] == "action"
        with adapter.snapshot(0) as historical:
            assert historical.version == 0
            assert "carrier_code_v2" not in historical.schema("events")
            assert historical.fetch("SELECT sum(event_id) FROM events") == [(3,)]
            with pytest.raises(PermissionError):
                historical.add_column("events", operation)
    after = delta.DeltaTable(table_path)
    assert (
        after.file_uris(file_pruning_predicate=[("event_date", "=", "2025-01-03")])
        == out_of_scope_files
    )
    rows = after.to_pyarrow_table().to_pylist()
    assert next(r for r in rows if r["event_id"] == 3)["carrier_code_v2"] is None


def test_read_only_and_scope_are_mandatory(table_path, bounds, operation):
    with DeltaAdapter(str(table_path)) as adapter:
        with pytest.raises(ValueError, match="bounded"):
            adapter.fetch("SELECT * FROM events")
        adapter.configure_scope("events", bounds)
        with pytest.raises(PermissionError):
            adapter.add_column("events", operation)
        with pytest.raises(PermissionError):
            adapter.execute("DELETE FROM events")
        with pytest.raises(ValueError, match="single read-only"):
            adapter.fetch("SELECT 1; SELECT 2")
        with pytest.raises(ValueError, match="single read-only"):
            adapter.fetch("DELETE FROM events RETURNING *")
        with pytest.raises(Exception, match="disabled|not permitted"):
            adapter.fetch("SELECT * FROM read_csv('/etc/passwd')")
        with pytest.raises(ValueError, match="only its configured"):
            adapter.schema("different_table")
    assert delta.DeltaTable(table_path).version() == 0
    assert not (table_path / ".partitionguard.lock").exists()


def test_refuses_nonpartitioned_date_table(tmp_path, bounds):
    path = tmp_path / "not-partitioned"
    delta.write_deltalake(path, pa.table({"event_date": pa.array([date(2025, 1, 1)], pa.date32())}))
    with DeltaAdapter(str(path)) as adapter:
        with pytest.raises(ValueError, match="physical partition"):
            adapter.configure_scope("events", bounds)


def test_refuses_string_partition_and_remote_uri(tmp_path, bounds):
    path = tmp_path / "string-partition"
    delta.write_deltalake(
        path, pa.table({"event_date": ["2025-01-01"], "id": [1]}), partition_by=["event_date"]
    )
    with DeltaAdapter(str(path)) as adapter:
        with pytest.raises(ValueError, match="physical DATE"):
            adapter.configure_scope("events", bounds)
    with pytest.raises(ValueError, match="local paths only"):
        DeltaAdapter("s3://bucket/path")


def test_rejects_outside_scope_and_row_cap(table_path, bounds, operation):
    limited = bounds.model_copy(update={"max_rows_per_partition": 1})
    delta.write_deltalake(
        table_path,
        pa.table(
            {
                "event_date": pa.array([date(2025, 1, 1)], pa.date32()),
                "event_id": pa.array([4], pa.int64()),
                "carrier_code": ["40"],
            }
        ),
        mode="append",
    )
    with DeltaAdapter(str(table_path), read_only=False) as adapter:
        adapter.configure_scope("events", limited)
        with adapter.lock("events"):
            add(adapter, operation)
            with pytest.raises(ValueError, match="outside"):
                fill(adapter, operation, limited, "2025-01-03")
            with pytest.raises(ValueError, match="row cap"):
                fill(adapter, operation, limited)
        assert adapter.fetch("SELECT count(*) FROM events WHERE carrier_code_v2 IS NOT NULL") == [
            (0,)
        ]


@pytest.mark.parametrize("bad_value", ["x", " 1", "1.5", "1e2", "١", "2147483648"])
def test_conversion_errors_do_not_commit_partition(table_path, bounds, operation, bad_value):
    delta.DeltaTable(table_path).update(
        new_values={"carrier_code": bad_value}, predicate="event_id = 1"
    )
    with DeltaAdapter(str(table_path), read_only=False) as adapter:
        adapter.configure_scope("events", bounds)
        with adapter.lock("events"):
            add(adapter, operation)
            version = adapter.version
            with pytest.raises(Exception):
                fill(adapter, operation, bounds)
            assert adapter.version == version
        assert adapter.fetch("SELECT carrier_code_v2 FROM events WHERE event_id = 1") == [(None,)]


def test_unexpected_writer_is_detected(table_path, bounds, operation):
    with DeltaAdapter(str(table_path), read_only=False) as adapter:
        adapter.configure_scope("events", bounds)
        delta.DeltaTable(table_path).update(
            new_values={"carrier_code": "99"}, predicate="event_id = 1"
        )
        with pytest.raises(RuntimeError, match="concurrently"):
            adapter.fetch("SELECT * FROM events")
        with pytest.raises(RuntimeError, match="concurrently"):
            with adapter.lock("events"):
                add(adapter, operation)


def test_cooperative_lock_excludes_another_adapter(table_path, bounds):
    with (
        DeltaAdapter(str(table_path), False) as first,
        DeltaAdapter(str(table_path), False) as second,
    ):
        first.configure_scope("events", bounds)
        second.configure_scope("events", bounds)
        with first.lock("events"):
            with pytest.raises(RuntimeError, match="holds the Delta writer lock"):
                with second.lock("events"):
                    pass


def test_one_mutation_transaction_and_commit_failure_window(table_path, bounds, operation):
    with DeltaAdapter(str(table_path), False) as adapter:
        adapter.configure_scope("events", bounds)
        with adapter.lock("events"):
            with pytest.raises(RuntimeError, match="one mutation"):
                with adapter.transaction():
                    adapter.add_column("events", operation)
                    adapter.backfill("events", operation, bounds, "2025-01-01")
            # The first action committed: the transaction context never claims
            # rollback of already committed Delta operations.
            assert "carrier_code_v2" in adapter.schema("events")
            with pytest.raises(RuntimeError, match="simulated process failure"):
                with adapter.transaction():
                    adapter.backfill("events", operation, bounds, "2025-01-01")
                    raise RuntimeError("simulated process failure before checkpoint")
            committed_version = adapter.version
    with DeltaAdapter(str(table_path), False) as recovered:
        recovered.configure_scope("events", bounds)
        with recovered.lock("events"):
            fill(recovered, operation, bounds)
        assert recovered.version == committed_version
        assert recovered.fetch("SELECT carrier_code_v2 FROM events WHERE event_id = 1") == [(10,)]


def test_stale_commit_does_not_retry_over_competing_writer(
    table_path, bounds, operation, monkeypatch
):
    with DeltaAdapter(str(table_path), False) as adapter:
        adapter.configure_scope("events", bounds)
        with adapter.lock("events"):
            add(adapter, operation)
            original_update = adapter._table.update

            def competing_update(*args, **kwargs):
                assert kwargs["commit_properties"].max_commit_retries == 0
                delta.DeltaTable(table_path).update(
                    new_values={"carrier_code": "99"}, predicate="event_id = 1"
                )
                return original_update(*args, **kwargs)

            monkeypatch.setattr(adapter._table, "update", competing_update)
            with pytest.raises(Exception, match="commit|Commit|concurrent"):
                fill(adapter, operation, bounds)
    row = next(
        r for r in delta.DeltaTable(table_path).to_pyarrow_table().to_pylist() if r["event_id"] == 1
    )
    assert row["carrier_code"] == "99"
    assert row["carrier_code_v2"] is None


def test_unsupported_cast_fails_before_column_addition(table_path, bounds):
    with DeltaAdapter(str(table_path), False) as adapter:
        adapter.configure_scope("events", bounds)
        with adapter.lock("events"):
            with pytest.raises(ValueError, match="Unsupported Delta conversion"):
                add(
                    adapter,
                    Operation(source="carrier_code", target="new_double", data_type="DOUBLE"),
                )
        assert adapter.version == 0


def migration_plan(bounds, operation, checks=()):
    from partitionguard.models import Column, Contract, MigrationSpec
    from partitionguard.planning import plan

    current = Contract(
        product="events",
        version="1",
        owner="logistics",
        table="events",
        columns=(
            Column(name="event_date", data_type="DATE", nullable=False),
            Column(name="event_id", data_type="BIGINT", nullable=False),
            Column(name="carrier_code", data_type="STRING"),
        ),
        primary_key=("event_id",),
        partition_by=("event_date",),
    )
    proposed = current.model_copy(
        update={
            "version": "2",
            "columns": current.columns + (Column(name="carrier_code_v2", data_type="INTEGER"),),
        }
    )
    result = plan(
        current,
        proposed,
        "delta",
        MigrationSpec(bounds=bounds, operations=(operation,), checks=checks),
    )
    assert not result.blockers
    return result


def test_engine_reopens_and_recovers_delta_run(tmp_path, table_path, bounds, operation):
    from partitionguard.engine import Engine, InjectedFailure
    from partitionguard.ledger import Ledger
    from partitionguard.verification import capture_baseline, verify

    plan = migration_plan(bounds, operation)
    with Ledger(tmp_path / "ledger.sqlite") as ledger:
        with DeltaAdapter(str(table_path)) as readonly:
            engine = Engine(readonly, ledger)
            prepared = engine.dry_run(plan)
            assert prepared["status"] == "PREPARED"
            assert readonly.version == 0
            assert not (table_path / ".partitionguard.lock").exists()
            baseline = prepared["baseline"]
            engine.approve(plan, plan.id, "reviewer")
        with DeltaAdapter(str(table_path), False) as writable:
            with pytest.raises(InjectedFailure):
                Engine(writable, ledger).apply(
                    plan, "operator", maintenance_window=True, fail_after=1
                )
            assert ledger.get(plan.id)["status"] == "FAILED"
            assert len(ledger.get(plan.id)["checkpoints"]) == 2
        with DeltaAdapter(str(table_path), False) as recovered:
            result = Engine(recovered, ledger).apply(
                plan, "operator", resume=True, maintenance_window=True
            )
            assert result["status"] == "COMPLETE"
            assert result["verification"]["passed"]
            assert ledger.verify_integrity(plan.id)
            assert recovered.version == 3
            with recovered.snapshot(0) as snapshot:
                assert capture_baseline(snapshot, plan) == baseline
                assert not verify(snapshot, plan, baseline)["passed"]
            with recovered.snapshot(recovered.version) as snapshot:
                assert verify(snapshot, plan, baseline)["passed"]


def test_engine_commit_checkpoint_gap_replays_without_new_delta_commit(
    tmp_path, table_path, bounds, operation, monkeypatch
):
    from partitionguard.engine import Engine
    from partitionguard.ledger import Ledger

    plan = migration_plan(bounds, operation)
    with (
        Ledger(tmp_path / "ledger.sqlite") as ledger,
        DeltaAdapter(str(table_path), False) as adapter,
    ):
        engine = Engine(adapter, ledger)
        engine.dry_run(plan)
        engine.approve(plan, plan.id, "reviewer")
        real_checkpoint = ledger.checkpoint
        crashed = False

        def crash_once(plan_id, action_id, detail):
            nonlocal crashed
            if action_id.startswith("backfill:") and not crashed:
                crashed = True
                raise KeyboardInterrupt("crash after Delta commit before checkpoint")
            return real_checkpoint(plan_id, action_id, detail)

        monkeypatch.setattr(ledger, "checkpoint", crash_once)
        with pytest.raises(KeyboardInterrupt):
            engine.apply(plan, "operator", maintenance_window=True)
        assert adapter.version == 2
        assert len(ledger.get(plan.id)["checkpoints"]) == 1
        result = engine.apply(plan, "operator", resume=True, maintenance_window=True)
        assert result["status"] == "COMPLETE"
        assert adapter.version == 3
        assert adapter.fetch("SELECT count(*) FROM events") == [(2,)]


def test_engine_failed_delta_verification_never_completes(tmp_path, table_path, bounds, operation):
    from partitionguard.engine import Engine, VerificationFailed
    from partitionguard.ledger import Ledger
    from partitionguard.models import Check

    plan = migration_plan(
        bounds, operation, checks=(Check(kind="range", column="carrier_code_v2", values=(0, 15)),)
    )
    with (
        Ledger(tmp_path / "ledger.sqlite") as ledger,
        DeltaAdapter(str(table_path), False) as adapter,
    ):
        engine = Engine(adapter, ledger)
        engine.dry_run(plan)
        engine.approve(plan, plan.id, "reviewer")
        with pytest.raises(VerificationFailed):
            engine.apply(plan, "operator", maintenance_window=True)
        run = ledger.get(plan.id)
        assert run["status"] == "VERIFICATION_FAILED"
        assert run["verification"]["passed"] is False


@pytest.mark.parametrize(
    "source_type,target_type,values",
    [
        (pa.string(), "STRING", ["alpha", None]),
        (pa.string(), "INTEGER", ["+12", "-5"]),
        (pa.string(), "BIGINT", ["9223372036854775807", "-9223372036854775808"]),
        (pa.int32(), "BIGINT", [2147483647, -2147483648]),
        (pa.int32(), "STRING", [2147483647, -2147483648]),
        (pa.int64(), "STRING", [9223372036854775807, -9223372036854775808]),
        (pa.float64(), "DOUBLE", [1.5, -0.5]),
        (pa.bool_(), "BOOLEAN", [True, False]),
        (pa.date32(), "DATE", [date(2025, 1, 1), date(2025, 2, 1)]),
        (pa.timestamp("us"), "TIMESTAMP", [datetime(2025, 1, 1, 12, 30), datetime(2025, 1, 2)]),
    ],
)
def test_supported_conversion_matrix_agrees_with_verification(
    tmp_path, bounds, source_type, target_type, values
):
    path = tmp_path / "conversion"
    delta.write_deltalake(
        path,
        pa.table(
            {
                "event_date": pa.array([date(2025, 1, 1)] * len(values), pa.date32()),
                "source": pa.array(values, source_type),
            }
        ),
        partition_by=["event_date"],
    )
    operation = Operation(source="source", target="replacement", data_type=target_type)
    with DeltaAdapter(str(path), False) as adapter:
        adapter.configure_scope("events", bounds)
        with adapter.lock("events"):
            add(adapter, operation)
            fill(adapter, operation, bounds)
        assert adapter.fetch(
            f"SELECT count(*) FROM events WHERE replacement IS DISTINCT FROM CAST(source AS {adapter.sql_type(target_type)})"
        ) == [(0,)]
