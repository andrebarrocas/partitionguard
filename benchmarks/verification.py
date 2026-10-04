"""Measure bounded exact source fingerprinting; no unmeasured Rust claims."""

import argparse
import json
import platform
import statistics
import tempfile
import time
import tracemalloc
from datetime import date
from pathlib import Path

import duckdb

from partitionguard.adapters import open_adapter
from partitionguard.models import Bounds, Column, Contract, MigrationSpec
from partitionguard.planning import plan
from partitionguard.verification import capture_baseline

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--rows", nargs="+", type=int, default=[1000, 10000])
parser.add_argument("--repeats", type=int, default=3)
parser.add_argument("--output", type=Path)
args = parser.parse_args()
if not 1 <= args.repeats <= 20 or any(not 1 <= n <= 1_000_000 for n in args.rows):
    parser.error("Use 1..20 repeats and 1..1000000 rows")
results = []
for n in args.rows:
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "benchmark.duckdb"
        with duckdb.connect(str(path)) as db:
            db.execute(
                "CREATE TABLE events AS SELECT i::BIGINT AS id, DATE '2026-01-01' AS day, CAST(i AS VARCHAR) AS code FROM range(?) t(i)",
                [n],
            )
        c = Contract(
            product="benchmark",
            version="1",
            owner="benchmark",
            table="events",
            columns=(
                Column(name="id", data_type="BIGINT"),
                Column(name="day", data_type="DATE"),
                Column(name="code", data_type="STRING"),
            ),
            partition_by=("day",),
            primary_key=("id",),
        )
        p = plan(
            c,
            c,
            migration=MigrationSpec(
                bounds=Bounds(
                    column="day",
                    start=date(2026, 1, 1),
                    end=date(2026, 1, 2),
                    max_rows_per_partition=n,
                    max_total_rows=n,
                )
            ),
        )
        durations, peaks = [], []
        with open_adapter("duckdb", str(path), read_only=True) as adapter:
            capture_baseline(adapter, p)
            for _ in range(args.repeats):
                tracemalloc.start()
                start = time.perf_counter()
                capture_baseline(adapter, p)
                durations.append(time.perf_counter() - start)
                peaks.append(tracemalloc.get_traced_memory()[1])
                tracemalloc.stop()
        results.append(
            {
                "rows": n,
                "seconds_median": statistics.median(durations),
                "seconds_runs": durations,
                "peak_python_bytes_max": max(peaks),
            }
        )
result = {
    "python": platform.python_version(),
    "platform": platform.system(),
    "machine": platform.machine(),
    "duckdb": duckdb.__version__,
    "metric": "capture_baseline with tracemalloc enabled; warm local cache; three columns; one DATE partition",
    "repeats": args.repeats,
    "results": results,
}
text = json.dumps(result, indent=2) + "\n"
if args.output:
    args.output.write_text(text)
print(text)
