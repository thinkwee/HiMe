"""
Memory manager — lightweight wrapper over the user's memory SQLite database.

Design principle:
  The agent (via SQLTool / CodeTool) is the *architect* of the memory DB; it creates
  whatever tables it needs autonomously.  MemoryManager only owns the mandatory
  ``reports`` and ``activity_log`` tables that backend API endpoints must read.

Deprecation note:
  The old observations / insights / context_summary tables were never written to by
  Agent V2 and have been removed.  Historical DBs that still carry those tables are
  unaffected — SQLite ignores extra tables.
"""
import asyncio
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------

def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the minimal set of tables that the backend API depends on."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS reports (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at      TEXT    DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
            time_range_start TEXT,
            time_range_end   TEXT,
            title           TEXT,
            content         TEXT    NOT NULL,
            alert_level     TEXT    DEFAULT 'normal',
            metadata        TEXT,
            source          TEXT    DEFAULT 'scheduled_analysis'
        );

        CREATE TABLE IF NOT EXISTS activity_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at  TEXT    DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
            event_type  TEXT,
            event_data  TEXT
        );

        CREATE TABLE IF NOT EXISTS scheduled_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cron_expr TEXT NOT NULL,
            prompt_goal TEXT NOT NULL,
            status TEXT DEFAULT 'active',
            last_run_at TEXT,
            created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
        );

        CREATE TABLE IF NOT EXISTS personalised_pages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            page_id TEXT UNIQUE NOT NULL,
            display_name TEXT NOT NULL,
            description TEXT,
            backend_route TEXT NOT NULL,
            frontend_asset TEXT NOT NULL,
            created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
            status TEXT DEFAULT 'active'
        );

        CREATE TABLE IF NOT EXISTS trigger_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            feature_type TEXT NOT NULL,
            condition TEXT NOT NULL,
            threshold REAL NOT NULL,
            window_minutes INTEGER DEFAULT 60,
            cooldown_minutes INTEGER DEFAULT 30,
            prompt_goal TEXT NOT NULL,
            status TEXT DEFAULT 'active',
            last_triggered_at TEXT,
            trigger_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
        );

        CREATE TABLE IF NOT EXISTS message_evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_hash TEXT NOT NULL,
            message_text TEXT NOT NULL,
            tool_calls TEXT NOT NULL,
            verification_status TEXT DEFAULT 'verified',
            verification_detail TEXT,
            created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
        );

        CREATE INDEX IF NOT EXISTS idx_activity_log_id
            ON activity_log(id);
        CREATE INDEX IF NOT EXISTS idx_message_evidence_hash
            ON message_evidence(message_hash);
    """)
    conn.commit()

    # Migrate: add 'source' column to existing reports tables that lack it
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(reports)").fetchall()}
        if "source" not in cols:
            conn.execute("ALTER TABLE reports ADD COLUMN source TEXT DEFAULT 'scheduled_analysis'")
            conn.commit()
    except Exception:
        pass  # table may not exist yet (handled by CREATE TABLE above)


# ---------------------------------------------------------------------------
# MemoryManager
# ---------------------------------------------------------------------------

class MemoryManager:
    """
    Manages the user's memory SQLite database.

    Responsibilities:
      - Ensure the mandatory schema exists on first access.
      - Provide typed read methods for the backend API (reports, activity log).
      - The agent itself performs arbitrary SQL via SQLTool — no write methods
        are needed here for routine operation.
    """

    ACTIVITY_LIMIT = 2000  # Maximum rows retained in activity_log

    def __init__(self, db_path: Path, user_id: str) -> None:
        self.db_path = Path(db_path)
        self.db_path.mkdir(parents=True, exist_ok=True)
        self.user_id = user_id
        self.db_file = self.db_path / f"{user_id}.db"
        self._init_database()
        logger.debug("MemoryManager ready: %s", self.db_file)

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def _init_database(self) -> None:
        with sqlite3.connect(self.db_file, timeout=30) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            _ensure_schema(conn)
            # Normalize created_at timestamps to YYYY-MM-DDTHH:MM:SS
            for table in ("reports", "activity_log", "scheduled_tasks", "personalised_pages"):
                try:
                    conn.execute(
                        f"UPDATE [{table}] SET created_at = substr(created_at, 1, 19) "
                        f"WHERE created_at IS NOT NULL AND length(created_at) > 19"
                    )
                except Exception:
                    pass
            conn.commit()

    # ------------------------------------------------------------------
    # Reports
    # ------------------------------------------------------------------

    def get_recent_reports(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return the most recent *limit* reports, newest first."""
        with sqlite3.connect(self.db_file, timeout=30) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM reports ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            if d.get("metadata"):
                try:
                    d["metadata"] = json.loads(d["metadata"])
                except json.JSONDecodeError:
                    pass
            result.append(d)
        return result

    # ------------------------------------------------------------------
    # Activity log
    # ------------------------------------------------------------------

    def get_recent_activity(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return recent agent activity events, oldest-first."""
        with sqlite3.connect(self.db_file, timeout=30) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT * FROM (
                       SELECT id, created_at, event_type, event_data
                       FROM activity_log
                       ORDER BY id DESC
                       LIMIT ?
                   ) ORDER BY id ASC""",
                (limit,),
            ).fetchall()
        events = []
        for row in rows:  # already in chronological order
            try:
                data = json.loads(row["event_data"]) if row["event_data"] else {}
            except json.JSONDecodeError:
                data = {"raw": row["event_data"]}
            events.append(
                {
                    "created_at": row["created_at"],
                    "type": row["event_type"],
                    "data": data,
                }
            )
        return events

    async def persist_activity(self, event: dict[str, Any]) -> None:
        """
        Persist an agent event to the activity log (async, non-blocking).
        Trims the table to ACTIVITY_LIMIT rows to bound disk usage.
        """
        await asyncio.to_thread(self._persist_activity_sync, event)

    def _persist_activity_sync(self, event: dict[str, Any]) -> None:
        try:
            with sqlite3.connect(self.db_file, timeout=30) as conn:
                _ensure_schema(conn)
                conn.execute(
                    "INSERT INTO activity_log (event_type, event_data) VALUES (?, ?)",
                    (event.get("type", ""), json.dumps(event, default=str)),
                )
                count = conn.execute("SELECT COUNT(*) FROM activity_log").fetchone()[0]
                if count > self.ACTIVITY_LIMIT:
                    excess = count - self.ACTIVITY_LIMIT
                    logger.info(
                        "Trimming %d old activity log entries (limit: %d)",
                        excess,
                        self.ACTIVITY_LIMIT,
                    )
                    conn.execute(
                        """DELETE FROM activity_log
                           WHERE id IN (
                               SELECT id FROM activity_log ORDER BY id LIMIT ?
                           )""",
                        (excess,),
                    )
                conn.commit()
        except Exception as exc:
            logger.warning("activity_log persist failed: %s", exc)

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> dict[str, Any]:
        """Return row counts and date range — used by the /memory API."""
        with sqlite3.connect(self.db_file, timeout=30) as conn:
            # Enumerate all user-visible tables
            tables = [
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                ).fetchall()
            ]
            counts: dict[str, int] = {}
            for tbl in tables:
                try:
                    counts[tbl] = conn.execute(
                        f"SELECT COUNT(*) FROM [{tbl}]"  # noqa: S608
                    ).fetchone()[0]
                except Exception:
                    counts[tbl] = -1

            # Date range from reports (primary) or activity_log (fallback)
            min_date = max_date = None
            for col, tbl in [("created_at", "reports"), ("created_at", "activity_log")]:
                if tbl in tables:
                    try:
                        row = conn.execute(
                            f"SELECT MIN({col}), MAX({col}) FROM [{tbl}]"  # noqa: S608
                        ).fetchone()
                        if row and (row[0] or row[1]):
                            min_date, max_date = row[0], row[1]
                            break
                    except Exception:
                        pass

        return {"table_counts": counts, "date_range": {"min": min_date, "max": max_date}}
