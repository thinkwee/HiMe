"""
SQL tool — query health data and memory databases.

Database dispatch
-----------------
The ``database`` parameter selects the target:
- ``health_data`` → read-only query against the streaming health SQLite DB.
- ``memory``      → read-write query against the agent's memory SQLite DB.

Backward compatibility: the legacy ``prefix:SQL`` format (e.g.
``health_data:SELECT …``) and auto-detection from table names are both
supported as fallbacks when ``database`` is not provided.

Thread safety
-------------
All SQLite I/O is offloaded to a thread pool via ``asyncio.to_thread`` so the
async event loop is never blocked by disk or lock waits.  The health-data
connection is obtained fresh for each call (no shared connection state).

Read-only enforcement
---------------------
``_readonly_authorizer`` is installed at the SQLite3 driver level for health
data connections.  This operates *before* the query parser so it cannot be
circumvented by comment tricks or multi-statement injection.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from pathlib import Path

import pandas as pd

from ...agent.memory_manager import _ensure_schema
from .base import BaseTool

logger = logging.getLogger(__name__)

# Maximum rows the agent can receive in a single query
_MAX_ROWS = 50


def _df_to_compact(df: pd.DataFrame, limit: int) -> dict:
    """
    Convert DataFrame to a compact format optimised for LLM consumption.

    Returns:
        columns: list of column names (once)
        rows:    list of lists (values only, no repeated keys)
        markdown: pipe-delimited table string (for LLM context)
        row_count: total rows before truncation
        truncated: whether result was capped at limit
    """
    total = len(df)
    df_limited = df.head(limit)
    cols = list(df_limited.columns)

    # Build rows as lists (not dicts — avoids repeating column names)
    rows = df_limited.values.tolist()

    # Format values for markdown (round floats, truncate long strings)
    def _fmt(v):
        if v is None:
            return ""
        if isinstance(v, float):
            # Drop unnecessary trailing zeros
            return f"{v:.4g}"
        s = str(v)
        return s[:80] + "…" if len(s) > 80 else s

    # Build markdown table
    header = "| " + " | ".join(cols) + " |"
    sep = "| " + " | ".join("---" for _ in cols) + " |"
    body_lines = []
    for row in rows:
        body_lines.append("| " + " | ".join(_fmt(v) for v in row) + " |")
    md = "\n".join([header, sep, *body_lines])
    if total > limit:
        md += f"\n\n*({total - limit} more rows omitted)*"

    return {
        "columns": cols,
        "rows": rows,
        "markdown": md,
        "row_count": total,
        "truncated": total > limit,
    }


class SQLTool(BaseTool):
    """Execute SQL against health_data DB (read-only) or memory DB (read-write)."""

    name = "sql"

    @property
    def is_concurrency_safe(self) -> bool:
        """SELECT queries are read-only and can run concurrently."""
        return True

    def __init__(
        self,
        data_store,
        memory_db_path: Path,
        user_id: str,
    ) -> None:
        self.data_store      = data_store
        self.memory_db_file  = memory_db_path / f"{user_id}.db"
        self.user_id  = user_id
        # Ensure mandatory schema exists (idempotent)
        with sqlite3.connect(self.memory_db_file, timeout=30) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            _ensure_schema(conn)

    # ------------------------------------------------------------------
    # Tool definition (shown to the LLM)
    # ------------------------------------------------------------------

    def get_definition(self) -> dict:
        return self._get_definition_from_json("sql")

    # ------------------------------------------------------------------
    # Execution — async, non-blocking
    # ------------------------------------------------------------------

    # Known memory-only tables for auto-detection fallback
    _MEMORY_TABLES = {"reports", "activity_log", "scheduled_tasks", "personalised_pages"}

    async def execute(
        self,
        query: str,
        database: str | None = None,
        limit: int = _MAX_ROWS,
    ) -> dict:
        """Dispatch query to the appropriate database (non-blocking).

        Routing priority:
        1. Explicit ``database`` parameter (preferred).
        2. Legacy ``prefix:SQL`` format in query string (backward compat).
        3. Auto-detect from table names in the query.
        """
        sql = query.strip()

        # --- resolve target database ---
        if database:
            db_name = database.strip().lower()
        elif ":" in sql:
            # Legacy prefix format: "health_data:SELECT ..." / "memory:SELECT ..."
            prefix, rest = sql.split(":", 1)
            prefix_lower = prefix.strip().lower()
            if prefix_lower in ("health_data", "memory"):
                db_name = prefix_lower
                sql = rest.strip()
            else:
                # Colon is part of the SQL itself (e.g. datetime literal)
                db_name = self._auto_detect_db(sql)
        else:
            db_name = self._auto_detect_db(sql)

        if limit <= 0:
            return {
                "success": False,
                "error": f"Invalid limit={limit}. Must be between 1 and {_MAX_ROWS}.",
            }
        actual_limit = min(limit, _MAX_ROWS)

        self.report_progress({"status": "executing", "database": db_name, "query_preview": sql[:100]})

        t0 = time.perf_counter()
        _QUERY_TIMEOUT = 30.0  # seconds
        if db_name == "health_data":
            coro = asyncio.to_thread(self._query_health_data, sql, actual_limit)
        elif db_name == "memory":
            coro = asyncio.to_thread(self._query_memory, sql, actual_limit)
        else:
            return {
                "success": False,
                "error": f"Unknown database '{db_name}'. Use 'health_data' or 'memory'.",
            }
        try:
            result = await asyncio.wait_for(coro, timeout=_QUERY_TIMEOUT)
        except asyncio.TimeoutError:
            elapsed = time.perf_counter() - t0
            logger.warning("sql: %s query timed out after %.1fs: %s", db_name, elapsed, sql[:120])
            return {
                "success": False,
                "error": f"Query timed out after {_QUERY_TIMEOUT:.0f}s. Try a simpler query or add tighter WHERE/LIMIT clauses.",
            }
        elapsed = time.perf_counter() - t0
        logger.info("sql: %s query took %.2fs", db_name, elapsed)

        self.report_progress({"status": "done", "row_count": result.get("row_count", 0), "elapsed": f"{elapsed:.2f}s"})

        return result

    @classmethod
    def _auto_detect_db(cls, sql: str) -> str:
        """Guess the target database from table names in the SQL.

        Uses word-boundary matching to avoid false positives from table names
        appearing inside string literals or comments.
        """
        import re
        sql_lower = sql.lower()
        for tbl in cls._MEMORY_TABLES:
            if re.search(rf'\b{tbl}\b', sql_lower):
                return "memory"
        return "health_data"

    # ------------------------------------------------------------------
    # Health data (read-only)
    # ------------------------------------------------------------------

    @staticmethod
    def _readonly_authorizer(action: int, arg1, arg2, db_name, trigger_name) -> int:
        """
        SQLite3 authorizer that denies any mutation operation.

        This runs at the driver level — before query parsing — so it cannot
        be circumvented by SQL injection or multi-statement tricks.
        """
        # Action codes that mutate state
        _DENIED = {
            1, 2, 3, 4,        # CREATE TABLE/INDEX/VIEW/TRIGGER
            9,                 # DELETE
            11, 12, 13, 14,    # DROP TABLE/INDEX/VIEW/TRIGGER
            18,                # INSERT
            23,                # UPDATE
            24, 25,            # ATTACH / DETACH
            26,                # ALTER TABLE
            27,                # REINDEX
            33,                # VACUUM
        }
        return sqlite3.SQLITE_DENY if action in _DENIED else sqlite3.SQLITE_OK

    def _query_health_data(self, sql: str, limit: int) -> dict:
        """Synchronous read-only query against the health data SQLite DB."""
        sql_upper = sql.upper().strip()
        if not (sql_upper.startswith("SELECT") or sql_upper.startswith("WITH")):
            return {
                "success": False,
                "error": "health_data database is READ-ONLY. Only SELECT (or WITH) queries are allowed.",
            }
        conn = None
        try:
            conn = self.data_store.get_connection()
            conn.set_authorizer(self._readonly_authorizer)
            df  = pd.read_sql(sql, conn)
            out = {"success": True, **_df_to_compact(df, limit)}
            if out["row_count"] == 0:
                by_feature = self.data_store.get_stats().get("by_feature", {})
                if by_feature:
                    out["available_feature_types"] = sorted(by_feature.keys())
            return out
        except Exception as exc:
            err = str(exc)
            if "not authorized" in err.lower():
                err = "Security violation: query attempted to modify health data."
            return {"success": False, "error": err}
        finally:
            if conn:
                conn.close()

    # ------------------------------------------------------------------
    # Memory (read-write)
    # ------------------------------------------------------------------

    def _query_memory(self, sql: str, limit: int) -> dict:
        """Synchronous read-write query against the agent memory SQLite DB."""
        try:
            with sqlite3.connect(self.memory_db_file, timeout=30) as conn:
                _ensure_schema(conn)
                sql_upper = sql.upper().strip()
                if sql_upper.startswith("SELECT") or sql_upper.startswith("WITH"):
                    df = pd.read_sql(sql, conn)
                    return {"success": True, **_df_to_compact(df, limit)}
                else:
                    cur = conn.execute(sql)
                    conn.commit()
                    return {
                        "success":       True,
                        "rows_affected": cur.rowcount,
                        "message":       "Query executed successfully.",
                    }
        except Exception as exc:
            return {"success": False, "error": str(exc)}
