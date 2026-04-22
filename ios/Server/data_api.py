"""
WatchExporter — read-only query API for the LLM agent platform.
Updated to support EAV schema (health_samples_eav).

Usage:
    from data_api import get_latest, get_recent, get_summary, get_range, to_llm_context

DB path resolution (in priority order):
    1. WATCH_DB environment variable
    2. watch.db next to this file (same directory as server.py)
"""

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional

_DEFAULT_DB = Path(__file__).parent / "watch.db"

# Legacy hardcoded list kept as fallback only
_LEGACY_METRICS = ("hr", "hrv", "spo2", "cal", "steps", "rr")


def _get_available_metrics() -> tuple:
    """
    Dynamically discover all feature names from the EAV table.
    Falls back to legacy hardcoded list if the DB or table doesn't exist yet.
    """
    try:
        con = _con()
        rows = con.execute(
            "SELECT DISTINCT f FROM health_samples_eav ORDER BY f"
        ).fetchall()
        con.close()
        if rows:
            return tuple(r[0] for r in rows)
    except Exception:
        pass
    return _LEGACY_METRICS


# Will be populated lazily on first use
_metrics_cache: Optional[tuple] = None
_metrics_cache_ts: float = 0.0
_METRICS_TTL: float = 60.0  # Re-discover metrics every 60 seconds


def _get_metrics() -> tuple:
    """Return cached metrics, refreshing every _METRICS_TTL seconds."""
    global _metrics_cache, _metrics_cache_ts
    import time
    now = time.time()
    if _metrics_cache is None or (now - _metrics_cache_ts) > _METRICS_TTL:
        _metrics_cache = _get_available_metrics()
        _metrics_cache_ts = now
    return _metrics_cache


def _con() -> sqlite3.Connection:
    path = os.environ.get("WATCH_DB", str(_DEFAULT_DB))
    return sqlite3.connect(path)


def _pivot_rows(rows: List[tuple]) -> List[Dict[str, Any]]:
    """
    Pivot rows from health_samples_eav(ts, f, v) into wide dicts.
    Input rows: [(ts, f, v), ...] ordered by ts.
    Accepts ALL feature names found in the data (no hardcoded filter).
    """
    result = []
    current_ts = None
    current_row: Dict[str, Any] = {}

    for ts, f, v in rows:
        if current_ts is None or ts != current_ts:
            if current_row:
                result.append(current_row)
            current_ts = ts
            current_row = {"ts": ts}

        # Accept every feature — no hardcoded allowlist
        current_row[f] = v

    if current_row:
        result.append(current_row)

    return result


def get_latest() -> Dict[str, Any]:
    """
    Most recent value of each metric, queried independently from EAV table.
    Dynamically discovers all available features.
    Returns (example):
        {
          "heart_rate":    {"ts": 1709500042.3, "value": 72.0},
          "steps":         {"ts": 1709500010.1, "value": 1234},
          ...
        }
    """
    con = _con()
    result = {}
    for metric in _get_metrics():
        row = con.execute(
            "SELECT ts, v FROM health_samples_eav "
            "WHERE f = ? ORDER BY ts DESC LIMIT 1",
            (metric,)
        ).fetchone()
        if row:
            result[metric] = {"ts": row[0], "value": row[1]}
    con.close()
    return result


def get_recent(minutes: int = 10) -> List[Dict[str, Any]]:
    """All samples from the last N minutes, oldest-first, pivoted."""
    since = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).timestamp()
    cur = _con().execute(
        "SELECT ts, f, v FROM health_samples_eav WHERE ts > ? ORDER BY ts ASC, f ASC",
        (since,)
    )
    return _pivot_rows(cur.fetchall())


def get_range(start: datetime, end: datetime) -> List[Dict[str, Any]]:
    """All samples between two datetimes, oldest-first, pivoted."""
    cur = _con().execute(
        "SELECT ts, f, v FROM health_samples_eav "
        "WHERE ts BETWEEN ? AND ? ORDER BY ts ASC, f ASC",
        (start.timestamp(), end.timestamp())
    )
    return _pivot_rows(cur.fetchall())


def get_summary(minutes: int = 60) -> Dict[str, Any]:
    """
    Aggregate statistics for the last N minutes from EAV table.
    Dynamically discovers all available features.
    """
    since = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).timestamp()
    con = _con()

    # Get total unique timestamps as a proxy for sample count
    count_row = con.execute(
        "SELECT COUNT(DISTINCT ts) FROM health_samples_eav WHERE ts > ?",
        (since,)
    ).fetchone()

    summary: Dict[str, Any] = {
        "period_minutes": minutes,
        "sample_count": count_row[0] if count_row else 0
    }

    for metric in _get_metrics():
        row = con.execute(
            "SELECT AVG(v), MIN(v), MAX(v) FROM health_samples_eav "
            "WHERE f = ? AND ts > ?",
            (metric, since)
        ).fetchone()

        if row and row[0] is not None:
            summary[metric] = {"avg": round(row[0], 2), "min": row[1], "max": row[2]}
        else:
            summary[metric] = {"avg": None, "min": None, "max": None}

    con.close()
    return summary


def to_llm_context(data: Any) -> str:
    """Serialize data to compact JSON for LLM context."""
    return json.dumps(data, ensure_ascii=False, default=str)
