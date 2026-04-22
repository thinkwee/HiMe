"""
Data retention enforcement for HIME.

HIME keeps a strict rolling window of health and agent-memory data. This
module implements a daily sweep that deletes rows older than
``settings.DATA_RETENTION_DAYS`` across every relevant SQLite database:

* Per-user health stores  (``data/data_stores/*_data.db``)    → ``samples``
* Raw watch ingestion log (``ios/Server/watch.db``)           → ``health_samples_eav``
* Per-user memory DBs     (``memory/*.db``)                   → ``reports``,
                                                                 ``message_evidence``,
                                                                 ``activity_log``

The loop follows the HIME "robustness with fallbacks" principle: a failure
pruning one table or one DB must never abort the rest of the sweep. Each
DB is processed in its own thread (sqlite3 is blocking) and each table is
guarded by an independent try/except. Config is re-read on every pass so
runtime edits to ``DATA_RETENTION_DAYS`` are picked up without a restart.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from backend.config import settings

logger = logging.getLogger(__name__)

# Sleep between sweeps (24h). Kept as a module-level constant so tests can
# monkey-patch it to a short value without touching asyncio internals.
RETENTION_SWEEP_INTERVAL_SECONDS: int = 24 * 60 * 60

# Per-memory-DB tables with ISO-8601 ``created_at`` columns.
_MEMORY_TABLES: tuple[str, ...] = ("reports", "message_evidence", "activity_log")


def _repo_root() -> Path:
    """Return the HIME repository root (parent of ``backend/``)."""
    return Path(__file__).resolve().parents[2]


def _cutoffs(retention_days: int) -> tuple[str, float]:
    """
    Return the cutoff boundary in both representations used on disk:

    * ISO-8601 seconds-precision string (matches ``samples.timestamp``,
      ``samples.updated_at`` and every ``created_at`` column in the memory
      DB — they all store ``strftime('%Y-%m-%dT%H:%M:%S', 'now')``).
    * Epoch seconds float (matches ``health_samples_eav.ts`` in
      ``watch.db``, which stores ``datetime.timestamp()``).
    """
    cutoff_dt = datetime.now(timezone.utc) - timedelta(days=retention_days)
    iso = cutoff_dt.strftime("%Y-%m-%dT%H:%M:%S")
    epoch = cutoff_dt.timestamp()
    return iso, epoch


def _prune_one_db(
    db_file: Path,
    deletes: list[tuple[str, str, Any]],
) -> dict[str, int]:
    """
    Apply a list of ``(table, column, cutoff)`` deletes to a single SQLite DB.

    Runs synchronously — call via ``asyncio.to_thread`` from async code.
    Each delete is guarded independently; a failure on one table does not
    prevent the rest from running. ``VACUUM`` is run at the end iff at
    least one row was deleted across the whole DB.

    Returns a mapping of ``f"{db_name}:{table}" -> rows_deleted``. Tables
    that do not exist in the DB contribute ``0`` silently.
    """
    results: dict[str, int] = {}
    if not db_file.exists():
        return results

    total_deleted = 0
    try:
        # isolation_level=None → autocommit so each DELETE is durable
        # independently and a later failure doesn't roll back earlier ones.
        with sqlite3.connect(str(db_file), timeout=30, isolation_level=None) as conn:
            for table, column, cutoff in deletes:
                key = f"{db_file.name}:{table}"
                try:
                    # Skip tables that don't exist in this DB (e.g. a
                    # legacy memory DB missing message_evidence).
                    exists = conn.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (table,),
                    ).fetchone()
                    if not exists:
                        results[key] = 0
                        continue
                    cur = conn.execute(
                        f"DELETE FROM {table} WHERE {column} < ?",
                        (cutoff,),
                    )
                    deleted = cur.rowcount if cur.rowcount is not None else 0
                    results[key] = max(deleted, 0)
                    total_deleted += results[key]
                except sqlite3.Error as e:
                    logger.warning(
                        "retention: DELETE failed for %s.%s: %s",
                        db_file.name, table, e,
                    )
                    results[key] = 0

            if total_deleted > 0:
                try:
                    conn.execute("VACUUM")
                except sqlite3.Error as e:
                    logger.warning(
                        "retention: VACUUM failed for %s: %s", db_file.name, e,
                    )
    except sqlite3.Error as e:
        logger.error("retention: could not open %s: %s", db_file, e)

    return results


async def _prune_health_stores(cutoff_iso: str) -> dict[str, int]:
    """Prune every ``data/data_stores/*_data.db`` down to the cutoff."""
    results: dict[str, int] = {}
    store_dir = Path(settings.DATA_STORE_PATH)
    if not store_dir.exists():
        return results
    for db_file in sorted(store_dir.glob("*_data.db")):
        try:
            part = await asyncio.to_thread(
                _prune_one_db,
                db_file,
                [("samples", "timestamp", cutoff_iso)],
            )
            results.update(part)
        except Exception as e:  # pragma: no cover — defensive
            logger.error("retention: health store %s failed: %s", db_file, e)
    return results


async def _prune_watch_db(cutoff_epoch: float) -> dict[str, int]:
    """Prune the raw watch ingestion DB (``ios/Server/watch.db``)."""
    watch_db = _repo_root() / "ios" / "Server" / "watch.db"
    if not watch_db.exists():
        return {}
    try:
        return await asyncio.to_thread(
            _prune_one_db,
            watch_db,
            [("health_samples_eav", "ts", cutoff_epoch)],
        )
    except Exception as e:  # pragma: no cover — defensive
        logger.error("retention: watch.db prune failed: %s", e)
        return {}


async def _prune_memory_dbs(cutoff_iso: str) -> dict[str, int]:
    """Prune every memory DB (``memory/*.db``) across our target tables."""
    results: dict[str, int] = {}
    memory_dir = Path(settings.MEMORY_DB_PATH)
    if not memory_dir.exists():
        return results
    deletes = [(tbl, "created_at", cutoff_iso) for tbl in _MEMORY_TABLES]
    for db_file in sorted(memory_dir.glob("*.db")):
        try:
            part = await asyncio.to_thread(_prune_one_db, db_file, deletes)
            results.update(part)
        except Exception as e:  # pragma: no cover — defensive
            logger.error("retention: memory DB %s failed: %s", db_file, e)
    return results


async def prune_expired_data(retention_days: int) -> dict[str, int]:
    """
    Run one retention sweep across every relevant SQLite database.

    Args:
        retention_days: number of days to keep. Rows older than
            ``now - retention_days`` are deleted. Values <= 0 are treated
            as a no-op (no pruning) to avoid accidentally wiping the DB.

    Returns:
        A mapping of ``"{db_filename}:{table}" -> rows_deleted``. One key
        per (db, table) pair that was attempted. Absent keys mean the
        table or DB was not present on disk.
    """
    if retention_days is None or retention_days <= 0:
        logger.info("retention: disabled (retention_days=%s)", retention_days)
        return {}

    cutoff_iso, cutoff_epoch = _cutoffs(retention_days)
    logger.info(
        "retention: pruning rows older than %s (retention_days=%d)",
        cutoff_iso, retention_days,
    )

    results: dict[str, int] = {}

    # Three independent try blocks so one failure doesn't abort the rest.
    try:
        results.update(await _prune_health_stores(cutoff_iso))
    except Exception as e:  # pragma: no cover — defensive
        logger.error("retention: health-store sweep failed: %s", e, exc_info=True)

    try:
        results.update(await _prune_watch_db(cutoff_epoch))
    except Exception as e:  # pragma: no cover — defensive
        logger.error("retention: watch.db sweep failed: %s", e, exc_info=True)

    try:
        results.update(await _prune_memory_dbs(cutoff_iso))
    except Exception as e:  # pragma: no cover — defensive
        logger.error("retention: memory sweep failed: %s", e, exc_info=True)

    total = sum(results.values())
    logger.info(
        "retention: sweep complete — %d row(s) deleted across %d table(s)",
        total, len(results),
    )
    return results


async def retention_loop(app_state: Any | None = None) -> None:
    """
    Long-running daily retention task.

    Runs one sweep immediately on startup (so a fresh install with
    pre-seeded test data is trimmed straight away), then sleeps for
    ``RETENTION_SWEEP_INTERVAL_SECONDS`` (24h) and repeats. The retention
    window is re-read from ``settings`` on every iteration so runtime
    changes take effect on the next tick.

    ``app_state`` is accepted for future extensibility (e.g. pausing the
    loop during maintenance mode) but is currently unused.
    """
    logger.info(
        "retention loop starting (interval=%ds, days=%d)",
        RETENTION_SWEEP_INTERVAL_SECONDS, settings.DATA_RETENTION_DAYS,
    )
    try:
        while True:
            try:
                await prune_expired_data(settings.DATA_RETENTION_DAYS)
            except Exception as e:
                # Never let a sweep failure kill the loop.
                logger.error("retention: sweep crashed: %s", e, exc_info=True)
            try:
                await asyncio.sleep(RETENTION_SWEEP_INTERVAL_SECONDS)
            except asyncio.CancelledError:
                raise
    except asyncio.CancelledError:
        logger.info("retention loop cancelled — exiting cleanly")
        raise
