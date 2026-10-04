"""
SQL tool — query health data and memory databases.

Database dispatch
-----------------
The ``database`` parameter selects the target:
- ``health_data`` → read-only query against the streaming health SQLite DB.
- ``memory``      → the agent's memory SQLite DB.  Read-write for the write
  roles (sub_manage, plan designer), read-only for sub_analysis -- see
  ``tools.base.tool_role``.

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
import re
import sqlite3
import time
from pathlib import Path

import pandas as pd

from ...agent.memory_manager import _ensure_schema
from .base import BaseTool, current_tool_role

logger = logging.getLogger(__name__)

# Maximum rows the agent can receive in a single query
_MAX_ROWS = 50

# SQLite authorizer action codes (https://sqlite.org/c3ref/c_alter_table.html).
# Spelled out numerically instead of via ``sqlite3.SQLITE_*``: those module
# constants only exist on Python >= 3.11, and CI also runs 3.10.  Code 33 is
# SQLITE_RECURSIVE (WITH RECURSIVE), *not* VACUUM -- the previous deny-list
# blocked recursive CTEs with a misleading "security violation".
_A_CREATE_INDEX = 1
_A_CREATE_TABLE = 2
_A_DELETE = 9
_A_DROP_TABLE = 11
_A_INSERT = 18
_A_PRAGMA = 19
_A_READ = 20
_A_SELECT = 21
_A_TRANSACTION = 22
_A_UPDATE = 23
_A_ATTACH = 24
_A_DETACH = 25
_A_ALTER_TABLE = 26
_A_FUNCTION = 31
_A_SAVEPOINT = 32
_A_RECURSIVE = 33

# Actions a pure reader may perform. Everything else (writes, DDL, ATTACH,
# DETACH, PRAGMA ...) is denied -- an allow-list can't miss a newly added code.
_READ_ACTIONS = frozenset({
    _A_READ, _A_SELECT, _A_FUNCTION, _A_RECURSIVE, _A_TRANSACTION, _A_SAVEPOINT,
})

# PRAGMAs that only report schema information; safe for every role.
_SAFE_READ_PRAGMAS = frozenset({
    "table_info", "table_xinfo", "table_list", "index_list", "index_info",
    "index_xinfo", "foreign_key_list",
})

# Tables the framework itself depends on -- never droppable / alterable by the
# agent even in a write role.
_CORE_TABLES = frozenset({
    "reports", "activity_log", "scheduled_tasks", "personalised_pages",
    "trigger_rules", "message_evidence", "chat_history", "onboarding_survey",
})

# Other DDL actions (CREATE/DROP of TEMP objects, triggers, views, vtables):
# they also precede the sqlite_master update that belongs to their statement.
_OTHER_DDL_ACTIONS = frozenset({3, 4, 5, 6, 7, 8, 10, 12, 13, 14, 15, 16, 17, 29, 30})

# Roles that may write to the memory DB (see ``tools.base.tool_role``).
# ``None`` = no role set (direct programmatic use); every agent loop sets one.
_MEMORY_WRITE_ROLES = frozenset({None, "plan", "manage"})

_DDL_KEYWORDS = frozenset({"CREATE", "DROP", "ALTER"})

_LEADING_NOISE_RE = re.compile(r"^(?:\s+|--[^\n]*(?:\n|$)|/\*.*?\*/)*", re.DOTALL)


def _first_keyword(sql: str) -> str:
    """First SQL keyword of *sql*, upper-cased, skipping comments/whitespace."""
    rest = sql[_LEADING_NOISE_RE.match(sql).end():]
    m = re.match(r"[A-Za-z]+", rest)
    return m.group(0).upper() if m else ""


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
    _MEMORY_TABLES = {
        "reports", "activity_log", "scheduled_tasks", "personalised_pages",
        "trigger_rules", "chat_history", "message_evidence", "onboarding_survey",
    }

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
            writable = current_tool_role() in _MEMORY_WRITE_ROLES
            coro = asyncio.to_thread(self._query_memory, sql, actual_limit, writable)
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
        """SQLite3 authorizer that only lets pure reads through.

        Runs at the driver level -- before query parsing -- so it cannot be
        circumvented by SQL injection or multi-statement tricks.
        """
        if action in _READ_ACTIONS:
            return sqlite3.SQLITE_OK
        if action == _A_PRAGMA and str(arg1 or "").lower() in _SAFE_READ_PRAGMAS:
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    @staticmethod
    def _memory_writer_authorizer_factory(ddl_statement: bool = False):
        """Build an authorizer for write roles on the memory DB.

        Ordinary reads/DML/DDL are fine, but even a writer may never attach
        another database file, change PRAGMAs, load extensions, touch SQLite's
        own catalogue (``sqlite_master`` ...) or drop/alter a framework table.

        SQLite reports the *internal* catalogue update of a ``CREATE TABLE`` as
        an INSERT on ``sqlite_master`` (before the CREATE action itself), so
        catalogue writes are only tolerated when the statement is DDL
        (``ddl_statement``); a bare ``INSERT INTO sqlite_master`` is denied.
        """
        ddl_actions = {_A_CREATE_TABLE, _A_CREATE_INDEX, _A_DROP_TABLE, _A_ALTER_TABLE}

        def _authorize(action: int, arg1, arg2, db_name, trigger_name) -> int:
            if action in (_A_ATTACH, _A_DETACH):
                return sqlite3.SQLITE_DENY
            if action == _A_PRAGMA:
                ok = str(arg1 or "").lower() in _SAFE_READ_PRAGMAS
                return sqlite3.SQLITE_OK if ok else sqlite3.SQLITE_DENY
            if action == _A_FUNCTION and str(arg2 or "").lower() == "load_extension":
                return sqlite3.SQLITE_DENY
            if action in ddl_actions or action in _OTHER_DDL_ACTIONS:
                table = str((arg2 if action == _A_CREATE_INDEX else arg1) or "").lower()
                if table.startswith("sqlite_"):
                    return sqlite3.SQLITE_DENY
                if action in (_A_DROP_TABLE, _A_ALTER_TABLE) and table in _CORE_TABLES:
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK
            if action in (_A_INSERT, _A_UPDATE, _A_DELETE):
                table = str(arg1 or "").lower()
                if table.startswith("sqlite_") and not (
                    ddl_statement and not trigger_name and "master" in table
                ):
                    return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        return _authorize

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
    # Memory (read-only for analysis roles, read-write for write roles)
    # ------------------------------------------------------------------

    def _query_memory(self, sql: str, limit: int, writable: bool = True) -> dict:
        """Synchronous query against the agent memory SQLite DB.

        ``writable=False`` (sub_analysis: cron / trigger / quick / chat
        analyze) opens the file in SQLite read-only URI mode *and* installs
        the read-only authorizer, so a read-only role cannot mutate memory no
        matter how the statement is phrased.
        """
        keyword = _first_keyword(sql)
        is_read = keyword in ("SELECT", "WITH", "PRAGMA")
        if not writable and not is_read:
            return {
                "success": False,
                "error": (
                    "The memory database is READ-ONLY in this role. Only SELECT "
                    "(or WITH) queries are allowed; persistent changes go through "
                    "the chat manage() path."
                ),
            }
        if keyword in ("VACUUM", "ATTACH", "DETACH"):
            # VACUUM [INTO] never reaches the authorizer, so gate it here.
            return {"success": False, "error": f"{keyword} is not allowed on the memory database."}
        conn = None
        try:
            if writable:
                conn = sqlite3.connect(self.memory_db_file, timeout=30)
                _ensure_schema(conn)
                conn.set_authorizer(self._memory_writer_authorizer_factory(keyword in _DDL_KEYWORDS))
            else:
                uri = f"file:{self.memory_db_file}?mode=ro"
                conn = sqlite3.connect(uri, uri=True, timeout=30)
                conn.set_authorizer(self._readonly_authorizer)
            if is_read:
                df = pd.read_sql(sql, conn)
                return {"success": True, **_df_to_compact(df, limit)}
            with conn:
                cur = conn.execute(sql)
            return {
                "success":       True,
                "rows_affected": cur.rowcount,
                "message":       "Query executed successfully.",
            }
        except Exception as exc:
            err = str(exc)
            if "not authorized" in err.lower():
                err = (
                    "Not allowed on the memory database (ATTACH / DETACH / PRAGMA "
                    "writes / SQLite internals / dropping framework tables are blocked)."
                    if writable else
                    "The memory database is READ-ONLY in this role."
                )
            return {"success": False, "error": err}
        finally:
            if conn is not None:
                conn.close()
