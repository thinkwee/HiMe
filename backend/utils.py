"""
Utility functions for safe JSON serialization of DataFrames and timestamp formatting.
"""
import math
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Canonical timestamp helpers — YYYY-MM-DDTHH:MM:SS (seconds, UTC, no tz suffix)
# No +00:00 suffix: avoids pandas creating tz-aware columns which break
# naive Timestamp comparisons in agent-generated code.
# ---------------------------------------------------------------------------

_TS_FMT = '%Y-%m-%dT%H:%M:%S'


def ts_now() -> str:
    """Return current UTC time as ISO-8601 string (seconds precision)."""
    return datetime.now(timezone.utc).strftime(_TS_FMT)


def ts_fmt(dt: datetime) -> str:
    """Format a datetime as ISO-8601 string (seconds precision, UTC)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime(_TS_FMT)


def parse_db_iso_utc(s: str | None) -> datetime | None:
    """
    Parse an ISO timestamp from the SQLite memory DB into a tz-aware UTC datetime.

    DB columns (`created_at`, `last_run_at`, ...) are written with SQLite's
    `strftime('%Y-%m-%dT%H:%M:%S','now')` — UTC, no offset suffix. We always
    re-attach UTC so downstream comparisons / cron walks are unambiguous.
    """
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Application timezone — single source of truth for cron scheduling and
# user-facing wall-clock formatting. Configured via `settings.TIMEZONE`
# (any IANA name, e.g. UTC, Europe/London, Asia/Shanghai).
# ---------------------------------------------------------------------------


def app_timezone() -> ZoneInfo:
    """
    Return the configured application timezone as a ZoneInfo.

    Falls back to UTC if `settings.TIMEZONE` is unset or unknown. The
    fallback is silent here; startup calls `settings.validate_timezone()`
    once which logs a warning so operators see the misconfiguration.
    """
    from .config import settings  # lazy import: utils <- config <- ... cycle-free
    tz_name = (settings.TIMEZONE or "UTC").strip() or "UTC"
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def now_utc() -> datetime:
    """Current time as a tz-aware UTC datetime."""
    return datetime.now(timezone.utc)


def now_local() -> datetime:
    """Current time as a tz-aware datetime in `settings.TIMEZONE`."""
    return datetime.now(app_timezone())


def serialize_value(value: Any) -> Any:
    """
    Serialize a single value to a JSON-safe scalar / list.

    Order matters: ``np.ndarray`` and datetime types are checked before
    ``pd.isna``, because ``pd.isna`` returns an array (not a bool) for
    array-like inputs and would raise inside the truthy check.
    """
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        # pd.Timestamp() normalises both pd.Timestamp (any precision) and
        # np.datetime64 (any precision) into a single Timestamp, then
        # ts_fmt truncates to seconds — no reliance on str() formatting.
        return ts_fmt(pd.Timestamp(value).to_pydatetime())
    if isinstance(value, (np.integer, np.floating)):
        value = value.item()  # collapse to plain Python int/float
    if isinstance(value, float) and not math.isfinite(value):
        return None
    try:
        # pd.isna handles None, NaN, NaT, pd.NA. May raise on exotic objects.
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def clean_dict_for_json(data: dict) -> dict:
    """Recursively clean a dictionary for JSON serialization."""
    result = {}
    for key, value in data.items():
        if isinstance(value, dict):
            result[key] = clean_dict_for_json(value)
        elif isinstance(value, list):
            result[key] = [serialize_value(v) for v in value]
        else:
            result[key] = serialize_value(value)
    return result


def dataframe_to_json_safe(df: pd.DataFrame) -> list[dict[str, Any]]:
    """
    Safely convert DataFrame to JSON-serializable list of dictionaries.

    Datetime columns are formatted column-wise (vectorised, faster than
    per-cell), then ``clean_dict_for_json`` handles NaN/Inf/pd.NA/Timestamp
    leftovers in object columns — same code path as ``serialize_value`` so
    bug fixes in one place flow to the other.
    """
    if df.empty:
        return []

    df = df.copy()
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            df[col] = df[col].apply(lambda x: ts_fmt(x.to_pydatetime()) if pd.notna(x) else None)

    return [clean_dict_for_json(row) for row in df.to_dict(orient='records')]
