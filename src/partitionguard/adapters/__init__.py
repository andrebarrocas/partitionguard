"""Storage adapters are loaded only when explicitly selected."""

from .sql import DuckDBAdapter, PostgresAdapter, quote


def open_adapter(name, connection, read_only=True):
    if name == "duckdb":
        return DuckDBAdapter(connection, read_only=read_only)
    if name == "postgres":
        return PostgresAdapter(connection, read_only=read_only)
    if name == "delta":
        from .delta import DeltaAdapter

        return DeltaAdapter(connection, read_only=read_only)
    from partitionguard.plugins import load_plugin

    factory = load_plugin("storage", name)
    return factory(connection, read_only=read_only)


__all__ = ["open_adapter", "quote", "DuckDBAdapter", "PostgresAdapter"]
