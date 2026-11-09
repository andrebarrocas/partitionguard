"""Measure one cold-process DuckDB fingerprint, excluding fixture generation."""

import argparse
import json
import platform
import resource
import sys
import tempfile
import time
from importlib.metadata import version
from pathlib import Path

import duckdb

from partitionguard.adapters import open_adapter
from partitionguard.models import Bounds, Column, Contract, MigrationSpec
from partitionguard.planning import plan
from partitionguard.verification import capture_source_partition


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, required=True)
    parser.add_argument("--width", type=int, required=True)
    args = parser.parse_args()
    if (
        not 1 <= args.rows <= 1_000_000
        or not 1 <= args.width <= 8192
        or args.rows * args.width > 256 * 1024**2
    ):
        parser.error(
            "Fixture must be within 1M rows, 8 KiB per payload and 256 MiB of logical payload"
        )
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "fixture.duckdb"
        if not path.exists():
            with duckdb.connect(str(path)) as db:
                db.execute(
                    "CREATE TABLE events AS SELECT i::BIGINT AS id, DATE '2026-01-01' AS day, "
                    "left(repeat(md5(i::VARCHAR), ?), ?) AS payload FROM range(?) t(i)",
                    ((args.width + 31) // 32, args.width, args.rows),
                )
        # A separate measurement process excludes fixture generation from peak RSS.
        import subprocess

        result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "_measure",
                str(path),
                str(args.rows),
                str(args.width),
            ],
            stdout=subprocess.PIPE,
            text=True,
            check=True,
        )
        print(result.stdout, end="")


def measure(path, rows, width):
    contract = Contract(
        product="benchmark",
        version="1",
        owner="benchmark",
        table="events",
        columns=(
            Column(name="id", data_type="BIGINT"),
            Column(name="day", data_type="DATE"),
            Column(name="payload", data_type="STRING"),
        ),
        partition_by=("day",),
    )
    value = plan(
        contract,
        contract,
        migration=MigrationSpec(
            bounds=Bounds(
                column="day",
                start="2026-01-01",
                end="2026-01-02",
                max_rows_per_partition=rows,
                max_total_rows=rows,
            )
        ),
    )
    with open_adapter("duckdb", path, read_only=True) as adapter:
        db = adapter._connection
        db.execute("SET threads=2")
        start = time.perf_counter()
        result = capture_source_partition(adapter, value, "2026-01-01")
        elapsed = time.perf_counter() - start
        usage = resource.getrusage(resource.RUSAGE_SELF)
        metrics = json.loads(
            db.execute(
                "EXPLAIN (ANALYZE, FORMAT JSON) SELECT day,id,payload FROM events "
                "WHERE day >= DATE '2026-01-01' AND day < DATE '2026-01-02' LIMIT ?",
                [rows + 1],
            ).fetchone()[1]
        )
    print(
        json.dumps(
            {
                "rows": rows,
                "payload_bytes_per_row": width,
                "logical_payload_bytes": rows * width,
                "seconds": elapsed,
                "peak_rss_bytes": usage.ru_maxrss * (1 if sys.platform == "darwin" else 1024),
                "process_cpu_seconds": usage.ru_utime + usage.ru_stime,
                "threads": 2,
                "database_cpu_seconds": metrics["cpu_time"],
                "database_rows_scanned": metrics["cumulative_rows_scanned"],
                "database_storage_bytes_read": metrics["total_bytes_read"],
                "database_peak_buffer_bytes": metrics["system_peak_buffer_memory"],
                "fingerprint": result["fingerprint"],
                "python": platform.python_version(),
                "platform": platform.platform(),
                "duckdb": version("duckdb"),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    if sys.argv[1:2] == ["_measure"]:
        measure(sys.argv[2], int(sys.argv[3]), int(sys.argv[4]))
    else:
        main()
