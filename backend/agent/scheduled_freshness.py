"""Freshness gate for scheduled (cron) analysis runs.

Scheduled runs fire on the clock, but the data they describe arrives whenever
the phone happens to sync. When the newest ingested sample is older than
``SCHEDULED_FRESHNESS_MAX_AGE_MIN`` the run is *deferred* (not dropped): it is
re-checked periodically, released immediately by the live-ingest loop when
fresh data lands, and run anyway at the deadline. Chat, quick analysis and
event triggers never go through this gate (they are caused by fresh data).

Also provides the runtime-context line prepended to a scheduled run's goal so
the agent always knows the wall clock and how old the data is. The system
prompt stays static; only the per-run user message carries this.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

from ..config import settings
from ..utils import app_timezone, now_local, ts_now

logger = logging.getLogger(__name__)


class ScheduledGoal(str):
    """A goal string that came from the scheduler (cron / manual trigger).

    Behaves exactly like ``str`` (dedupe, logging, queue); the extra attributes
    tell ``_run_one_shot_analysis`` to add the run-context line.
    """

    deadline_passed: bool = False
    waited_min: float = 0.0


def parse_sample_ts(value: Any) -> datetime | None:
    """Parse a stored sample timestamp (UTC ISO string / datetime) or None."""
    if value is None:
        return None
    try:
        if isinstance(value, datetime):
            dt = value
        else:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _fmt_age(minutes: float) -> str:
    minutes = max(minutes, 0.0)
    if minutes < 60:
        return f"{minutes:.0f} min"
    hours = minutes / 60
    if hours < 48:
        return f"{hours:.1f} h"
    return f"{hours / 24:.1f} d"


def build_run_context(
    latest: datetime | None,
    *,
    now: datetime | None = None,
    deadline_passed: bool = False,
    waited_min: float = 0.0,
) -> str:
    """Return the ``[Run context]`` line (plus a stale-data note when due)."""
    now_l = now or now_local()
    tz = app_timezone()
    parts = [f"[Run context] Now: {now_l.strftime('%Y-%m-%d %H:%M')} {tz.key}."]
    stale = False
    if latest is not None:
        age_min = (now_l.astimezone(timezone.utc) - latest).total_seconds() / 60
        parts.append(
            f"Latest synced data: {latest.astimezone(tz).strftime('%Y-%m-%d %H:%M')} "
            f"({_fmt_age(age_min)} ago)."
        )
        limit = float(settings.SCHEDULED_FRESHNESS_MAX_AGE_MIN or 0)
        stale = limit > 0 and age_min > limit
    else:
        parts.append("No synced data found yet.")
    line = " ".join(parts)
    if deadline_passed and stale:
        line += (
            f" Note: this run waited {waited_min:.0f} min for a fresh sync and none arrived, "
            "so the most recent period may not have synced yet. If the period this task "
            "covers is missing from the data, say so in the report, label the report with "
            "the period it actually covers, and do not present older data as current."
        )
    return line


class ScheduledFreshnessMixin:
    """Deferral list for scheduled runs. Mixed into ``AutonomousHealthAgent``.

    Needs on the host: ``data_store``, ``_analysis_queue``, ``_emit``.
    State lives in ``_deferred_scheduled`` (created lazily).
    """

    def _deferred_list(self) -> list[dict]:
        lst = getattr(self, "_deferred_scheduled", None)
        if lst is None:
            lst = []
            self._deferred_scheduled = lst
        return lst

    async def _latest_sample_time(self) -> datetime | None:
        """Newest ingested sample time (UTC), or None when no data / on error."""
        try:
            df = await asyncio.to_thread(
                self.data_store.query, "SELECT MAX(timestamp) as ts FROM samples",
            )
            if df is None or df.empty or "ts" not in df.columns:
                return None
            return parse_sample_ts(df.iloc[0]["ts"])
        except Exception as exc:
            logger.debug("latest sample time unavailable: %s", exc)
            return None

    async def _data_age_min(self) -> float | None:
        latest = await self._latest_sample_time()
        if latest is None:
            return None
        return (datetime.now(timezone.utc) - latest).total_seconds() / 60

    async def _enqueue_scheduled(
        self, goal: str, *, deadline_passed: bool = False, waited_min: float = 0.0,
    ) -> None:
        item = ScheduledGoal(goal or "")
        item.deadline_passed = deadline_passed
        item.waited_min = waited_min
        q = self._analysis_queue
        if q.full():
            try:
                dropped = q.get_nowait()
                logger.warning("Analysis queue full — dropped oldest task: %s", (dropped or "")[:60])
            except asyncio.QueueEmpty:
                pass
        await q.put(item)

    async def _gate_scheduled(self, goal: str, task_id: int | str | None, gate: bool) -> None:
        """Enqueue now, or defer when data is stale. Gate errors fall through to enqueue."""
        max_age = float(settings.SCHEDULED_FRESHNESS_MAX_AGE_MIN or 0)
        if gate and max_age > 0:
            age = await self._data_age_min()  # None = no data / unreadable: never defer
            if age is not None and age > max_age:
                key = f"task:{task_id}" if task_id is not None else f"goal:{goal}"
                pending = self._deferred_list()
                if any(d["key"] == key for d in pending):
                    logger.info("Scheduled run already deferred (%s) — ignoring duplicate", key)
                    return
                now = time.monotonic()
                max_wait = float(settings.SCHEDULED_FRESHNESS_MAX_WAIT_MIN or 0)
                recheck = max(float(settings.SCHEDULED_FRESHNESS_RECHECK_MIN or 0), 0.05) * 60
                pending.append({
                    "key": key, "goal": goal, "task_id": task_id, "since": now,
                    "deadline": now + max_wait * 60, "next_check": now + recheck,
                })
                logger.info(
                    "Deferring scheduled run %s: newest data is %.0f min old (max %.0f)",
                    key, age, max_age,
                )
                await self._emit({
                    "type": "analysis_deferred",
                    "task_id": task_id, "goal": goal,
                    "data_age_min": round(age, 1),
                    "max_wait_min": max_wait,
                    "timestamp": ts_now(),
                })
                return
        await self._enqueue_scheduled(goal)

    def request_deferred_recheck(self) -> None:
        """Called by the ingest loop after a batch: re-check on the next poll."""
        for d in getattr(self, "_deferred_scheduled", None) or []:
            d["next_check"] = 0.0

    async def poll_deferred_scheduled(self) -> int:
        """Release deferred runs whose data is fresh or whose deadline passed.

        Cheap when nothing is deferred. Returns the number released.
        """
        pending = getattr(self, "_deferred_scheduled", None)
        if not pending:
            return 0
        now = time.monotonic()
        if not any(d["next_check"] <= now or d["deadline"] <= now for d in pending):
            return 0
        age = await self._data_age_min()
        max_age = float(settings.SCHEDULED_FRESHNESS_MAX_AGE_MIN or 0)
        now = time.monotonic()
        recheck = max(float(settings.SCHEDULED_FRESHNESS_RECHECK_MIN or 0), 0.05) * 60
        # Fresh (or gate disabled / no data at all) releases everything.
        fresh = age is None or max_age <= 0 or age <= max_age
        released = 0
        for d in list(pending):
            overdue = d["deadline"] <= now
            if not (fresh or overdue):
                if d["next_check"] <= now:
                    d["next_check"] = now + recheck
                continue
            if d not in pending:  # cancelled while we awaited
                continue
            pending.remove(d)
            waited = (now - d["since"]) / 60
            reason = "fresh_data" if fresh else "deadline"
            try:
                await self._enqueue_scheduled(
                    d["goal"], deadline_passed=(reason == "deadline"), waited_min=waited,
                )
            except Exception as exc:
                logger.error("Releasing deferred run failed: %s", exc)
                continue
            released += 1
            await self._emit({
                "type": "analysis_released",
                "task_id": d["task_id"], "goal": d["goal"], "reason": reason,
                "waited_min": round(waited, 1), "timestamp": ts_now(),
            })
        return released

    def cancel_deferred_scheduled(self) -> None:
        pending = getattr(self, "_deferred_scheduled", None)
        if pending:
            logger.info("Dropping %d deferred scheduled run(s) on stop", len(pending))
            pending.clear()
