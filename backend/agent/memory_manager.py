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

        CREATE TABLE IF NOT EXISTS chat_history (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at    TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
            history_key   TEXT NOT NULL,
            role          TEXT NOT NULL,
            content       TEXT NOT NULL,
            message_hash  TEXT,
            client_msg_id TEXT,
            report_id     INTEGER,
            image_id      TEXT
        );

        CREATE TABLE IF NOT EXISTS onboarding_survey (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at   TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
            goals        TEXT,
            answers      TEXT,
            plan_status  TEXT DEFAULT 'pending'
        );

        CREATE INDEX IF NOT EXISTS idx_activity_log_id
            ON activity_log(id);
        CREATE INDEX IF NOT EXISTS idx_message_evidence_hash
            ON message_evidence(message_hash);
        CREATE INDEX IF NOT EXISTS idx_chat_history_key
            ON chat_history(history_key, id);
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

    # Migrate: add 'report_id' column to chat_history tables that predate it,
    # so a proactive report turn can deep-link to its full report in the app.
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(chat_history)").fetchall()}
        if "report_id" not in cols:
            conn.execute("ALTER TABLE chat_history ADD COLUMN report_id INTEGER")
            conn.commit()
    except Exception:
        pass  # table may not exist yet (handled by CREATE TABLE above)

    # Migrate: add 'image_id' column so an agent-sent chart (the iOS in-app
    # `chat_image` event) is persisted on its assistant turn and can be
    # replayed by GET /chat-history — not just delivered live over the WS.
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(chat_history)").fetchall()}
        if "image_id" not in cols:
            conn.execute("ALTER TABLE chat_history ADD COLUMN image_id TEXT")
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

    def delete_report(self, report_id: int) -> bool:
        """Delete a single report by id. Returns True if a row was removed."""
        with sqlite3.connect(self.db_file, timeout=30) as conn:
            cursor = conn.execute("DELETE FROM reports WHERE id = ?", (report_id,))
            conn.commit()
            return cursor.rowcount > 0

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
    # Chat history (in-app conversation transcript)
    # ------------------------------------------------------------------

    CHAT_HISTORY_LIMIT = 2000  # rows retained per history_key

    def _chat_write_lock(self) -> asyncio.Lock:
        """Lazily-created, per-instance lock that serializes chat-history
        writes. Two turns (or the user+assistant rows of one turn) must never
        hit the SQLite file concurrently: parallel writers race on the
        autoincrement ``id`` (reordering the conversation) and can collide on
        the file lock (dropping a row). ``asyncio.Lock`` wakes waiters FIFO, so
        the first write scheduled is the first committed."""
        lock = getattr(self, "_chat_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            self._chat_lock = lock
        return lock

    async def persist_chat_turn(
        self,
        history_key: str,
        role: str,
        content: str,
        message_hash: str | None = None,
        client_msg_id: str | None = None,
        report_id: int | None = None,
        image_id: str | None = None,
    ) -> None:
        """Persist one chat turn to the memory DB (async)."""
        await self.persist_chat_turns(history_key, [{
            "role": role, "content": content,
            "message_hash": message_hash, "client_msg_id": client_msg_id,
            "report_id": report_id, "image_id": image_id,
        }])

    async def persist_chat_turns(
        self,
        history_key: str,
        turns: list[dict[str, Any]],
    ) -> None:
        """Persist an ordered list of chat turns in ONE transaction.

        Each turn is a dict with ``role``/``content`` (required) and optional
        ``message_hash``/``client_msg_id``/``report_id``/``image_id``. Writing the whole turn
        atomically, under the per-instance write lock, guarantees the rows keep
        their given order and that no concurrent writer can interleave or drop
        them."""
        if not turns:
            return
        async with self._chat_write_lock():
            await asyncio.to_thread(self._persist_chat_turns_sync, history_key, turns)

    def _persist_chat_turns_sync(
        self,
        history_key: str,
        turns: list[dict[str, Any]],
    ) -> None:
        try:
            with sqlite3.connect(self.db_file, timeout=30) as conn:
                _ensure_schema(conn)
                conn.executemany(
                    "INSERT INTO chat_history "
                    "(history_key, role, content, message_hash, client_msg_id, report_id, image_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [
                        (
                            history_key, t["role"], t["content"],
                            t.get("message_hash"), t.get("client_msg_id"),
                            t.get("report_id"), t.get("image_id"),
                        )
                        for t in turns
                    ],
                )
                # Bound table size per conversation.
                count = conn.execute(
                    "SELECT COUNT(*) FROM chat_history WHERE history_key = ?",
                    (history_key,),
                ).fetchone()[0]
                if count > self.CHAT_HISTORY_LIMIT:
                    excess = count - self.CHAT_HISTORY_LIMIT
                    conn.execute(
                        "DELETE FROM chat_history WHERE id IN ("
                        "  SELECT id FROM chat_history WHERE history_key = ? "
                        "  ORDER BY id LIMIT ?)",
                        (history_key, excess),
                    )
                conn.commit()
        except Exception as exc:
            logger.warning("chat_history persist failed: %s", exc)

    def get_chat_history(self, history_key: str, limit: int = 200) -> list[dict[str, Any]]:
        """Return the most recent *limit* chat turns for *history_key*, oldest-first."""
        with sqlite3.connect(self.db_file, timeout=30) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT * FROM (
                       SELECT id, created_at, role, content, message_hash,
                              client_msg_id, report_id, image_id
                       FROM chat_history WHERE history_key = ?
                       ORDER BY id DESC LIMIT ?
                   ) ORDER BY id ASC""",
                (history_key, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def recent_user_text(self, limit: int = 12) -> str:
        """Concatenated text of the most recent *user* chat turns across ALL
        channels — used to infer which language to write proactive reports in.
        Returns "" when there's no chat history yet."""
        try:
            with sqlite3.connect(self.db_file, timeout=10) as conn:
                rows = conn.execute(
                    "SELECT content FROM chat_history WHERE role = 'user' "
                    "ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return "\n".join(str(r[0]) for r in rows if r and r[0])
        except Exception as exc:
            logger.debug("recent_user_text failed: %s", exc)
            return ""

    async def clear_chat_history(self, history_key: str) -> int:
        """Delete all chat turns for *history_key*. Returns rows removed."""
        return await asyncio.to_thread(self._clear_chat_history_sync, history_key)

    def _clear_chat_history_sync(self, history_key: str) -> int:
        try:
            with sqlite3.connect(self.db_file, timeout=30) as conn:
                cur = conn.execute(
                    "DELETE FROM chat_history WHERE history_key = ?", (history_key,)
                )
                conn.commit()
                return cur.rowcount
        except Exception as exc:
            logger.warning("clear_chat_history failed: %s", exc)
            return 0

    # ------------------------------------------------------------------
    # Onboarding goal survey (no LLM at capture time — recorded verbatim,
    # acted on later by the deferred plan-designer run)
    # ------------------------------------------------------------------

    def save_onboarding_survey(
        self, goals: list[str], answers: dict[str, Any] | None = None
    ) -> None:
        """Record the user's onboarding health-goal survey. Marks any prior
        survey 'superseded' so only the newest one is ever 'pending' — a user
        who re-runs onboarding gets one fresh plan, not a backlog."""
        with sqlite3.connect(self.db_file, timeout=30) as conn:
            _ensure_schema(conn)
            conn.execute(
                "UPDATE onboarding_survey SET plan_status = 'superseded' "
                "WHERE plan_status = 'pending'"
            )
            conn.execute(
                "INSERT INTO onboarding_survey (goals, answers, plan_status) "
                "VALUES (?, ?, 'pending')",
                (json.dumps(goals or []), json.dumps(answers or {})),
            )
            conn.commit()

    def get_pending_survey(self) -> dict[str, Any] | None:
        """Return the newest survey awaiting a plan, or None. Used by the
        first-message hook to decide whether to fire the plan designer."""
        try:
            with sqlite3.connect(self.db_file, timeout=10) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT * FROM onboarding_survey WHERE plan_status = 'pending' "
                    "ORDER BY id DESC LIMIT 1"
                ).fetchone()
        except Exception as exc:
            logger.debug("get_pending_survey failed: %s", exc)
            return None
        if not row:
            return None
        d = dict(row)
        for col in ("goals", "answers"):
            if d.get(col):
                try:
                    d[col] = json.loads(d[col])
                except json.JSONDecodeError:
                    pass
        return d

    def mark_survey_planned(self, survey_id: int) -> None:
        """Flip a survey from 'pending' to 'done' once its plan has been built,
        so the plan designer fires exactly once per survey."""
        try:
            with sqlite3.connect(self.db_file, timeout=30) as conn:
                conn.execute(
                    "UPDATE onboarding_survey SET plan_status = 'done' WHERE id = ?",
                    (survey_id,),
                )
                conn.commit()
        except Exception as exc:
            logger.warning("mark_survey_planned failed: %s", exc)

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
