"""
Page Helpers — Pre-built backend utilities for agent-created pages.

These functions are auto-injected into every page's route.py, giving the agent
easy access to health data queries, memory DB read/write, and custom table
management without needing to write boilerplate SQLite code.

Usage in agent-generated backend_code:
    def route_handler(request):
        # Read health data
        hr = query_health("heart_rate", days=7)
        sleep = query_health(["sleep_core", "sleep_deep", "sleep_rem"], days=3)

        # Read/write memory DB (custom tables)
        ensure_table("mood_log", {"ts": "TEXT", "mood": "INTEGER", "note": "TEXT"})
        write_memory("INSERT INTO mood_log (ts, mood, note) VALUES (?, ?, ?)",
                     [datetime.now().isoformat(), 8, "Feeling great"])
        entries = query_memory("SELECT * FROM mood_log ORDER BY ts DESC LIMIT 20")

        # Combine health + custom data
        return {"status": "success", "health": hr, "mood": entries}
"""
import json
import re
import sqlite3
import statistics
from datetime import datetime, timedelta

# --- SQL safety guardrails ----------------------------------------------
# Personalised pages run agent-generated Python that calls query_memory /
# write_memory. We treat the SQL passed to those helpers as untrusted and
# enforce simple but effective restrictions:
#
#   - read SQL must start with SELECT (no PRAGMA, no ATTACH, no recursion
#     into sqlite_master / sqlite_schema)
#   - write SQL must start with INSERT / UPDATE / DELETE / CREATE TABLE
#   - no semicolons (no multi-statement injection)
#   - identifiers passed to ensure_table must match a strict regex
#
# These checks are not bullet-proof against a determined attacker with full
# control over both the SQL string and its parameters, but they raise the
# bar significantly above "anything goes" and catch the vast majority of
# bad LLM output.

_FORBIDDEN_SQL_TOKENS = re.compile(
    r"\b(sqlite_master|sqlite_schema|sqlite_temp_master|attach\s+database|detach\s+database|pragma)\b",
    re.IGNORECASE,
)
_VALID_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_VALID_COLUMN_TYPE = re.compile(
    r"^(?:INTEGER|REAL|TEXT|BLOB|NUMERIC|BOOLEAN|DATE|DATETIME|TIMESTAMP)"
    r"(?:\s+(?:NOT\s+NULL|PRIMARY\s+KEY|UNIQUE|DEFAULT\s+[A-Za-z0-9_'\".\-]+|AUTOINCREMENT))*$",
    re.IGNORECASE,
)


def _validate_sql(sql: str, *, allowed_starts: tuple[str, ...]) -> None:
    """Reject SQL that doesn't start with one of the expected verbs or that
    references forbidden identifiers / multi-statement separators."""
    if not isinstance(sql, str):
        raise ValueError("SQL must be a string")
    stripped = sql.strip().rstrip(";")
    if ";" in stripped:
        raise ValueError("Multi-statement SQL is not allowed")
    head = stripped.split(None, 1)[0].upper() if stripped else ""
    if head not in allowed_starts:
        raise ValueError(
            f"SQL must start with one of {allowed_starts}, got {head!r}"
        )
    if _FORBIDDEN_SQL_TOKENS.search(stripped):
        raise ValueError("SQL references a forbidden identifier or PRAGMA")


def _get_health_conn() -> sqlite3.Connection:
    """Get a read-only connection to the health database."""
    conn = sqlite3.connect(f"file:{HEALTH_DB_PATH}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _get_memory_conn() -> sqlite3.Connection:
    """Get a read-write connection to the memory database."""
    conn = sqlite3.connect(MEMORY_DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def query_health(
    feature_types: "str | list[str]",
    days: int = 7,
    start: str | None = None,
    end: str | None = None,
    limit: int = 5000,
    agg: str | None = None,
    agg_interval: str = "day",
) -> list[dict]:
    """Query health data with sensible defaults.

    Args:
        feature_types: Single feature or list, e.g. "heart_rate" or ["sleep_core", "sleep_deep"]
        days: Look back N days from now (ignored if start is set)
        start: ISO timestamp for range start (optional)
        end: ISO timestamp for range end (optional, defaults to now)
        limit: Max rows returned
        agg: Aggregation function — "avg", "sum", "min", "max", "count" (optional)
        agg_interval: "hour" or "day" (used when agg is set)

    Returns:
        Without agg: list of {timestamp, feature_type, value}.
        With agg:    list of {period, feature_type, value}.
                     Note the time key is "period", not "timestamp".
    """
    if isinstance(feature_types, str):
        feature_types = [feature_types]

    placeholders = ",".join("?" * len(feature_types))
    if not start:
        start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
    if not end:
        end = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    conn = _get_health_conn()
    try:
        if agg:
            agg_fn = agg.upper()
            if agg_fn not in ("AVG", "SUM", "MIN", "MAX", "COUNT"):
                raise ValueError(f"Invalid agg function: {agg}")
            if agg_interval == "hour":
                group_expr = "strftime('%Y-%m-%dT%H:00:00', timestamp)"
            else:
                group_expr = "strftime('%Y-%m-%d', timestamp)"
            sql = (
                f"SELECT {group_expr} AS period, feature_type, "
                f"{agg_fn}(value) AS value "
                f"FROM samples WHERE feature_type IN ({placeholders}) "
                f"AND timestamp BETWEEN ? AND ? "
                f"GROUP BY period, feature_type ORDER BY period"
            )
            rows = conn.execute(sql, [*feature_types, start, end]).fetchall()
            return [{"period": r["period"], "feature_type": r["feature_type"],
                     "value": round(r["value"], 2)} for r in rows]
        else:
            sql = (
                f"SELECT timestamp, feature_type, value FROM samples "
                f"WHERE feature_type IN ({placeholders}) "
                f"AND timestamp BETWEEN ? AND ? "
                f"ORDER BY timestamp DESC LIMIT ?"
            )
            rows = conn.execute(sql, [*feature_types, start, end, limit]).fetchall()
            return [dict(r) for r in rows]
    finally:
        conn.close()


def query_memory(sql: str, params: list | None = None) -> list[dict]:
    """Read from the memory database (SELECT only).

    Args:
        sql: SELECT query (must start with SELECT, no semicolons,
             no references to sqlite_master / PRAGMA)
        params: Query parameters

    Returns:
        List of dicts
    """
    _validate_sql(sql, allowed_starts=("SELECT", "WITH"))
    conn = _get_memory_conn()
    try:
        rows = conn.execute(sql, params or []).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def write_memory(sql: str, params: list | None = None) -> int:
    """Write to the memory database (INSERT/UPDATE/DELETE).

    Args:
        sql: Write query (must start with INSERT/UPDATE/DELETE, no
             semicolons, no PRAGMA / ATTACH)
        params: Query parameters

    Returns:
        Number of affected rows
    """
    _validate_sql(sql, allowed_starts=("INSERT", "UPDATE", "DELETE", "REPLACE"))
    conn = _get_memory_conn()
    try:
        cur = conn.execute(sql, params or [])
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def ensure_table(table_name: str, columns: dict[str, str],
                 if_not_exists: bool = True) -> None:
    """Create a table in the memory database if it doesn't exist.

    Args:
        table_name: Table name (alphanumeric + underscore only)
        columns: Dict of column_name -> SQL type, e.g. {"ts": "TEXT", "value": "REAL"}
        if_not_exists: Add IF NOT EXISTS clause (default True)

    Constraints (rejected with ValueError otherwise):
        - An ``id INTEGER PRIMARY KEY AUTOINCREMENT`` column is added
          automatically. Do NOT include ``id`` in ``columns``.
        - Column types are restricted to: INTEGER, REAL, TEXT, BLOB, NUMERIC,
          BOOLEAN, DATE, DATETIME, TIMESTAMP — optionally followed by
          ``NOT NULL`` / ``UNIQUE`` / ``DEFAULT <literal>``. ``DEFAULT`` only
          accepts a literal value; function calls like
          ``DEFAULT (strftime(...))`` or ``DEFAULT CURRENT_TIMESTAMP`` are
          rejected. Fill timestamps in your INSERT instead
          (e.g. ``datetime.now().isoformat()``).

    Example:
        ensure_table("mood_log", {
            "ts": "TEXT NOT NULL",
            "mood": "INTEGER",
            "note": "TEXT",
            "energy": "REAL"
        })
    """
    if not _VALID_IDENTIFIER.match(table_name):
        raise ValueError(f"Invalid table name: {table_name}")
    for col_name, col_type in columns.items():
        if not _VALID_IDENTIFIER.match(col_name):
            raise ValueError(f"Invalid column name: {col_name}")
        if not _VALID_COLUMN_TYPE.match(str(col_type).strip()):
            raise ValueError(
                f"Invalid column type for {col_name!r}: {col_type!r}. "
                "Use a simple SQLite type with optional NOT NULL / DEFAULT modifiers."
            )

    col_defs = ", ".join(f"{name} {typ}" for name, typ in columns.items())
    exists_clause = "IF NOT EXISTS " if if_not_exists else ""
    sql = f"CREATE TABLE {exists_clause}{table_name} (id INTEGER PRIMARY KEY AUTOINCREMENT, {col_defs})"

    conn = _get_memory_conn()
    try:
        conn.execute(sql)
        conn.commit()
    finally:
        conn.close()


def health_stats(feature_type: str, days: int = 7) -> dict:
    """Get summary statistics for a health feature.

    Returns:
        Dict with keys: count, mean, min, max, std, latest_value, latest_time
    """
    data = query_health(feature_type, days=days, limit=50000)
    if not data:
        return {"count": 0, "mean": None, "min": None, "max": None,
                "std": None, "latest_value": None, "latest_time": None}

    values = [r["value"] for r in data]
    return {
        "count": len(values),
        "mean": round(statistics.mean(values), 2),
        "min": round(min(values), 2),
        "max": round(max(values), 2),
        "std": round(statistics.stdev(values), 2) if len(values) > 1 else 0,
        "latest_value": data[0]["value"],
        "latest_time": data[0]["timestamp"],
    }


def parse_request_body(request) -> dict:
    """Parse JSON body from a POST request (sync-safe).

    The framework pre-reads the body into request._body before calling
    sync route handlers, so this always works in both sync and async contexts.

    Returns:
        Parsed dict, or empty dict if no body.
    """
    try:
        body = getattr(request, '_body', b'')
        if body:
            return json.loads(body)
    except Exception:
        pass
    return {}


def get_query_params(request) -> dict:
    """Extract query parameters from the request URL.

    Returns:
        Dict of query parameter key-value pairs.
    """
    try:
        return dict(request.query_params)
    except Exception:
        return {}
