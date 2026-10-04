"""Deterministic, bounded migration evidence; no row data is persisted."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
from importlib.metadata import version

from partitionguard.adapters.sql import quote
from partitionguard.models import canonical_json, normalize_type


def _migration(plan):
    if plan.migration is None:
        raise ValueError("Execution requires an explicitly bounded migration")
    return plan.migration


def _scope(adapter, plan, partition=None):
    bounds = _migration(plan).bounds
    if partition is not None:
        if partition not in bounds.partitions():
            raise ValueError("Partition is outside the approved bounds")
        start = date.fromisoformat(partition)
        end = start + timedelta(days=1)
    else:
        start, end = bounds.start, bounds.end
    col = quote(bounds.column)
    return f"{col} >= {adapter.placeholder} AND {col} < {adapter.placeholder}", (start, end)


def _cell(value):
    # Explicit type tags avoid string/int/date collisions and serialize IEEE
    # nonfinite values without invalid JSON or platform-dependent reprs.
    if value is None:
        return ["null"]
    if isinstance(value, bool):
        return ["boolean", value]
    if isinstance(value, int):
        return ["integer", str(value)]
    if isinstance(value, float):
        return ["float", value.hex()]
    if isinstance(value, Decimal):
        return ["decimal", str(value)]
    if isinstance(value, datetime):
        return ["datetime", value.isoformat()]
    if isinstance(value, date):
        return ["date", value.isoformat()]
    if isinstance(value, str):
        return ["string", value]
    if isinstance(value, bytes):
        return ["bytes", value.hex()]
    raise ValueError(f"Unsupported fingerprint value type: {type(value).__name__}")


def _fingerprint(rows):
    encoded = sorted(canonical_json([_cell(cell) for cell in row]).encode() for row in rows)
    hasher = sha256()
    for item in encoded:
        hasher.update(len(item).to_bytes(8, "big"))
        hasher.update(item)
    return hasher.hexdigest()


def capture_baseline(adapter, plan):
    migration = _migration(plan)
    bounds = migration.bounds
    actual = adapter.schema(plan.current.table)
    original = {col.name: normalize_type(col.data_type) for col in plan.current.columns}
    proposed = {col.name: normalize_type(col.data_type) for col in plan.proposed.columns}
    if any(actual.get(name) != kind for name, kind in original.items()):
        raise ValueError("Live source schema differs from the current contract")
    if set(actual) - set(original) - set(proposed):
        raise ValueError("Live table contains columns absent from both approved contracts")
    if actual.get(bounds.column) != "DATE":
        raise ValueError("Initial bounded execution requires a DATE partition column")
    columns = ", ".join(quote(name) for name in sorted(original))
    partitions = {}
    total = 0
    for partition in bounds.partitions():
        scope, params = _scope(adapter, plan, partition)
        rows = adapter.fetch(
            f"SELECT {columns} FROM {quote(plan.current.table)} WHERE {scope} "
            f"LIMIT {bounds.max_rows_per_partition + 1}",
            params,
        )
        if len(rows) > bounds.max_rows_per_partition:
            raise ValueError(f"Partition {partition} exceeds its row cap")
        if migration.require_nonempty and not rows:
            raise ValueError(f"Required partition {partition} is empty")
        total += len(rows)
        if total > bounds.max_total_rows:
            raise ValueError("Bounded dataset exceeds the approved total row cap")
        partitions[partition] = {"row_count": len(rows), "fingerprint": _fingerprint(rows)}
    from partitionguard import __version__

    dependencies = {
        "duckdb": ("duckdb",),
        "postgres": ("psycopg",),
        "delta": ("duckdb", "deltalake", "pyarrow"),
    }.get(adapter.name, ())
    runtime = {
        "adapter": adapter.name,
        "core": __version__,
        "dependencies": {name: version(name) for name in dependencies},
        "backend": getattr(adapter, "runtime_signature", {}),
    }
    return {
        "schema": original,
        "partitions": partitions,
        "total_rows": total,
        "runtime_signature": runtime,
    }


def _check(name, passed, observed, expected, partition=None):
    return {
        "name": name,
        "partition": partition,
        "passed": bool(passed),
        "observed": observed,
        "expected": expected,
    }


def _error(name, exc, partition=None):
    # Driver errors can contain failing row values, SQL, or credential-bearing
    # connection details. Persist a classified error, never the raw exception.
    return _check(name, False, {"error": type(exc).__name__}, "successful evaluation", partition)


def _result(checks):
    failed = sum(not check["passed"] for check in checks)
    return {
        "passed": bool(checks) and not failed,
        "checks": checks,
        "summary": {"total": len(checks), "passed": len(checks) - failed, "failed": failed},
    }


def _expression(adapter, operation):
    source = quote(operation.source)
    if operation.transform == "copy":
        return source
    if operation.transform == "cast":
        return f"CAST({source} AS {adapter.sql_type(operation.data_type)})"
    raise ValueError("Unsupported transformation")


def verify_conversion(adapter, plan):
    """Evaluate every planned conversion before mutating the destination."""
    checks = []
    migration = _migration(plan)
    for operation in migration.operations:
        source_type = plan.current.column_map[operation.source].data_type
        target_type = operation.data_type
        pairs = {
            ("STRING", "INTEGER"),
            ("STRING", "BIGINT"),
            ("INTEGER", "BIGINT"),
            ("INTEGER", "STRING"),
            ("BIGINT", "STRING"),
        }
        if (source_type != target_type and (source_type, target_type) not in pairs) or (
            operation.transform == "copy" and source_type != target_type
        ):
            checks.append(
                _check(
                    "supported_conversion", False, [source_type, target_type], "tested cast pair"
                )
            )
            continue
        validator = getattr(adapter, "validate_operation", None)
        if validator:
            try:
                validator(plan.current.table, operation)
            except Exception as exc:
                checks.append(_error("supported_conversion", exc))
                continue
        for partition in migration.bounds.partitions():
            name = f"conversion:{operation.source}->{operation.target}"
            try:
                scope, params = _scope(adapter, plan, partition)
                rows = adapter.fetch(
                    f"SELECT {quote(operation.source)}, {_expression(adapter, operation)} FROM {quote(plan.current.table)} "
                    f"WHERE {scope} LIMIT {migration.bounds.max_rows_per_partition + 1}",
                    params,
                )
                within_cap = len(rows) <= migration.bounds.max_rows_per_partition
                strict_values = not (
                    source_type == "STRING" and target_type in ("INTEGER", "BIGINT")
                ) or all(
                    value is None or re.fullmatch(r"[+-]?[0-9]+", value) is not None
                    for value, _ in rows
                )
                checks.append(
                    _check(
                        name,
                        within_cap and strict_values,
                        {"evaluated_rows": len(rows), "strict_values": strict_values},
                        "strict lossless conversion within row cap",
                        partition,
                    )
                )
            except Exception as exc:
                checks.append(_error(name, exc, partition))
    if not checks:
        checks.append(_check("conversion", True, 0, "no conversion operations"))
    return _result(checks)


def verify_operation(adapter, plan, operation, partition):
    """Reconcile one committed or uncertain action without requiring later work."""
    checks = []
    name = f"parity:{operation.source}->{operation.target}"
    try:
        actual = adapter.schema(plan.current.table)
        expected = normalize_type(operation.data_type)
        if actual.get(operation.target) != expected:
            return _result([_check(name, False, actual.get(operation.target), expected, partition)])
        scope, params = _scope(adapter, plan, partition)
        mismatches = adapter.fetch(
            f"SELECT COUNT(*) FROM {quote(plan.current.table)} WHERE {scope} "
            f"AND {quote(operation.target)} IS DISTINCT FROM {_expression(adapter, operation)}",
            params,
        )[0][0]
        checks.append(_check(name, mismatches == 0, mismatches, 0, partition))
    except Exception as exc:
        checks.append(_error(name, exc, partition))
    return _result(checks)


def _scalar(adapter, plan, expression, predicate=None):
    scope, params = _scope(adapter, plan)
    if predicate:
        scope = f"({scope}) AND ({predicate})"
    return adapter.fetch(
        f"SELECT {expression} FROM {quote(plan.current.table)} WHERE {scope}", params
    )[0][0]


def _uniqueness(adapter, plan, names):
    if not names:
        raise ValueError("Uniqueness requires columns")
    scope, params = _scope(adapter, plan)
    cols = ", ".join(quote(name) for name in names)
    return adapter.fetch(
        f"SELECT COUNT(*) FROM (SELECT {cols} FROM {quote(plan.current.table)} WHERE {scope} "
        f"GROUP BY {cols} HAVING COUNT(*) > 1) AS duplicate_groups",
        params,
    )[0][0]


def _enum_violations(adapter, plan, column, values):
    if not values:
        raise ValueError("Enumeration must have at least one allowed value")
    scope, params = _scope(adapter, plan)
    marks = ", ".join(adapter.placeholder for _ in values)
    col = quote(column)
    return adapter.fetch(
        f"SELECT COUNT(*) FROM {quote(plan.current.table)} WHERE ({scope}) "
        f"AND ({col} IS NOT NULL AND {col} NOT IN ({marks}))",
        (*params, *values),
    )[0][0]


def _custom_sql(adapter, plan, sql):
    import sqlglot
    from sqlglot import exp

    if not sql or sql.count("{scope}") != 1:
        raise ValueError("SQL assertion must reference {scope} exactly once")
    statements = sqlglot.parse(
        sql.replace("{scope}", "pg_scope"), read=getattr(adapter, "dialect", "duckdb")
    )
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        raise ValueError("SQL assertion must be one SELECT")
    tree = statements[0]
    tables = list(tree.find_all(exp.Table))
    if len(tree.expressions) != 1 or len(tables) != 1 or tables[0].name != "pg_scope":
        raise ValueError("SQL assertion must produce one scalar from {scope}")
    if tables[0].db or tables[0].catalog or tables[0].alias:
        raise ValueError("The assertion scope cannot be qualified or aliased")
    forbidden = (
        exp.DML,
        exp.DDL,
        exp.Into,
        exp.Lock,
        exp.Subquery,
        exp.Join,
        exp.CTE,
        exp.Window,
        exp.TableSample,
    )
    if any(isinstance(node, forbidden) for node in tree.walk()):
        raise ValueError("SQL assertion cannot write, join, nest queries, or use windows")
    safe_functions = {
        "COUNT",
        "SUM",
        "AVG",
        "MIN",
        "MAX",
        "ABS",
        "COALESCE",
        "NULLIF",
        "CAST",
        "ROUND",
        "FLOOR",
        "CEIL",
        "LENGTH",
        "LOWER",
        "UPPER",
    }
    for function in tree.find_all(exp.Func):
        if function.sql_name().upper() not in safe_functions:
            raise ValueError("SQL assertion contains an unsupported function")
    for cast in tree.find_all(exp.Cast):
        normalize_type(cast.args["to"].sql(dialect=getattr(adapter, "dialect", "duckdb")))
    if getattr(adapter, "dialect", "duckdb") == "postgres":
        # Do not let an exact user-defined overload shadow polymorphic builtins
        # such as count(text). SQL syntax forms COALESCE/NULLIF/CAST stay syntax.
        for function in reversed(list(tree.find_all(exp.Func))):
            if function.sql_name().upper() not in {"CAST", "COALESCE", "NULLIF"}:
                function.replace(
                    exp.Dot(this=exp.to_identifier("pg_catalog"), expression=function.copy())
                )
    scope, params = _scope(adapter, plan)
    dialect = getattr(adapter, "dialect", "duckdb")
    source = sqlglot.parse_one(
        f"SELECT * FROM {quote(plan.current.table)} WHERE {scope}", read=dialect
    )
    tables[0].replace(
        exp.Subquery(this=source, alias=exp.TableAlias(this=exp.to_identifier("scope")))
    )
    rendered = tree.sql(dialect=dialect)
    rows = adapter.fetch(f"SELECT * FROM ({rendered}) AS assertion_result LIMIT 2", params)
    return len(rows) == 1 and len(rows[0]) == 1 and rows[0][0] is True


def _run_optional(adapter, plan, check):
    name = "policy:" + check.kind + (":" + check.column if check.column else "")
    try:
        column = quote(check.column) if check.column else None
        if check.kind == "unique":
            observed = _uniqueness(adapter, plan, check.columns or (check.column,))
            return _check(name, observed == 0, observed, 0)
        if check.kind == "not_null":
            observed = _scalar(adapter, plan, "COUNT(*)", f"{column} IS NULL")
            return _check(name, observed == 0, observed, 0)
        if check.kind == "null_rate":
            total = _scalar(adapter, plan, "COUNT(*)")
            if not total:
                return _check(name, False, None, "nonempty bounded scope")
            rate = _scalar(adapter, plan, "COUNT(*)", f"{column} IS NULL") / total
            if check.reference:
                other = (
                    _scalar(adapter, plan, "COUNT(*)", f"{quote(check.reference)} IS NULL") / total
                )
                observed = abs(rate - other)
            else:
                observed = rate
            return _check(name, observed <= check.threshold, observed, {"maximum": check.threshold})
        if check.kind == "aggregate":
            observed = _scalar(adapter, plan, f"SUM({column})")
            expected = _scalar(adapter, plan, f"SUM({quote(check.reference)})")
            if observed is None or expected is None:
                return _check(name, False, observed, expected)
            actual_number, expected_number = Decimal(str(observed)), Decimal(str(expected))
            tolerance = Decimal(str(check.absolute_tolerance)) + Decimal(
                str(check.relative_tolerance)
            ) * abs(expected_number)
            passed = (
                all(number.is_finite() for number in (actual_number, expected_number, tolerance))
                and abs(actual_number - expected_number) <= tolerance
            )
            return _check(
                name,
                passed,
                str(actual_number),
                {"value": str(expected_number), "tolerance": str(tolerance)},
            )
        if check.kind == "freshness":
            latest = _scalar(adapter, plan, f"MAX({column})")
            if not isinstance(latest, (date, datetime)):
                return _check(name, False, None, "a timestamp in a nonempty scope")
            if not isinstance(latest, datetime):
                latest = datetime.combine(latest, datetime.min.time())
            if latest.tzinfo is None:
                latest = latest.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - latest).total_seconds()
            return _check(
                name, 0 <= age <= check.threshold, age, {"maximum_seconds": check.threshold}
            )
        if check.kind == "enum":
            observed = _enum_violations(adapter, plan, check.column, check.values)
            return _check(name, observed == 0, observed, 0)
        if check.kind == "range":
            lower, upper = check.values
            scope, params = _scope(adapter, plan)
            observed = adapter.fetch(
                f"SELECT COUNT(*) FROM {quote(plan.current.table)} WHERE ({scope}) AND "
                f"({column} IS NULL OR {column} < {adapter.placeholder} OR {column} > {adapter.placeholder})",
                (*params, lower, upper),
            )[0][0]
            return _check(name, observed == 0, observed, 0)
        if check.kind == "sql":
            passed = _custom_sql(adapter, plan, check.sql)
            return _check(name, passed, passed, True)
        raise ValueError("Unsupported verification policy")
    except Exception as exc:
        return _error(name, exc)


def verify(adapter, plan, baseline):
    """Accept only complete scoped schema, source integrity, parity and policies."""
    checks = []
    migration = _migration(plan)
    try:
        current = capture_baseline(adapter, plan)
        checks.append(
            _check(
                "runtime_signature",
                current["runtime_signature"] == baseline.get("runtime_signature"),
                current["runtime_signature"],
                baseline.get("runtime_signature"),
            )
        )
        for partition in migration.bounds.partitions():
            expected = baseline["partitions"][partition]
            observed = current["partitions"][partition]
            checks.append(
                _check(
                    "row_count",
                    observed["row_count"] == expected["row_count"],
                    observed["row_count"],
                    expected["row_count"],
                    partition,
                )
            )
            checks.append(
                _check(
                    "source_fingerprint",
                    observed["fingerprint"] == expected["fingerprint"],
                    observed["fingerprint"],
                    expected["fingerprint"],
                    partition,
                )
            )
            checks.append(
                _check(
                    "partition_completeness",
                    not migration.require_nonempty or observed["row_count"] > 0,
                    observed["row_count"],
                    "nonempty" if migration.require_nonempty else "empty permitted",
                    partition,
                )
            )
    except Exception as exc:
        checks.append(_error("baseline_integrity", exc))
        # A rejected scope/cap must not trigger further scans or policy queries.
        return _result(checks)
    try:
        actual_schema = adapter.schema(plan.current.table)
        expected_schema = {col.name: normalize_type(col.data_type) for col in plan.proposed.columns}
        checks.append(
            _check("schema", actual_schema == expected_schema, actual_schema, expected_schema)
        )
    except Exception as exc:
        checks.append(_error("schema", exc))
    for operation in migration.operations:
        for partition in migration.bounds.partitions():
            checks.extend(verify_operation(adapter, plan, operation, partition)["checks"])
    for label, contract in (("source", plan.current), ("target", plan.proposed)):
        for column in contract.columns:
            try:
                if not column.nullable:
                    count = _scalar(adapter, plan, "COUNT(*)", f"{quote(column.name)} IS NULL")
                    checks.append(_check(f"{label}:not_null:{column.name}", count == 0, count, 0))
                if column.enum:
                    count = _enum_violations(adapter, plan, column.name, column.enum)
                    checks.append(_check(f"{label}:enum:{column.name}", count == 0, count, 0))
            except Exception as exc:
                checks.append(_error(f"{label}:column:{column.name}", exc))
        for key_label, names in (
            ("primary_key", contract.primary_key),
            ("business_keys", contract.business_keys),
        ):
            if names:
                try:
                    count = _uniqueness(adapter, plan, names)
                    nulls = _scalar(
                        adapter,
                        plan,
                        "COUNT(*)",
                        " OR ".join(f"{quote(name)} IS NULL" for name in names),
                    )
                    checks.append(
                        _check(
                            f"{label}:{key_label}",
                            count == nulls == 0,
                            {"duplicate_groups": count, "null_rows": nulls},
                            {"duplicate_groups": 0, "null_rows": 0},
                        )
                    )
                except Exception as exc:
                    checks.append(_error(f"{label}:{key_label}", exc))
        for check in contract.quality:
            checks.append(_run_optional(adapter, plan, check))
    for check in migration.checks:
        checks.append(_run_optional(adapter, plan, check))
    return _result(checks)
