"""Pure functions: open-wearables payloads -> DataStore.ingest_batch() rows.

No I/O here — everything takes plain dicts (already-decoded JSON, whether
they came from the REST client or a webhook body) and returns a list of
``{"date": ..., "feature_type": ..., "value": ..., "metadata": ...}`` dicts
ready for :meth:`backend.agent.data_store.DataStore.ingest_batch`.

Timestamp convention (matched to ``backend/utils.py`` / ``watch_db_reader.py``
/ ``agent_lifecycle._live_ingest_loop``): HiMe stores **naive strings in UTC**,
``YYYY-MM-DDTHH:MM:SS`` (seconds precision, no offset suffix — see
``backend.utils.ts_fmt``). open-wearables timestamps arrive as ISO-8601
strings that are usually offset-aware (``Z`` or ``+HH:MM``); event records
additionally carry a ``zone_offset`` field for display purposes. We always
parse to an aware datetime (falling back to the record's ``zone_offset``,
then UTC, when the timestamp string itself is naive) and convert to UTC
before formatting — mirroring exactly what ``_live_ingest_loop`` does for
Apple Watch samples.

Sleep sessions are anchored at segment/session END time (not start), and a
staged session emits ONLY its stage rows, never the session total alongside
them — see ``map_sleep_session``'s docstring and ``prompts/data_schema.md``.

Daily-total timeseries samples (``is_daily_total: true`` — pre-aggregated
by the provider for additive series like steps/energy/distance) are
deprioritized against granular samples for the same (feature, provider, UTC
day) within one mapped batch — see ``_drop_superseded_daily_totals``. A
surviving daily total is written at a deterministic day-anchor timestamp
with a ``"daily_total": true`` metadata marker so a later poll can find and
delete it once granular data for that day shows up (see
``poller.py`` / ``DataStore.delete_daily_total_anchors``).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from ...data_readers.apple_health_features import FEATURE_SPEC
from ...utils import ts_fmt
from .features import OW_FEATURE_SPEC, get_conversion_factor, resolve_feature_type

logger = logging.getLogger(__name__)

# Providers whose data HiMe's native pipeline already owns. Dropped by
# default so open-wearables never double-counts Apple Watch data that also
# flows in through backend/api/agent_lifecycle.py's live ingest loop.
_NATIVE_PROVIDERS = frozenset({"apple", "apple_health"})

# OW workout `type` substring -> HiMe workout-category slug (matches the
# `workout_<slug>_<metric>` keys in apple_health_features.FEATURE_SPEC).
_WORKOUT_TYPE_ALIASES: tuple[tuple[str, str], ...] = (
    ("run", "running"),
    ("cycl", "cycling"),
    ("bike", "cycling"),
    ("biking", "cycling"),
    ("swim", "swimming"),
    ("walk", "walking"),
    ("hik", "hiking"),
    ("yoga", "yoga"),
    ("strength", "strength"),
    ("weight", "strength"),
    ("hiit", "hiit"),
    ("interval", "hiit"),
    ("elliptical", "elliptical"),
    ("row", "rowing"),
    ("core", "core"),
    ("flexib", "flexibility"),
    ("stretch", "flexibility"),
    ("cooldown", "cooldown"),
    ("cool_down", "cooldown"),
)

# Which `workout_<slug>_<metric>` feature_types actually exist, derived from
# whichever spec (Apple's native one, or our generic "other" bucket) defines
# them — so we never emit a feature_type nothing recognizes.
_KNOWN_WORKOUT_FEATURES = frozenset(FEATURE_SPEC) | frozenset(OW_FEATURE_SPEC)

# OW SleepStageType -> HiMe sleep-stage feature_type. "sleeping" (generic,
# unstaged) and "unknown" contribute to sleep_asleep only, not a specific
# stage (mirrors HiMe's own sleep_stage_coverage concept: unclassified sleep
# still counts toward total sleep time).
_SLEEP_STAGE_FEATURE = {
    "awake": "sleep_awake",
    "light": "sleep_core",
    "deep": "sleep_deep",
    "rem": "sleep_rem",
}


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------

def _parse_zone_offset(zone_offset: str | None) -> timezone | None:
    """Parse a ``+HH:MM`` / ``-HH:MM`` offset string into a ``timezone``."""
    if not zone_offset:
        return None
    s = zone_offset.strip()
    if s in ("Z", "z", "+00:00", "-00:00"):
        return timezone.utc
    try:
        sign = -1 if s[0] == "-" else 1
        s = s[1:] if s[0] in "+-" else s
        parts = s.split(":")
        hours = int(parts[0])
        minutes = int(parts[1]) if len(parts) > 1 else 0
        return timezone(sign * timedelta(hours=hours, minutes=minutes))
    except (ValueError, IndexError):
        return None


def parse_ow_datetime(value: Any, zone_offset: str | None = None) -> datetime | None:
    """Parse an open-wearables timestamp (str or datetime) into an aware UTC datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).strip()
        if not s:
            return None
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            logger.debug("open-wearables: unparseable timestamp %r", value)
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_parse_zone_offset(zone_offset) or timezone.utc)
    return dt.astimezone(timezone.utc)


def to_hime_ts(value: Any, zone_offset: str | None = None) -> str | None:
    """Parse + format an OW timestamp into HiMe's canonical storage string."""
    dt = parse_ow_datetime(value, zone_offset)
    return ts_fmt(dt) if dt is not None else None


# ---------------------------------------------------------------------------
# Provider filtering
# ---------------------------------------------------------------------------

def _provider_allowed(provider: str | None, allowlist: frozenset[str] | None) -> bool:
    p = (provider or "").strip().lower()
    if p in _NATIVE_PROVIDERS:
        return False
    if allowlist:
        return p in allowlist
    return True


def _metadata_json(provider: str | None, device: str | None = None, extra: dict | None = None) -> str:
    meta: dict[str, Any] = {"src": "ow"}
    if provider:
        meta["provider"] = provider
    if device:
        meta["device"] = device
    if extra:
        meta.update({k: v for k, v in extra.items() if v is not None})
    return json.dumps(meta, separators=(",", ":"), default=str)


# ---------------------------------------------------------------------------
# Timeseries samples
# ---------------------------------------------------------------------------

def _map_sample(
    series_type: str | None,
    value: Any,
    timestamp: Any,
    zone_offset: str | None,
    provider: str | None,
    device: str | None,
    allowlist: frozenset[str] | None,
    is_daily_total: bool = False,
) -> dict | None:
    if not series_type or value is None:
        return None
    if not _provider_allowed(provider, allowlist):
        return None
    feature_type = resolve_feature_type(series_type)
    if feature_type is None:
        logger.debug("open-wearables: skipping unmapped series type %r", series_type)
        return None
    try:
        numeric_value = float(value) * get_conversion_factor(series_type)
    except (TypeError, ValueError):
        return None
    ts = to_hime_ts(timestamp, zone_offset)
    if ts is None:
        return None
    extra = None
    if is_daily_total:
        # Deterministic day-anchor (midnight UTC) rather than whatever
        # wall-clock moment the provider happened to stamp the pre-aggregated
        # total at — keeps the anchor stable/idempotent across re-polls and
        # gives the supersede-on-granular-arrival delete (see poller.py /
        # DataStore.delete_daily_total_anchors) an exact, predictable key.
        ts = ts[:10] + "T00:00:00"
        extra = {"daily_total": True}
    return {
        "date": ts,
        "feature_type": feature_type,
        "value": numeric_value,
        "metadata": _metadata_json(provider, device, extra),
    }


def _aggregate_daily_totals(mapped: list[tuple[dict, str, bool]]) -> list[tuple[dict, str, bool]]:
    """A provider can report one day's pre-aggregated total as MULTIPLE
    same-day ``is_daily_total`` rows instead of a single row — observed live
    against open-wearables' own seed data: Polar's continuous energy feed
    stamps every 5-minute chunk ``is_daily_total: true`` (not just one row
    per day). open-wearables' own archival aggregation sums these together
    to get the day's true total (``archival_repository.py``'s
    ``daily_sum_value`` / "Prefer the daily total when present, else sum
    the samples"), so we must too: anchoring every such row at the same
    day-midnight ``(feature_type, timestamp)`` key without summing them
    first collapses them via last-write-wins in
    ``DataStore.ingest_batch()``'s upsert, silently keeping only whichever
    row happens to be last in insertion order — observed storing 12.0 for a
    day whose true total (sum of all 288 same-day rows) was 2953.0. Sums
    same (feature_type, provider, day) daily-total rows within this batch
    into one row before they ever reach the anchor timestamp."""
    groups: dict[tuple[str, str, str], list[dict]] = {}
    order: list[tuple[str, str, str]] = []
    passthrough: list[tuple[dict, str, bool]] = []
    for row, provider, is_daily_total in mapped:
        if not is_daily_total:
            passthrough.append((row, provider, is_daily_total))
            continue
        key = (row["feature_type"], provider, row["date"])
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(row)

    for key in order:
        rows = groups[key]
        row = rows[0] if len(rows) == 1 else {**rows[0], "value": sum(r["value"] for r in rows)}
        passthrough.append((row, key[1], True))

    return passthrough


def _drop_superseded_daily_totals(mapped: list[tuple[dict, str, bool]]) -> list[dict]:
    """Granular data is authoritative (F4): within one mapped batch, drop any
    ``is_daily_total`` anchor row for a (feature_type, provider, UTC day)
    that also has a granular (non-daily-total) sample in the same batch —
    otherwise the two would both land in the store and roughly double the
    real daily total (granular sum + pre-aggregated total)."""
    granular_days = {
        (row["feature_type"], provider, row["date"][:10])
        for row, provider, is_daily_total in mapped
        if not is_daily_total
    }
    return [
        row
        for row, provider, is_daily_total in mapped
        if not (is_daily_total and (row["feature_type"], provider, row["date"][:10]) in granular_days)
    ]


def map_timeseries_samples(samples: list[dict], provider_allowlist: frozenset[str] | None = None) -> list[dict]:
    """Map a list of REST-shaped ``TimeSeriesSample`` dicts
    (``{timestamp, zone_offset, type, value, unit, is_daily_total,
    source: {provider, device}}``)."""
    mapped: list[tuple[dict, str, bool]] = []
    for s in samples or []:
        source = s.get("source") or {}
        is_daily_total = bool(s.get("is_daily_total"))
        row = _map_sample(
            series_type=s.get("type"),
            value=s.get("value"),
            timestamp=s.get("timestamp"),
            zone_offset=s.get("zone_offset"),
            provider=source.get("provider"),
            device=source.get("device"),
            allowlist=provider_allowlist,
            is_daily_total=is_daily_total,
        )
        if row:
            mapped.append((row, (source.get("provider") or "").strip().lower(), is_daily_total))
    return _drop_superseded_daily_totals(_aggregate_daily_totals(mapped))


def map_webhook_timeseries_event(data: dict, provider_allowlist: frozenset[str] | None = None) -> list[dict]:
    """Map a webhook timeseries batch: ``{user_id, provider, series_type,
    samples: [{timestamp, type, value, unit, is_daily_total}]}``. Chunked
    batches (``chunk_index``/``total_chunks``) are handled naively — ingest
    each chunk independently; the upsert on (timestamp, feature_type) makes
    re-ingesting an overlapping chunk harmless."""
    if not data:
        return []
    provider = data.get("provider")
    device = (data.get("source") or {}).get("device") if isinstance(data.get("source"), dict) else None
    default_series_type = data.get("series_type")
    mapped: list[tuple[dict, str, bool]] = []
    for item in data.get("samples") or []:
        is_daily_total = bool(item.get("is_daily_total"))
        row = _map_sample(
            series_type=item.get("type") or default_series_type,
            value=item.get("value"),
            timestamp=item.get("timestamp"),
            zone_offset=item.get("zone_offset"),
            provider=provider,
            device=item.get("device") or device,
            allowlist=provider_allowlist,
            is_daily_total=is_daily_total,
        )
        if row:
            mapped.append((row, (provider or "").strip().lower(), is_daily_total))
    return _drop_superseded_daily_totals(_aggregate_daily_totals(mapped))


# ---------------------------------------------------------------------------
# Sleep sessions
# ---------------------------------------------------------------------------

def map_sleep_session(session: dict, provider_allowlist: frozenset[str] | None = None) -> list[dict]:
    """Map one ``SleepSession`` record to HiMe's sleep_* feature samples.

    Two rules match HiMe's native Apple Health sleep convention
    (``prompts/data_schema.md``):

    F6 — timestamp anchor: sleep rows are stamped at the moment a segment
    *ended*, not started. Per-interval stage rows use each interval's own
    ``end_time``; session-level rows (in_bed, efficiency, and the unstaged
    total) use the session's ``end_time``.

    F5 — no double-counting: HiMe's total-sleep formula is
    ``sleep_asleep + sleep_core + sleep_deep + sleep_rem``. A session with a
    stage breakdown (from intervals or the summary-minutes fallback) emits
    ONLY its stage rows; the session-total ``sleep_asleep`` row is emitted
    only for sessions with no stage breakdown at all, so the total is never
    represented twice.
    """
    if not session:
        return []
    source = session.get("source") or {}
    provider = source.get("provider")
    if not _provider_allowed(provider, provider_allowlist):
        return []
    zone_offset = session.get("zone_offset")
    end_ts = to_hime_ts(session.get("end_time"), zone_offset)
    if end_ts is None:
        return []
    device = source.get("device")
    is_nap = bool(session.get("is_nap"))
    extra = {"session_id": session.get("id"), "is_nap": is_nap or None}
    metadata = _metadata_json(provider, device, extra)

    rows: list[dict] = []

    def _emit(ts: str, feature_type: str, seconds: float | None) -> None:
        if seconds is None or seconds < 0:
            return
        rows.append({"date": ts, "feature_type": feature_type, "value": float(seconds), "metadata": metadata})

    # Session-level rows — not part of the total-sleep formula, always
    # emitted (staged or not) when the source data provides them.
    duration_seconds = session.get("duration_seconds")
    if duration_seconds is not None:
        _emit(end_ts, "sleep_in_bed", duration_seconds)

    efficiency = session.get("efficiency_percent")
    if efficiency is not None:
        rows.append({
            "date": end_ts, "feature_type": "sleep_efficiency",
            "value": float(efficiency), "metadata": metadata,
        })

    # Per-stage breakdown: prefer sleep_stage_intervals (exact) over the
    # `stages` summary (pre-aggregated minutes, no per-interval timing).
    intervals = session.get("sleep_stage_intervals")
    stages = session.get("stages") or {}
    staged_via_summary = any(
        stages.get(k) is not None
        for k in ("awake_minutes", "light_minutes", "deep_minutes", "rem_minutes")
    )

    if intervals:
        # One row PER INTERVAL, anchored at that interval's own end time —
        # mirrors HiMe's native per-segment storage instead of summing same-
        # stage intervals into a single row at one shared timestamp.
        for interval in intervals:
            stage = str(interval.get("stage") or "").lower()
            feature = _SLEEP_STAGE_FEATURE.get(stage)
            if feature is None:
                continue  # in_bed / sleeping / unknown: not a specific stage
            start = parse_ow_datetime(interval.get("start_time"), zone_offset)
            end = parse_ow_datetime(interval.get("end_time"), zone_offset)
            if start is None or end is None:
                continue
            dur = (end - start).total_seconds()
            if dur > 0:
                _emit(ts_fmt(end), feature, dur)
    elif staged_via_summary:
        if stages.get("awake_minutes") is not None:
            _emit(end_ts, "sleep_awake", stages["awake_minutes"] * 60)
        if stages.get("light_minutes") is not None:
            _emit(end_ts, "sleep_core", stages["light_minutes"] * 60)
        if stages.get("deep_minutes") is not None:
            _emit(end_ts, "sleep_deep", stages["deep_minutes"] * 60)
        if stages.get("rem_minutes") is not None:
            _emit(end_ts, "sleep_rem", stages["rem_minutes"] * 60)
    else:
        # Unstaged session: the total IS the only sleep data we have.
        sleep_duration_seconds = session.get("sleep_duration_seconds")
        if sleep_duration_seconds is not None:
            _emit(end_ts, "sleep_asleep", sleep_duration_seconds)

    return rows


# ---------------------------------------------------------------------------
# Workouts
# ---------------------------------------------------------------------------

def _workout_slug(raw_type: str | None) -> str:
    t = (raw_type or "").strip().lower()
    for needle, slug in _WORKOUT_TYPE_ALIASES:
        if needle in t:
            return slug
    return "other"


def map_workout(workout: dict, provider_allowlist: frozenset[str] | None = None) -> list[dict]:
    """Map one ``Workout`` record to ``workout_<slug>_{duration,distance,energy}`` samples."""
    if not workout:
        return []
    source = workout.get("source") or {}
    provider = source.get("provider")
    if not _provider_allowed(provider, provider_allowlist):
        return []
    zone_offset = workout.get("zone_offset")
    ts = to_hime_ts(workout.get("start_time"), zone_offset)
    if ts is None:
        return []
    device = source.get("device")

    slug = _workout_slug(workout.get("type"))

    duration_seconds = workout.get("duration_seconds")
    if duration_seconds is None:
        start = parse_ow_datetime(workout.get("start_time"), zone_offset)
        end = parse_ow_datetime(workout.get("end_time"), zone_offset)
        if start is not None and end is not None:
            duration_seconds = (end - start).total_seconds()

    metadata = _metadata_json(provider, device, {
        "workout_id": workout.get("id"),
        "workout_type": workout.get("type"),
        "name": workout.get("name"),
        "avg_heart_rate_bpm": workout.get("avg_heart_rate_bpm"),
        "max_heart_rate_bpm": workout.get("max_heart_rate_bpm"),
        "avg_pace_sec_per_km": workout.get("avg_pace_sec_per_km"),
        "elevation_gain_meters": workout.get("elevation_gain_meters"),
    })

    candidates = (
        ("duration", duration_seconds),
        ("distance", workout.get("distance_meters")),
        ("energy", workout.get("calories_kcal")),
    )

    rows: list[dict] = []
    for metric, value in candidates:
        if value is None:
            continue
        feature_type = f"workout_{slug}_{metric}"
        if feature_type not in _KNOWN_WORKOUT_FEATURES:
            # e.g. "workout_yoga_distance" doesn't exist upstream — fall back
            # to the generic bucket rather than dropping the sample.
            feature_type = f"workout_other_{metric}"
            if feature_type not in _KNOWN_WORKOUT_FEATURES:
                continue
        rows.append({"date": ts, "feature_type": feature_type, "value": float(value), "metadata": metadata})

    return rows
