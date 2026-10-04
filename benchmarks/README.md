# Reproducible verification benchmark

Run from the repository after installing `.[duckdb]`:

```sh
python benchmarks/verification.py --rows 1000 10000 --repeats 3 --output benchmarks/results-local.json
```

The script constructs a fresh local DuckDB table, warms one baseline read, then
measures exact bounded source fingerprint capture. It records each duration,
median, Python-tracked peak allocation, interpreter, CPU architecture, OS, driver,
row count and methodology. `results-local.json` is an actual observed run, not a
performance target. It has three scalar columns and one date partition.

The checked-in run measured medians of about 0.015 seconds for 1,000 rows and
0.129 seconds for 10,000 rows on Darwin arm64 / Python 3.11.14 / DuckDB 1.5.6.
Tracemalloc was enabled, so these are instrumented times. Peak tracked Python
allocations were roughly 226 KB and 2.75 MB. Native engine buffers, process RSS,
I/O under cold cache and production data distributions are not captured.

This is a small scaling probe for the exact fingerprint path. It does not measure
warehouse-scale backfills, compare adapters, establish a throughput SLA, or
justify a Rust rewrite. Source fingerprints currently materialize bounded data;
users should choose conservative caps. Streaming fingerprints and larger-scale
profiling are possible future work if actual workloads demonstrate the need.
