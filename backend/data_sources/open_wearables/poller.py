"""Background REST-pull loop for open-wearables.

Started from ``backend/main.py``'s lifespan only when
``settings.OPENWEARABLES_ENABLED`` is true — mirrors the structure of
``backend.api.agent_lifecycle._live_ingest_loop`` (asyncio task, high-water
mark per stream, defensive per-iteration error handling so one bad poll
never kills the loop).

State (OW user UUID + per-category cursors + webhook registration) is
persisted through the same ``app_state`` mechanism as the rest of HiMe
(``backend/api/config_routes.py``), under the ``"openwearables"`` key, so it
survives backend restarts.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from ...agent.data_store import DataStore
from ...config import settings
from ...utils import ts_now
from . import mapper
from .client import OpenWearablesClient
from .features import provider_allowlist_from_settings
from .webhook import WEBHOOK_PATH

logger = logging.getLogger(__name__)

_EXTERNAL_USER_ID = "LiveUser"
_CATEGORIES = ("timeseries", "workouts", "sleep")


def _state():
    """Return (and lazily create) the ``openwearables`` sub-dict of app_state."""
    from ...api.config_routes import get_app_state
    root = get_app_state()
    return root.setdefault("openwearables", {})


async def _save_state() -> None:
    from ...api.config_routes import save_app_state_locked
    await save_app_state_locked()


# ---------------------------------------------------------------------------
# User bootstrap
# ---------------------------------------------------------------------------

async def ensure_user(client: OpenWearablesClient) -> str | None:
    """Return the open-wearables UUID for the HiMe user, creating it on first run."""
    state = _state()
    if state.get("ow_user_id"):
        return state["ow_user_id"]

    user_id = await client.find_or_create_user(external_user_id=_EXTERNAL_USER_ID)
    if user_id:
        state["ow_user_id"] = user_id
        state["external_user_id"] = _EXTERNAL_USER_ID
        await _save_state()
        logger.info("open-wearables: resolved user %s -> %s", _EXTERNAL_USER_ID, user_id)
    return user_id


# ---------------------------------------------------------------------------
# Webhook self-registration (best-effort — see client.py docstring)
# ---------------------------------------------------------------------------

async def register_webhook_once(client: OpenWearablesClient, user_id: str) -> None:
    """Attempt webhook endpoint registration exactly once (skips if already
    persisted from a previous run). Never raises — a failure just means the
    poller stays the only ingestion path, which is fully sufficient."""
    state = _state()
    if (state.get("webhook") or {}).get("secret"):
        return

    base = (settings.OPENWEARABLES_WEBHOOK_CALLBACK_URL or "http://backend:8000").rstrip("/")
    url = f"{base}{WEBHOOK_PATH}"

    endpoint = await client.create_webhook_endpoint(url=url, description="HiMe", user_id=user_id)
    if not endpoint or not endpoint.get("id"):
        logger.info(
            "open-wearables: webhook endpoint registration skipped/failed for %s "
            "— relying on polling only (this is expected unless open-wearables "
            "accepts the HiMe API key for developer-scoped webhook management)",
            url,
        )
        return

    secret = await client.get_webhook_endpoint_secret(endpoint["id"])
    if not secret:
        logger.info("open-wearables: webhook endpoint %s created but secret fetch failed — relying on polling only", endpoint["id"])
        return

    state["webhook"] = {
        "endpoint_id": endpoint["id"],
        "secret": secret,
        "url": url,
        "registered_at": ts_now(),
    }
    await _save_state()
    logger.info("open-wearables: webhook endpoint registered at %s", url)


# ---------------------------------------------------------------------------
# Two-tier polling (F1/F2/F3)
# ---------------------------------------------------------------------------
#
# Fast poll (every OPENWEARABLES_POLL_INTERVAL): per-category event-time
# high-water-mark cursor, [cursor - _FAST_POLL_OVERLAP, now). The cursor only
# advances when the fetch was *complete* (PageResult.complete) — a client-
# swallowed transport error/timeout/non-2xx or a pagination-cap truncation
# must never be indistinguishable from "no new data", or the cursor sails
# past a window that still has ungathered samples in it (F2/F3). On a
# truncated-but-partially-successful fetch the cursor advances only to the
# last fetched sample's own event timestamp, so the next fast poll resumes
# exactly where pagination stopped instead of skipping ahead.
#
# Reconciliation pass (every OPENWEARABLES_RECONCILE_INTERVAL): independent
# of the per-category cursors, re-fetches [now - OPENWEARABLES_RECONCILE_
# WINDOW_HOURS, now] for every category and re-ingests (upsert on
# (timestamp, feature_type) makes overlap free). This is what actually
# fixes F1: a provider sync landing hours late (Oura syncing overnight data
# the next morning) writes samples whose *event* timestamps are already
# behind the fast-poll cursor's small overlap window — only a periodic
# rescan of a wide trailing window catches that.

_FAST_POLL_OVERLAP = timedelta(minutes=5)


def _parse_cursor(cursor: str | None) -> datetime | None:
    if not cursor:
        return None
    try:
        return datetime.fromisoformat(cursor)
    except ValueError:
        return None


def _max_event_dt(items: list[dict], field: str) -> datetime | None:
    """Latest event-time timestamp across raw OW records (not the mapped
    rows — mapped daily-total rows are deliberately re-anchored to a day
    boundary and would understate how far the fetch actually reached)."""
    best: datetime | None = None
    for item in items or []:
        dt = mapper.parse_ow_datetime(item.get(field), item.get("zone_offset"))
        if dt is not None and (best is None or dt > best):
            best = dt
    return best


def _granular_daily_keys(rows: list[dict]) -> list[tuple[str, str]]:
    """(feature_type, day) pairs among freshly-mapped granular (non-daily-
    total) rows — used to supersede any previously-stored daily-total anchor
    row for the same pair (F4)."""
    keys: set[tuple[str, str]] = set()
    for row in rows:
        if '"daily_total":true' in (row.get("metadata") or ""):
            continue
        keys.add((row["feature_type"], row["date"][:10]))
    return list(keys)


async def _fetch_map_ingest(
    client: OpenWearablesClient,
    user_id: str,
    data_store: DataStore,
    category: str,
    start_iso: str,
    end_iso: str,
) -> tuple[int, bool, datetime | None]:
    """Fetch + map + ingest one category in ``[start_iso, end_iso)``.

    Returns ``(rows_ingested, complete, last_event_dt)`` — ``complete``
    mirrors the client's :class:`~.client.PageResult`, and ``last_event_dt``
    is the latest event timestamp actually seen (used by the fast poll to
    resume a truncated fetch without skipping data — see module docstring).
    """
    allowlist = provider_allowlist_from_settings()

    if category == "timeseries":
        result = await client.get_timeseries(user_id, [], start_iso, end_iso)
        rows = mapper.map_timeseries_samples(result.items, allowlist)
        event_field = "timestamp"
    elif category == "workouts":
        result = await client.list_workouts(user_id, start_iso, end_iso)
        rows = [r for w in result.items for r in mapper.map_workout(w, allowlist)]
        event_field = "start_time"
    elif category == "sleep":
        result = await client.list_sleep_sessions(user_id, start_iso, end_iso)
        rows = [r for s in result.items for r in mapper.map_sleep_session(s, allowlist)]
        event_field = "start_time"
    else:  # pragma: no cover — defensive
        raise ValueError(f"unknown category: {category}")

    if rows:
        await asyncio.to_thread(data_store.ingest_batch, {"data": rows})
        if category == "timeseries":
            keys = _granular_daily_keys(rows)
            if keys:
                await asyncio.to_thread(data_store.delete_daily_total_anchors, keys)

    return len(rows), result.complete, _max_event_dt(result.items, event_field)


async def _poll_category_fast(
    client: OpenWearablesClient,
    user_id: str,
    data_store: DataStore,
    category: str,
    cursor: str | None,
) -> tuple[str | None, int, str, str | None]:
    """One fast-poll pass for one category. Returns
    ``(new_cursor, rows_ingested, status, error_message)``."""
    now = datetime.now(timezone.utc)
    cursor_dt = _parse_cursor(cursor)
    if cursor_dt is not None:
        start = cursor_dt - _FAST_POLL_OVERLAP
    else:
        start = now - timedelta(days=settings.OPENWEARABLES_BACKFILL_DAYS)

    n, complete, last_event_dt = await _fetch_map_ingest(
        client, user_id, data_store, category, start.isoformat(), now.isoformat(),
    )

    if complete:
        return now.isoformat(), n, "ok", None
    if last_event_dt is not None:
        # Truncated (pagination cap) but made progress — resume right after
        # the last sample we actually got, don't re-fetch from scratch and
        # don't skip past what's still unfetched.
        return last_event_dt.isoformat(), n, "partial", "fetch incomplete (pagination cap) — cursor advanced to last fetched sample only"
    # Nothing usable came back at all (transport error / non-2xx) — leave
    # the cursor exactly where it was (F2).
    return cursor, n, "error", "fetch failed (transport error or non-2xx) — cursor not advanced"


async def poll_once(client: OpenWearablesClient, user_id: str, data_store: DataStore) -> dict:
    """Run one fast-poll pass across all categories. Each category fails
    independently so a broken sleep endpoint can't block timeseries."""
    state = _state()
    cursors = state.setdefault("cursors", {})
    counts: dict[str, int] = {}
    statuses: dict[str, str] = {}
    errors: dict[str, str] = {}

    for category in _CATEGORIES:
        try:
            new_cursor, n, status, err = await _poll_category_fast(
                client, user_id, data_store, category, cursors.get(category),
            )
            cursors[category] = new_cursor
            counts[category] = n
            statuses[category] = status
            if err:
                errors[category] = err
        except Exception as exc:
            logger.warning("open-wearables poll: %s failed: %s", category, exc, exc_info=True)
            statuses[category] = "error"
            errors[category] = str(exc)

    state["last_poll_at"] = ts_now()
    state["last_poll_status"] = "error" if errors else "ok"
    state["last_poll_error"] = errors or None
    state["category_status"] = statuses
    state["counts"] = counts
    await _save_state()

    if any(counts.values()):
        logger.info("open-wearables poll: ingested %s", counts)
    return {"counts": counts, "errors": errors, "category_status": statuses}


# ---------------------------------------------------------------------------
# Reconciliation pass
# ---------------------------------------------------------------------------

def _reconcile_due(state: dict) -> bool:
    """True if it's been at least OPENWEARABLES_RECONCILE_INTERVAL seconds
    since the last reconciliation pass (or none has run yet)."""
    last = state.get("last_reconcile_at")
    if not last:
        return True
    try:
        last_dt = datetime.strptime(last, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    elapsed = (datetime.now(timezone.utc) - last_dt).total_seconds()
    return elapsed >= settings.OPENWEARABLES_RECONCILE_INTERVAL


async def reconcile_once(client: OpenWearablesClient, user_id: str, data_store: DataStore) -> dict:
    """Re-fetch and re-ingest ``[now - OPENWEARABLES_RECONCILE_WINDOW_HOURS,
    now]`` for every category, independent of the fast-poll cursors. This is
    the mechanism that catches data whose *event* time is already behind the
    fast poll's cursor by the time it lands in open-wearables (F1) — see the
    module docstring."""
    state = _state()
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=settings.OPENWEARABLES_RECONCILE_WINDOW_HOURS)
    start_iso, end_iso = start.isoformat(), now.isoformat()

    counts: dict[str, int] = {}
    errors: dict[str, str] = {}
    for category in _CATEGORIES:
        try:
            n, complete, _ = await _fetch_map_ingest(client, user_id, data_store, category, start_iso, end_iso)
            counts[category] = n
            if not complete:
                errors[category] = "reconcile fetch incomplete (transport error, non-2xx, or pagination cap)"
        except Exception as exc:
            logger.warning("open-wearables reconcile: %s failed: %s", category, exc, exc_info=True)
            errors[category] = str(exc)

    state["last_reconcile_at"] = ts_now()
    state["last_reconcile_status"] = "error" if errors else "ok"
    state["last_reconcile_error"] = errors or None
    state["reconcile_counts"] = counts
    await _save_state()

    if any(counts.values()):
        logger.info("open-wearables reconcile: ingested %s", counts)
    return {"counts": counts, "errors": errors}


# ---------------------------------------------------------------------------
# Optional optimization: trigger reconciliation from OW's sync-status API
# instead of waiting for the timer (skipped gracefully if unavailable).
# ---------------------------------------------------------------------------

async def _sync_events_indicate_new_data(client: OpenWearablesClient, user_id: str, state: dict) -> bool:
    """Best-effort peek at ``GET /users/{user_id}/sync/runs``: True if a
    sync run has completed since the last time this was checked, in which
    case the caller should reconcile immediately rather than wait up to
    OPENWEARABLES_RECONCILE_INTERVAL. Updates ``state["last_sync_event_marker"]``
    in place (caller is responsible for persisting it). Any failure
    (endpoint missing/unauthorized on older OW deployments, network error)
    just returns False — the timed reconciliation pass is the required
    baseline regardless, this is purely a latency optimization."""
    runs = await client.list_sync_run_summaries(user_id)
    if not runs:
        return False

    marker_dt = mapper.parse_ow_datetime(state.get("last_sync_event_marker"))
    newest_dt = marker_dt
    found_new = False
    for run in runs:
        if str(run.get("status")) not in ("success", "partial"):
            continue
        ended_dt = mapper.parse_ow_datetime(run.get("ended_at"))
        if ended_dt is None:
            continue
        if marker_dt is None or ended_dt > marker_dt:
            found_new = True
        if newest_dt is None or ended_dt > newest_dt:
            newest_dt = ended_dt

    if newest_dt is not None:
        state["last_sync_event_marker"] = newest_dt.isoformat()
    return found_new


# ---------------------------------------------------------------------------
# Background loop
# ---------------------------------------------------------------------------

async def openwearables_poll_loop() -> None:
    """Entry point started as an asyncio task from ``main.py``'s lifespan."""
    if not settings.OPENWEARABLES_ENABLED:
        return  # defensive — callers already gate on this

    client = OpenWearablesClient()
    data_store = DataStore(db_path=settings.DATA_STORE_PATH, user_id=_EXTERNAL_USER_ID)

    user_id = await ensure_user(client)
    if not user_id:
        logger.warning(
            "open-wearables: could not resolve/create the OW user (is OPENWEARABLES_BASE_URL "
            "reachable and OPENWEARABLES_API_KEY correct?) — will keep retrying every poll interval"
        )
    elif settings.OPENWEARABLES_WEBHOOK_ENABLED:
        try:
            await register_webhook_once(client, user_id)
        except Exception as exc:
            logger.info("open-wearables: webhook registration attempt raised: %s", exc)

    logger.info(
        "open-wearables poll loop started (interval=%ss, reconcile_interval=%ss, "
        "reconcile_window=%sh, backfill=%sd)",
        settings.OPENWEARABLES_POLL_INTERVAL, settings.OPENWEARABLES_RECONCILE_INTERVAL,
        settings.OPENWEARABLES_RECONCILE_WINDOW_HOURS, settings.OPENWEARABLES_BACKFILL_DAYS,
    )

    try:
        while True:
            if user_id is None:
                user_id = await ensure_user(client)
            if user_id:
                try:
                    await poll_once(client, user_id, data_store)
                except Exception as exc:
                    logger.error("open-wearables poll iteration failed: %s", exc, exc_info=True)

                state = _state()
                reconcile_now = False
                try:
                    reconcile_now = await _sync_events_indicate_new_data(client, user_id, state)
                except Exception as exc:
                    logger.debug(
                        "open-wearables: sync-events check failed (non-fatal — timed "
                        "reconciliation still applies): %s", exc,
                    )
                finally:
                    await _save_state()

                if reconcile_now or _reconcile_due(state):
                    try:
                        await reconcile_once(client, user_id, data_store)
                    except Exception as exc:
                        logger.error("open-wearables reconcile iteration failed: %s", exc, exc_info=True)
            await asyncio.sleep(max(1, settings.OPENWEARABLES_POLL_INTERVAL))
    except asyncio.CancelledError:
        logger.info("open-wearables poll loop stopping")
        raise
