"""
Utility functions for safe JSON serialization of DataFrames and timestamp formatting.
"""
import json
import logging
import math
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Canonical timestamp helpers — YYYY-MM-DDTHH:MM:SS (seconds, UTC, no tz suffix)
# No +00:00 suffix: avoids pandas creating tz-aware columns which break
# naive Timestamp comparisons in agent-generated code.
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

_TS_FMT = '%Y-%m-%dT%H:%M:%S'


def atomic_write_json(path: "str | os.PathLike[str]", data: Any, indent: int | None = 2) -> None:
    """Write *data* as JSON to *path* atomically (temp file + ``os.replace``).

    A crash or concurrent reader never observes a half-written file. Blocking —
    call via ``asyncio.to_thread`` from async code.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=indent)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


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


_warned_bad_tz: set[str] = set()


def app_timezone() -> ZoneInfo:
    """
    Return the configured application timezone as a ZoneInfo.

    An unset ``settings.TIMEZONE`` means UTC. An invalid one (unknown name,
    malformed key such as an absolute path, ...) logs a one-time warning and
    falls back to UTC, so the cron scheduler and prompt builders never crash
    on a typo in ``.env``.
    """
    from .config import settings  # lazy import: utils <- config <- ... cycle-free
    tz_name = (settings.TIMEZONE or "UTC").strip() or "UTC"
    try:
        return ZoneInfo(tz_name)
    except Exception as exc:  # ZoneInfoNotFoundError, ValueError, OSError, ...
        if tz_name not in _warned_bad_tz:
            _warned_bad_tz.add(tz_name)
            logger.warning(
                "TIMEZONE=%r is invalid (%s); falling back to UTC.",
                tz_name, exc,
            )
        return ZoneInfo("UTC")


def now_utc() -> datetime:
    """Current time as a tz-aware UTC datetime."""
    return datetime.now(timezone.utc)


def now_local() -> datetime:
    """Current time as a tz-aware datetime in `settings.TIMEZONE`."""
    return datetime.now(app_timezone())


def local_tz_line() -> str:
    """Render the current local-TZ context line for LLM prompts.

    Format: ``Local TZ: <IANA name> (UTC±HH:MM, <abbrev>)``. The abbrev/offset
    come from the live local time so DST is reflected automatically. Used to
    give the LLM a concrete, current offset on every chat turn — UTC is what
    the DB stores, this is what the LLM should quote to the user.
    """
    from .config import settings
    tz = app_timezone()
    now = datetime.now(tz)
    raw = now.strftime("%z")  # e.g. "+0100"
    offset = f"{raw[:3]}:{raw[3:]}" if raw else "+00:00"
    abbrev = now.strftime("%Z") or tz.key  # e.g. "BST"
    name = (settings.TIMEZONE or "UTC").strip() or "UTC"
    return f"Local TZ: {name} (UTC{offset}, {abbrev})"


def language_directive(sample_text: str, default_lang: str = "en") -> str:
    """One-line instruction telling the model which language to write
    user-facing output in, inferred from a sample of the user's OWN recent
    messages (CJK present → Chinese). Chat turns infer language from the
    incoming message, but proactive reports have no such signal — this gives
    them one. Falls back to *default_lang* when there's no chat history yet.
    """
    text = sample_text or ""
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    if cjk >= 2:
        lang = "Chinese"
    elif any(ch.isascii() and ch.isalpha() for ch in text):
        lang = "English"
    else:
        lang = "Chinese" if (default_lang or "en").lower().startswith("zh") else "English"
    return (
        f"Language: write everything the user will see — the report title, "
        f"content and digest — in {lang}, matching how the user talks to you."
    )


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
