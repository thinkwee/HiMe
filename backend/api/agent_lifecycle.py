"""
Agent lifecycle management — start, stop, status, restore, ingestion, supervisor.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request

from ..agent import MemoryManager, create_provider
from ..agent.autonomous_agent import AutonomousHealthAgent
from ..agent.data_store import DataStore
from ..config import settings
from ..utils import atomic_write_json, ts_fmt
from .agent_state import (
    _ID_RE,
    _RATE_LIMIT_CHAT_MAX_CALLS,
    ChatMessageRequest,
    ChatStopRequest,
    QuickAnalysisResponse,
    StartAgentRequest,
    _check_rate_limit,
    _client_ip,
    active_agents,
    startup_lock,
    system_ingest_tasks,
)

# Single-user identity for the in-app channel.
_LIVE_USER = "LiveUser"

logger = logging.getLogger(__name__)

lifecycle_router = APIRouter()

# ---------------------------------------------------------------------------
# Restart supervisor settings
# ---------------------------------------------------------------------------
_MAX_RESTART_BACKOFF_S = 300   # cap exponential back-off at 5 minutes
_RESTART_BASE_DELAY_S  = 5     # initial restart delay in seconds

# Streaming chunk events — NOT persisted to activity_log (ephemeral display only).
_EPHEMERAL_EVENT_TYPES = frozenset({
    "content",           # LLM response chunks (analysis)
    "agent_thinking",    # LLM thinking chunks (analysis)
    "chat_content",      # LLM response chunks (chat)
    "chat_thinking",     # LLM thinking chunks (chat)
    "chat_reply_delta",  # streamed reply_user text snapshots (chat)
    "token_usage",       # per-turn token stats (cumulative is in status)
    "startup_progress",  # init progress steps (transient UI feedback)
    "chat_thread_updated",  # thread metadata snapshots (state lives in the DB)
    "chat_thread_deleted",
})


# ---------------------------------------------------------------------------
# GET /last-config
# ---------------------------------------------------------------------------

@lifecycle_router.get("/last-config")
async def get_last_config():
    """Return the last successfully started agent configuration."""
    from pathlib import Path as _Path
    try:
        cfg_path = settings.AGENT_LAST_CONFIG_PATH
        if _Path(cfg_path).exists():
            def _read_config():
                with open(cfg_path) as f:
                    return json.load(f)
            config = await asyncio.to_thread(_read_config)
            return {"success": True, "config": config}
    except Exception as exc:
        logger.warning("Failed to read last agent config: %s", exc)
    return {"success": False, "config": None}


# ---------------------------------------------------------------------------
# POST /start
# ---------------------------------------------------------------------------

@lifecycle_router.post("/start")
async def start_autonomous_agent(request: Request, body: StartAgentRequest):
    """
    Start (or resume) an autonomous agent for a user.

    Only one agent may run globally.  If one is already running the request
    is rejected with a descriptive error so the caller knows what to do.

    Returns immediately and runs heavy initialisation in the background,
    emitting ``startup_progress`` events via the event queue so the frontend
    can show real-time feedback.
    """
    _check_rate_limit(_client_ip(request), "start")

    async with startup_lock:
        if active_agents:
            running_pid = next(iter(active_agents))
            if running_pid == body.user_id:
                return {"success": False, "error": "Agent is already running for this user."}
            return {
                "success": False,
                "error": f"Agent for '{running_pid}' is already running. Stop it first.",
            }

        # Register a placeholder immediately so the frontend can connect the
        # WebSocket monitor and receive startup_progress events.
        event_queue: asyncio.Queue = asyncio.Queue(maxsize=500)
        active_agents[body.user_id] = {
            "agent": None,
            "task": None,
            "data_store": None,
            "ingest_task": None,
            "event_queue": event_queue,
            "config": {},
            "memory": None,
            "_starting": True,
        }

    # Kick off the heavy init in the background — events flow to *event_queue*.
    asyncio.create_task(
        _start_agent_background(body, event_queue),
        name=f"agent-startup-{body.user_id}",
    )

    return {
        "success":        True,
        "user_id": body.user_id,
        "message":        "Agent startup initiated.",
    }


async def _start_agent_background(
    body: StartAgentRequest,
    event_queue: asyncio.Queue,
) -> None:
    """Run heavy agent initialisation in the background, emitting progress."""
    from ..utils import ts_now

    def _progress(step: int, total: int, label: str) -> None:
        _enqueue_event(event_queue, {
            "type": "startup_progress",
            "step": step,
            "total": total,
            "label": label,
            "timestamp": ts_now(),
        })

    try:
        _progress(1, 7, "Creating LLM provider")
        _agent_info = await _start_agent_internal(body, _progress, event_queue=event_queue)

        async with startup_lock:
            # POST /stop during startup removes the placeholder. If it is gone
            # (or replaced by a newer startup) we must NOT resurrect the agent:
            # tear down what we just built instead of registering it.
            _current = active_agents.get(body.user_id)
            if _current is None or _current.get("event_queue") is not event_queue:
                logger.info(
                    "Startup for %s was cancelled (stopped while starting) — "
                    "discarding the new agent", body.user_id,
                )
                await _teardown_agent_info(_agent_info)
                _enqueue_event(event_queue, {
                    "type": "agent_stopped",
                    "user_id": body.user_id,
                    "timestamp": ts_now(),
                })
                return
            # The frontend connects the monitor WS during startup to watch
            # progress; that connect lazily attaches an EventHub + fan-out pump
            # to the *placeholder* dict (keyed on the same event_queue). If we
            # replace the dict wholesale we'd drop those keys, orphan the pump
            # (it stays alive because the uid is still in active_agents) and a
            # later connect would spawn a *second* pump competing on the same
            # queue — the exact split-stream bug EventHub exists to prevent.
            # Carry the live hub/pump across the swap.
            _placeholder = active_agents.get(body.user_id)
            if _placeholder:
                for _k in ("event_hub", "fanout_task"):
                    if _placeholder.get(_k) is not None:
                        _agent_info[_k] = _placeholder[_k]
            active_agents[body.user_id] = _agent_info

        await _save_last_config(body)
        _progress(7, 7, "Agent started")
        # Emit agent_started so the frontend modal can transition to "done".
        # run_forever() also yields this event, but it may be delayed by the
        # supervisor task startup — emit it here for immediate UI feedback.
        _enqueue_event(event_queue, {
            "type": "agent_started",
            "user_id": body.user_id,
            "timestamp": ts_now(),
        })
        logger.info("Started autonomous agent for %s", body.user_id)

    except Exception as exc:
        logger.error("Background agent startup failed: %s", exc, exc_info=True)
        _enqueue_event(event_queue, {
            "type": "startup_error",
            "error": str(exc),
            "timestamp": ts_now(),
        })
        # Clean up the placeholder — but only OUR placeholder: after a /stop a
        # newer /start may have registered its own under the same user id.
        async with startup_lock:
            _cur = active_agents.get(body.user_id)
            if _cur is not None and _cur.get("event_queue") is event_queue:
                active_agents.pop(body.user_id, None)


async def _start_agent_internal(
    body: StartAgentRequest,
    progress: callable | None = None,
    event_queue: asyncio.Queue | None = None,
) -> dict:
    """Build all components and launch background tasks. Returns the registry entry."""
    def _step(step: int, total: int, label: str) -> None:
        if progress:
            progress(step, total, label)

    # 1. LLM provider
    _step(1, 7, "Creating LLM provider")
    api_key = _resolve_api_key(body.llm_provider)
    kwargs: dict = {}
    if body.llm_provider == "vllm":
        kwargs["base_url"] = settings.VLLM_BASE_URL
    elif body.llm_provider == "azure_openai":
        kwargs["azure_endpoint"] = settings.AZURE_OPENAI_ENDPOINT
        kwargs["api_version"]    = settings.AZURE_OPENAI_API_VERSION
    # Provider SDK imports / client construction can take seconds — keep them
    # (and the SQLite-opening constructors below) off the event loop.
    llm = await asyncio.to_thread(
        lambda: create_provider(body.llm_provider, model=body.model, api_key=api_key, **kwargs)
    )

    # 2. Data store (wearable health data, stored under data/data_stores)
    _step(2, 7, "Initialising health data store")
    data_store = await asyncio.to_thread(
        DataStore, db_path=settings.DATA_STORE_PATH, user_id=body.user_id,
    )

    # 3. Memory manager (schema owner)
    _step(3, 7, "Initialising memory")
    memory = await asyncio.to_thread(MemoryManager, settings.MEMORY_DB_PATH, body.user_id)

    # 4. Resolve messaging gateway registry (Telegram / Feishu / …)
    telegram_sender = None
    default_chat_id = None
    gateway_registry = None
    try:
        from ..main import get_gateway_registry, get_telegram_gateway
        gw = get_telegram_gateway()
        if gw:
            telegram_sender = gw.sender
            default_chat_id = settings.CHAT_ID
        gateway_registry = get_gateway_registry()
    except Exception:
        pass

    # 5. Agent (ToolRegistry picks up the shared GatewayRegistry — which
    #    holds every enabled channel — and falls back to the legacy
    #    telegram_sender path if the registry is empty/unavailable.)
    _step(4, 7, "Building agent and tools")
    from ..agent.skills.registry import SkillRegistry
    from ..agent.tools.registry import ToolRegistry

    def _build_agent() -> AutonomousHealthAgent:
        skill_registry = SkillRegistry(roots=AutonomousHealthAgent._resolve_skill_roots())
        registry = ToolRegistry.with_default_tools(
            data_store, settings.MEMORY_DB_PATH, body.user_id,
            telegram_sender=telegram_sender,
            default_chat_id=default_chat_id,
            gateway_registry=gateway_registry,
            skill_registry=skill_registry,
        )
        return AutonomousHealthAgent(
            user_id=body.user_id,
            llm_provider=llm,
            data_store=data_store,
            memory_db_path=settings.MEMORY_DB_PATH,
            tool_registry=registry,
        )

    agent = await asyncio.to_thread(_build_agent)

    # 6. Data ingestion stream (Check if system-level ingestion is already running)
    _step(5, 7, "Setting up data ingestion")
    existing_ingest = system_ingest_tasks.get(body.user_id)
    if existing_ingest is not None and not existing_ingest.done():
        logger.info("Reusing existing system-level ingestion task for %s", body.user_id)
        ingest_task = existing_ingest
    else:
        # A dead task must not be "reused": start a fresh (self-registering) one.
        ingest_task = await _build_ingest_task(body, data_store)

    # 7. Default scheduled tasks (first launch)
    _step(6, 7, "Configuring scheduled tasks")
    await asyncio.to_thread(_ensure_default_scheduled_tasks, memory)

    # 8. Event queue — use caller-provided one or create a new one
    if event_queue is None:
        event_queue = asyncio.Queue(maxsize=500)

    # 9. Agent supervisor task (restarts on crash)
    agent_task = asyncio.create_task(
        _agent_supervisor(body.user_id, agent, event_queue, memory),
        name=f"agent-{body.user_id}",
    )

    from ..agent.llm_providers import _DEFAULT_MODELS, LLMProvider
    try:
        prov = LLMProvider(body.llm_provider.lower())
        provider_default = _DEFAULT_MODELS[prov]
    except ValueError:
        provider_default = settings.DEFAULT_MODEL

    resolved_model = body.model or provider_default
    config = {
        "llm_provider":    body.llm_provider,
        "model":           resolved_model,
        "granularity":     body.granularity,
        "speed_multiplier": body.speed_multiplier,
    }

    return {
        "agent":       agent,
        "task":        agent_task,
        "data_store":  data_store,
        "ingest_task": ingest_task,
        "event_queue": event_queue,
        "config":      config,
        "memory":      memory,
    }


async def _build_ingest_task(body: StartAgentRequest, data_store: DataStore) -> asyncio.Task:
    """Build the live data-ingestion background task (self-restarting).

    The task is registered in ``system_ingest_tasks`` and carries a done
    callback: if it ever dies unexpectedly it is dropped from the registry and
    relaunched after a short delay, so one bad batch can't silence ingestion
    for the rest of the process lifetime.
    """
    from ..data_readers.watch_db_reader import WatchDBReader

    data_path = (Path(__file__).parent.parent.parent / "ios" / "Server").resolve()
    reader = await asyncio.to_thread(WatchDBReader, data_path)
    return _spawn_ingest_task(reader, data_store, body.user_id)


_INGEST_RESTART_DELAY_S = 5.0


def _spawn_ingest_task(reader, data_store: DataStore, user_id: str) -> asyncio.Task:
    """Create + register the ingest task and attach the restart callback."""
    task = asyncio.create_task(
        _live_ingest_loop(reader, data_store, user_id),
        name=f"ingest-{user_id}",
    )
    system_ingest_tasks[user_id] = task

    def _on_done(t: asyncio.Task) -> None:
        if system_ingest_tasks.get(user_id) is not t:
            return  # replaced or deliberately removed (shutdown)
        if t.cancelled():
            return
        system_ingest_tasks.pop(user_id, None)
        exc = t.exception()
        logger.error("Live ingest task for %s died (%r) — restarting in %.0fs",
                     user_id, exc, _INGEST_RESTART_DELAY_S)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        def _restart() -> None:
            if user_id not in system_ingest_tasks:
                data_store.is_ingesting = False
                _spawn_ingest_task(reader, data_store, user_id)

        loop.call_later(_INGEST_RESTART_DELAY_S, _restart)

    task.add_done_callback(_on_done)
    return task


_INGEST_POLL_INTERVAL_S = 5.0
_TRIGGER_SWEEP_INTERVAL_S = 60.0  # absent rules + throttled-feature catch-up
_INGEST_MAX_BACKOFF_S = 60.0
_INGEST_PAGE_SIZE = 100000  # matches the reader's default row limit
# Epoch values above this are milliseconds (1e11 s is year 5138).
_MS_EPOCH_THRESHOLD = 1e11
_MAX_VALID_EPOCH = 4102444800.0  # 2100-01-01


def _coerce_epoch(ts: object) -> float | None:
    """Normalise a raw watch.db timestamp to epoch seconds, or None if bad."""
    try:
        v = float(ts)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    if v > _MS_EPOCH_THRESHOLD:
        v /= 1000.0
    if v <= 0 or v > _MAX_VALID_EPOCH:
        return None
    return v


def _build_records(samples: list, pid: str) -> list:
    """Convert raw watch.db rows to DataStore ingest format.

    Rows with an unusable timestamp/value are skipped individually (and
    counted in one log line) so a single corrupt row never blocks the batch.
    """
    records = []
    skipped = 0
    for s in samples:
        try:
            epoch = _coerce_epoch(s.get("ts"))
            value = s.get("value")
            if epoch is None or value is None or not s.get("feature_type"):
                skipped += 1
                continue
            if isinstance(value, float) and not math.isfinite(value):
                skipped += 1
                continue
            dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
            records.append({
                "date": ts_fmt(dt),
                "value": value,
                "feature_type": s["feature_type"],
                "pid": pid,
            })
        except Exception:
            skipped += 1
    if skipped:
        logger.warning("Live ingest: skipped %d malformed row(s) out of %d", skipped, len(samples))
    return records


async def _ingest_cycle(reader, data_store: DataStore, user_id: str, hwm: dict,
                        trigger_eval, evaluate_triggers: bool = True) -> int:
    """Run one poll+ingest pass. Returns the number of source rows consumed.

    ``hwm`` (``{"id": int, "ua": float}``) is only advanced after the batch is
    durably saved, so a failed write is retried on the next pass instead of
    silently dropping rows. Raises on DB errors (the caller backs off).
    """
    # Detect a recreated watch.db (ids restart below our high-water mark).
    max_id = await asyncio.to_thread(reader.get_max_id)
    if max_id is not None and max_id < hwm["id"]:
        logger.warning(
            "watch.db max id %d < stored HWM %d for %s — database was recreated; "
            "resetting high-water marks", max_id, hwm["id"], user_id,
        )
        hwm["id"] = 0
        hwm["ua"] = 0.0
        await asyncio.to_thread(data_store.save_ingestion_id, 0)
        await asyncio.to_thread(data_store.save_last_updated_at, 0.0)

    # New rows (id-based) + in-place updates (updated_at-based).
    new_samples = await asyncio.to_thread(reader.get_all_samples_since_id, hwm["id"])
    updated_samples = await asyncio.to_thread(reader.get_samples_updated_since, hwm["ua"])

    new_id = hwm["id"]
    new_ua = hwm["ua"]
    for s in new_samples:
        try:
            new_id = max(new_id, int(s["id"]))
            new_ua = max(new_ua, float(s.get("updated_at") or 0.0))
        except (TypeError, ValueError, KeyError):
            continue
    seen_ids = {s.get("id") for s in new_samples}
    extra = [s for s in updated_samples if s.get("id") not in seen_ids]
    for s in updated_samples:
        try:
            new_ua = max(new_ua, float(s.get("updated_at") or 0.0))
        except (TypeError, ValueError):
            continue

    consumed = len(new_samples) + len(extra)
    if not consumed:
        return 0

    all_records = _build_records(new_samples, user_id) + _build_records(extra, user_id)
    if all_records:
        batch = {
            "data": all_records,
            "data_timestamp": max(r["date"] for r in all_records),
            "num_records": len(all_records),
            "is_live": True,
        }
        await asyncio.to_thread(data_store.ingest_batch, batch)
    await asyncio.to_thread(data_store.save_ingestion_id, new_id)
    await asyncio.to_thread(data_store.save_last_updated_at, new_ua)
    hwm["id"], hwm["ua"] = new_id, new_ua
    logger.info("Live ingest: %d records synced (%d new, %d updated) hwm_id=%d ua=%.0f",
                len(all_records), len(new_samples), len(extra), new_id, new_ua)

    if all_records and evaluate_triggers:
        try:
            triggered = await trigger_eval.evaluate_after_ingest(
                agent_queue=_get_agent_analysis_queue(user_id),
                ingested_features={r["feature_type"] for r in all_records},
            )
            if triggered:
                logger.info("Triggers fired: %s", [t["name"] for t in triggered])
        except Exception as exc:
            logger.debug("Trigger evaluation error: %s", exc)
    return consumed


async def _periodic_trigger_sweep(trigger_eval, user_id: str) -> None:
    """Evaluate rules that per-batch evaluation can't reach: ``absent`` rules
    (nothing is ingested while data is missing) and features whose batch was
    skipped by the evaluator's throttle. Only runs while an agent can take the
    resulting analysis task."""
    queue = _get_agent_analysis_queue(user_id)
    if queue is None:
        return
    try:
        triggered = await trigger_eval.evaluate_periodic(queue)
        if triggered:
            logger.info("Periodic triggers fired: %s", [t["name"] for t in triggered])
    except Exception as exc:
        logger.warning("Periodic trigger sweep failed for %s: %s", user_id, exc)


async def _live_ingest_loop(reader, data_store: DataStore, user_id: str) -> None:
    """
    Continuously poll the live watch.db and forward ALL samples into the DataStore.
    The first pass is the historical back-fill (no trigger evaluation); then
    lossless incremental polling. Every pass is individually guarded: any error
    is logged and retried with exponential back-off — the loop only ends on
    cancellation.
    """
    from ..agent.trigger_evaluator import TriggerEvaluator

    hwm = {
        "id": await asyncio.to_thread(data_store.get_last_ingested_id),
        "ua": await asyncio.to_thread(data_store.get_last_updated_at),
    }
    trigger_eval = TriggerEvaluator(
        memory_db_path=settings.MEMORY_DB_PATH,
        user_id=user_id,
        health_db_path=data_store.db_file,
    )
    logger.info("Live ingest loop started for %s. ID HWM: %d, updated_at HWM: %.0f",
                user_id, hwm["id"], hwm["ua"])
    data_store.is_ingesting = True

    backoff = _INGEST_POLL_INTERVAL_S
    first = True
    last_sweep = time.monotonic()
    try:
        while data_store.is_ingesting:
            if not first and time.monotonic() - last_sweep >= _TRIGGER_SWEEP_INTERVAL_S:
                last_sweep = time.monotonic()
                await _periodic_trigger_sweep(trigger_eval, user_id)
            try:
                consumed = await _ingest_cycle(
                    reader, data_store, user_id, hwm, trigger_eval,
                    evaluate_triggers=not first,
                )
                first = False
                backoff = _INGEST_POLL_INTERVAL_S
                if consumed >= _INGEST_PAGE_SIZE:
                    continue  # full page — more backlog waiting, don't sleep
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Live ingest cycle failed for %s: %s — retrying in %.0fs",
                             user_id, exc, backoff, exc_info=True)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _INGEST_MAX_BACKOFF_S)
                continue
            await asyncio.sleep(_INGEST_POLL_INTERVAL_S)
    except asyncio.CancelledError:
        pass
    finally:
        data_store.is_ingesting = False
        logger.info("Live ingest loop stopped for %s", user_id)


def _resolve_api_key(provider: str) -> str | None:
    """Resolve API key for any supported LLM provider."""
    from ..agent.llm import get_env_api_key
    return get_env_api_key(provider)


def _get_agent_analysis_queue(user_id: str) -> asyncio.Queue | None:
    """Return the running agent's analysis queue (if any)."""
    info = active_agents.get(user_id)
    if info:
        agent = info.get("agent")
        if agent and hasattr(agent, "_analysis_queue"):
            return agent._analysis_queue
    return None


# ---------------------------------------------------------------------------
# Agent supervisor  (auto-restart on crash)
# ---------------------------------------------------------------------------

async def _agent_supervisor(
    user_id: str,
    agent: AutonomousHealthAgent,
    event_queue: asyncio.Queue,
    memory: MemoryManager,
) -> None:
    """
    Drive the agent loop.  Restarts automatically with exponential back-off
    if the agent raises an unexpected exception.
    """
    backoff = _RESTART_BASE_DELAY_S

    while user_id in active_agents:
        ran_successfully = False
        try:
            async for event in agent.run_forever():
                ran_successfully = True
                _enqueue_event(event_queue, event)
                etype = event.get("type", "")
                if etype not in _EPHEMERAL_EVENT_TYPES:
                    _spawn_persist(memory, event)
                _log_event(event)
            # run_forever returned normally (stop was called)
            break
        except asyncio.CancelledError:
            logger.info("Agent supervisor cancelled for %s", user_id)
            break
        except Exception as exc:
            if user_id not in active_agents:
                break  # was externally stopped while crashing
            if ran_successfully:
                backoff = _RESTART_BASE_DELAY_S
            logger.error(
                "Agent %s crashed: %s — restarting in %ds",
                user_id, exc, backoff, exc_info=True,
            )
            _enqueue_event(event_queue, {"type": "agent_error", "error": str(exc)})
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _MAX_RESTART_BACKOFF_S)


# Strong references to in-flight activity-persist tasks. asyncio only keeps a
# weak reference to a running task, so a bare create_task() can be garbage
# collected mid-write; keeping the set alive also gives us a place to surface
# exceptions that would otherwise be swallowed as "never retrieved".
_persist_tasks: set[asyncio.Task] = set()


def _spawn_persist(memory: MemoryManager, event: dict) -> None:
    """Persist an activity event in the background, keeping a hard reference."""
    task = asyncio.create_task(memory.persist_activity(event))
    _persist_tasks.add(task)

    def _done(t: asyncio.Task) -> None:
        _persist_tasks.discard(t)
        if not t.cancelled() and t.exception() is not None:
            logger.warning("persist_activity failed: %s", t.exception())

    task.add_done_callback(_done)


def _enqueue_event(queue: asyncio.Queue, event: dict) -> None:
    """Non-blocking enqueue; drops oldest item if full."""
    try:
        safe = json.loads(json.dumps(event, default=str))
    except Exception:
        safe = {"type": event.get("type", "unknown"), "raw": str(event)[:500]}
    try:
        queue.put_nowait(safe)
    except asyncio.QueueFull:
        try:
            queue.get_nowait()
            queue.put_nowait(safe)
        except Exception:
            pass


def _log_event(event: dict) -> None:
    etype = event.get("type")
    if etype in ("agent_started", "agent_stopped", "agent_error"):
        logger.info("Agent event: %s", event)
    elif etype in ("cycle_start", "cycle_end", "forced_sleep"):
        logger.info("Agent cycle: %s", event)
    elif etype == "error":
        logger.error("Agent error: %s", event.get("error"))


# ---------------------------------------------------------------------------
# POST /stop
# ---------------------------------------------------------------------------

async def _cancel_and_wait(task: asyncio.Task, timeout: float = 5.0) -> None:
    """Cancel *task* and wait (bounded) for it to finish.

    Swallows the task's OWN ``CancelledError`` (expected) but re-raises it when
    the *caller* is the one being cancelled — otherwise a cancelled request
    handler would keep running as if nothing happened.
    """
    task.cancel()
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
    except asyncio.TimeoutError:
        pass
    except asyncio.CancelledError:
        if not task.done():
            raise  # we were cancelled, not the awaited task


async def _teardown_agent_info(info: dict) -> None:
    """Stop an agent registry entry: agent loop, supervisor and fan-out pump.

    The ingest task is deliberately left running (system-level, independent of
    the agent lifecycle).
    """
    if info.get("agent"):
        info["agent"].stop()
    for key in ("task", "fanout_task"):
        t: asyncio.Task | None = info.get(key)
        if t is not None:
            await _cancel_and_wait(t)


@lifecycle_router.post("/stop")
async def stop_autonomous_agent(request: Request, user_id: str = Query("LiveUser")):
    """Stop the running agent. Defaults to LiveUser (single-user mode)."""
    _check_rate_limit(_client_ip(request), "stop")

    async with startup_lock:
        info = active_agents.pop(user_id, None)
        if not info:
            raise HTTPException(status_code=404, detail=f"No agent running for '{user_id}'")

        try:
            await _teardown_agent_info(info)
            logger.info("Stopped autonomous agent for %s (Ingestion continues)", user_id)
            return {"success": True, "user_id": user_id}

        except Exception as exc:
            logger.error("Error stopping agent: %s", exc, exc_info=True)
            raise HTTPException(status_code=500, detail="Failed to stop the agent.") from exc


# ---------------------------------------------------------------------------
# GET /status
# ---------------------------------------------------------------------------

@lifecycle_router.get("/status")
async def get_agent_status(user_id: str | None = None):
    """Return running status.  If user_id is omitted, returns all agents."""
    if user_id:
        info = active_agents.get(user_id)
        if not info:
            return {"success": False, "running": False, "user_id": user_id}
        if info.get("_starting"):
            return {"success": True, "running": True, "starting": True,
                    "user_id": user_id, "config": info.get("config", {})}
        agent: AutonomousHealthAgent = info["agent"]
        status = await asyncio.to_thread(agent.get_status)
        return {
            "success":           True,
            "running":           True,
            "status":            status,
            "config":            info["config"],
            "data_store_stats":  status.get("data_store_stats", {}),
        }
    # All agents (run get_status off event loop)
    async def _status_for(pid, inf):
        if inf.get("_starting"):
            return pid, {"starting": True, "config": inf.get("config", {})}
        s = await asyncio.to_thread(inf["agent"].get_status)
        return pid, {"status": s, "config": inf["config"]}
    results = await asyncio.gather(
        *[_status_for(pid, inf) for pid, inf in active_agents.items()],
        return_exceptions=True,
    )
    results = [r for r in results if not isinstance(r, BaseException)]
    return {
        "success":      True,
        "active_agents": len(active_agents),
        "agents":        dict(results),
    }


# ---------------------------------------------------------------------------
# POST /quick-analysis
# ---------------------------------------------------------------------------

_QUICK_ANALYSIS_MAX_CALLS = 10  # per minute per IP — each call is a full LLM run


@lifecycle_router.post("/quick-analysis")
async def quick_analysis(request: Request):
    """
    Trigger a rapid health status analysis (max 3 tool calls, 30s timeout).
    Returns {state: CatState, message: str} for iOS cat animation.
    """
    _check_rate_limit(_client_ip(request), "quick-analysis", _QUICK_ANALYSIS_MAX_CALLS)
    item = next(iter(active_agents.items()), None)
    if not item:
        return QuickAnalysisResponse(state="neutral", message="Agent not running. Start the agent first.")

    pid, info = item
    if info.get("_starting"):
        return QuickAnalysisResponse(state="neutral", message="Agent is still starting up. Please wait.")
    agent = info["agent"]

    try:
        result = await asyncio.wait_for(
            agent.run_quick_analysis(),
            timeout=32.0
        )
        if not result or not isinstance(result, dict):
            return QuickAnalysisResponse(state="neutral", message="Analysis returned no result.")
        return QuickAnalysisResponse(
            state=result.get("state", "neutral"),
            message=result.get("message", "Analysis complete.")
        )
    except asyncio.TimeoutError:
        return QuickAnalysisResponse(state="neutral", message="Analysis took too long. Try again later.")
    except Exception as e:
        logger.error("Quick analysis error: %s", e)
        return QuickAnalysisResponse(state="neutral", message="Analysis error. Check the logs.")


# ---------------------------------------------------------------------------
# POST /chat — inbound in-app chat (iOS native channel)
# ---------------------------------------------------------------------------

def _last_config_start_request() -> StartAgentRequest:
    """Build a StartAgentRequest from the last-used config (or defaults).

    Used to auto-start the agent on demand when a chat arrives and the agent
    isn't running yet.
    """
    body = StartAgentRequest(user_id=_LIVE_USER)
    try:
        cfg_path = settings.AGENT_LAST_CONFIG_PATH
        if os.path.exists(cfg_path):
            with open(cfg_path) as f:
                cfg = json.load(f)
            if cfg.get("llm_provider"):
                body.llm_provider = cfg["llm_provider"]
            if cfg.get("model"):
                body.model = cfg["model"]
            if cfg.get("granularity"):
                body.granularity = cfg["granularity"]
            if cfg.get("speed_multiplier"):
                body.speed_multiplier = cfg["speed_multiplier"]
    except Exception as exc:
        logger.warning("Could not read last agent config (%s) — using defaults", exc)
    return body


async def _alast_config_start_request() -> StartAgentRequest:
    """Off-loop variant of :func:`_last_config_start_request` (file read)."""
    return await asyncio.to_thread(_last_config_start_request)


async def _ensure_agent_started() -> str:
    """Ensure the agent is running or starting. Returns the state.

    ``"running"`` if live, else ``"starting"`` (kicking a background startup
    if none is in progress). Mirrors POST /start's guarded placeholder.
    """
    async with startup_lock:
        info = active_agents.get(_LIVE_USER)
        if info is not None:
            if info.get("_starting") or not info.get("agent"):
                return "starting"
            return "running"
        event_queue: asyncio.Queue = asyncio.Queue(maxsize=500)
        active_agents[_LIVE_USER] = {
            "agent": None,
            "task": None,
            "data_store": None,
            "ingest_task": None,
            "event_queue": event_queue,
            "config": {},
            "memory": None,
            "_starting": True,
        }
    asyncio.create_task(
        _start_agent_background(await _alast_config_start_request(), event_queue),
        name=f"agent-startup-{_LIVE_USER}",
    )
    return "starting"


async def _store_inbound_image(image_base64: str, image_mime: str | None) -> dict | None:
    """Decode a base64 image, size-check it, and write it to the uploads dir.

    Returns an attachment dict ``{"type":"image","path":...,"mime":...}`` or
    None if invalid/too large. Files land in ``<DATA_STORE_PATH>/LiveUser/
    uploads/`` and are read by the agent to pass to a vision-capable LLM. The
    write is offloaded to a thread.
    """
    import base64

    # Reject oversized payloads *before* decoding — base64 inflates memory by
    # ~4/3, so decoding first would materialise the whole thing just to throw
    # it away. 3/4 of the encoded length is an upper bound on the decoded size.
    if len(image_base64) * 3 // 4 > settings.IOS_MAX_IMAGE_BYTES:
        logger.warning(
            "Inbound image rejected before decode (encoded=%d, max=%d)",
            len(image_base64), settings.IOS_MAX_IMAGE_BYTES,
        )
        return None
    try:
        raw = base64.b64decode(image_base64)
    except Exception as e:
        logger.warning("Bad inbound image: %s", e)
        return None
    if not raw or len(raw) > settings.IOS_MAX_IMAGE_BYTES:
        logger.warning(
            "Inbound image rejected (size=%d, max=%d)",
            len(raw), settings.IOS_MAX_IMAGE_BYTES,
        )
        return None
    mime = (image_mime or "image/jpeg").lower()
    ext = {
        "image/jpeg": "jpg", "image/png": "png", "image/heic": "heic",
        "image/webp": "webp", "image/gif": "gif",
    }.get(mime, "jpg")
    updir = Path(settings.DATA_STORE_PATH) / _LIVE_USER / "uploads"

    def _write() -> str:
        updir.mkdir(parents=True, exist_ok=True)
        path = updir / f"{uuid.uuid4().hex}.{ext}"
        with open(path, "wb") as f:
            f.write(raw)
        return str(path)

    path = await asyncio.to_thread(_write)
    return {"type": "image", "path": path, "mime": mime}


@lifecycle_router.post("/chat")
async def post_chat_message(request: Request, body: ChatMessageRequest):
    """Accept an in-app chat message and route it to the agent inbox.

    The reply is NOT returned here — it streams back over the
    ``/api/stream/agent`` WebSocket as ``chat_thinking`` / ``chat_content``
    chunks followed by a final ``chat_reply`` event (and ``chat_image`` for
    charts).
    """
    _check_rate_limit(_client_ip(request), "chat", _RATE_LIMIT_CHAT_MAX_CALLS)

    text = (body.text or "").strip()
    attachments: list[dict] = []
    if settings.IOS_VISION_ENABLED and body.image_base64:
        att = await _store_inbound_image(body.image_base64, body.image_mime)
        if att:
            attachments.append(att)
    if not text and not attachments:
        raise HTTPException(status_code=400, detail="Empty message")

    from ..ios_gateway.threads import MAIN_THREAD_ID, chat_id_for
    from .agent_threads import check_thread_id, emit_thread_event, get_thread_memory
    thread_id = check_thread_id(body.thread_id)
    if thread_id != MAIN_THREAD_ID:
        memory = await get_thread_memory()
        thread = await asyncio.to_thread(memory.get_chat_thread, thread_id)
        if thread is None:
            raise HTTPException(status_code=404, detail="Thread not found")
        if thread.get("archived"):
            # Writing into an archived thread revives it.
            thread = await asyncio.to_thread(
                lambda: memory.update_chat_thread(thread_id, archived=False, touch=True)
            )
            await emit_thread_event({"type": "chat_thread_updated", "thread": thread})

    info = active_agents.get(_LIVE_USER)
    if info is None or info.get("_starting") or not info.get("agent"):
        # Agent not ready — kick a startup and tell the client to retry once
        # the agent_started event arrives on the stream.
        state = await _ensure_agent_started()
        return {"success": True, "status": state, "queued": False}

    from ..messaging.base import MessageChannel, MessageEnvelope

    envelope = MessageEnvelope(
        message_id=body.client_msg_id or uuid.uuid4().hex,
        channel=MessageChannel.IOS,
        sender_id=_LIVE_USER,
        content=text,
        chat_id=chat_id_for(_LIVE_USER, thread_id),
        attachments=attachments,
    )
    await info["agent"].inbox.push(envelope)
    return {"success": True, "status": "queued", "queued": True}


@lifecycle_router.post("/chat/stop")
async def post_chat_stop(request: Request, body: ChatStopRequest | None = None):
    """Cancel the in-flight chat run (mid-LLM-call or mid-tool, sub-agents
    included) without stopping the agent or its queued analysis tasks.

    Returns ``{"success": true, "stopped": bool}``; ``stopped`` is false when no
    chat run is active. Clients receive ``chat_stopped`` on the agent stream.
    """
    _check_rate_limit(_client_ip(request), "chat", _RATE_LIMIT_CHAT_MAX_CALLS)
    uid = (body.user_id if body else None) or _LIVE_USER
    info = active_agents.get(uid)
    agent = info.get("agent") if info else None
    if agent is None:
        return {"success": True, "stopped": False}
    from .agent_threads import check_thread_id
    thread_id = check_thread_id(body.thread_id) if body and body.thread_id else None
    return {"success": True, "stopped": bool(agent.stop_chat(thread_id))}


# ---------------------------------------------------------------------------
# Lifecycle helpers (called from main.py)
# ---------------------------------------------------------------------------

async def try_restore_agent() -> None:
    """On startup, restore the last running agent if AUTO_RESTORE_AGENT is set."""
    if not settings.AUTO_RESTORE_AGENT:
        logger.info("[Startup] Agent restore skipped (AUTO_RESTORE_AGENT=false)")
        return
    cfg_path = settings.AGENT_LAST_CONFIG_PATH
    if not cfg_path.exists():
        logger.info("[Startup] Agent restore skipped (no saved config)")
        return
    try:
        logger.info("[Startup] Restoring agent from %s (loading data may take a while)...", cfg_path)
        def _read_restore_config():
            with open(cfg_path) as f:
                return json.load(f)
        cfg = await asyncio.to_thread(_read_restore_config)
        pid = cfg.get("user_id")
        if not pid or not isinstance(pid, str) or not _ID_RE.match(pid):
            logger.warning("[Startup] Agent restore skipped (invalid user_id in saved config)")
            return
        body = StartAgentRequest(
            user_id=pid,
            llm_provider=cfg.get("llm_provider", "gemini"),
            model=cfg.get("model"),
            granularity=cfg.get("granularity", "1hour"),
            speed_multiplier=cfg.get("speed_multiplier", 1.0),
        )
        # Call internal start directly — no need for rate limiting on auto-restore
        async with startup_lock:
            if not active_agents:
                _agent_info = await _start_agent_internal(body)
                active_agents[body.user_id] = _agent_info
                await _save_last_config(body)
        logger.info("Auto-restored agent for %s", pid)
    except Exception as exc:
        logger.warning("Auto-restore agent failed: %s", exc)


async def restart_agent(user_id: str) -> bool:
    """Stop the running agent and restart from saved config. Returns True on success."""
    async with startup_lock:
        info = active_agents.pop(user_id, None)
        if not info:
            return False
        # Cancel the fan-out pump as well as the supervisor (mirrors /stop).
        # A surviving pump would keep draining the OLD event_queue while every
        # connected monitor stays subscribed to the OLD hub — the new
        # supervisor's events would then never reach them.
        await _teardown_agent_info(info)

        # Start again directly from the saved config. Going through
        # try_restore_agent() would make /restart a no-op stop whenever
        # AUTO_RESTORE_AGENT is false (the default).
        body = await _alast_config_start_request()
        body.user_id = user_id
        try:
            new_info = await _start_agent_internal(body)
        except Exception as exc:
            logger.error("Agent restart failed for %s: %s", user_id, exc, exc_info=True)
            return False

        # Carry the EventHub across so already-connected monitors keep their
        # subscriptions, but re-point its pump at the NEW event_queue — a pump
        # is bound to the queue it was created with, so reusing the old one
        # would leave every viewer deaf. No await between create_task and the
        # registry write, so the pump sees the uid as registered.
        hub = info.get("event_hub")
        if hub is not None:
            from .event_hub import _fanout_pump
            new_info["event_hub"] = hub
            new_info["fanout_task"] = asyncio.create_task(
                _fanout_pump(user_id, new_info["event_queue"], hub),
                name=f"fanout-{user_id}",
            )
        active_agents[user_id] = new_info

    await _save_last_config(body)
    logger.info("Restarted autonomous agent for %s", user_id)
    return user_id in active_agents


async def shutdown_agents() -> None:
    """Cancel all running agents gracefully during server shutdown."""
    if not active_agents:
        return
    logger.info("Shutting down %d agent(s)…", len(active_agents))
    all_tasks = []
    for pid, info in list(active_agents.items()):
        try:
            if info.get("agent"):
                info["agent"].stop()
                # Flush the latest in-memory state to disk. Transitions already
                # persist continuously, so this is only a final safety net.
                if hasattr(info["agent"], "_save_state"):
                    try:
                        info["agent"]._save_state()
                    except Exception as exc:
                        logger.error("State save failed for %s: %s", pid, exc)
            if info.get("data_store"):
                info["data_store"].stop_ingestion()
            for key in ("task", "ingest_task", "fanout_task"):
                t = info.get(key)
                if t:
                    t.cancel()
                    all_tasks.append(t)
        except Exception as exc:
            logger.error("Error shutting down agent %s: %s", pid, exc)
    if all_tasks:
        try:
            await asyncio.wait_for(
                asyncio.gather(*all_tasks, return_exceptions=True),
                timeout=15.0,
            )
        except asyncio.TimeoutError:
            logger.warning("Graceful shutdown timed out; force-cancelling remaining tasks")
            for t in all_tasks:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*all_tasks, return_exceptions=True)
    active_agents.clear()


async def _save_last_config(body: StartAgentRequest) -> None:
    cfg_path = settings.AGENT_LAST_CONFIG_PATH
    try:
        await asyncio.to_thread(
            atomic_write_json,
            cfg_path,
            {
                "user_id":  body.user_id,
                "llm_provider":    body.llm_provider,
                "model":           body.model or "",
                "granularity":     body.granularity,
                "speed_multiplier": body.speed_multiplier,
            },
        )
    except Exception as exc:
        logger.debug("Failed to save agent config: %s", exc)


async def start_system_ingestion(user_id: str = "LiveUser") -> None:
    """Start data ingestion for a user without starting the agent."""
    existing = system_ingest_tasks.get(user_id)
    if existing is not None and not existing.done():
        return
    if existing is not None:
        logger.warning("Previous ingestion task for %s is dead — restarting it", user_id)
        system_ingest_tasks.pop(user_id, None)

    data_store = await asyncio.to_thread(
        DataStore, db_path=settings.DATA_STORE_PATH, user_id=user_id,
    )

    # Create a dummy request body for _build_ingest_task
    body = StartAgentRequest(
        user_id=user_id,
        granularity="1hour",  # default
        speed_multiplier=1.0
    )

    logger.info("Starting system-level background ingestion for %s", user_id)
    await _build_ingest_task(body, data_store)


async def stop_all_ingestions() -> None:
    """Cleanup all ingestion tasks during shutdown."""
    for _pid, task in system_ingest_tasks.items():
        task.cancel()
    system_ingest_tasks.clear()


def _ensure_default_scheduled_tasks(memory: MemoryManager) -> None:
    """Insert default scheduled tasks and trigger rules if tables are empty."""
    try:
        import sqlite3
        with sqlite3.connect(str(memory.db_file), timeout=5) as conn:
            count = conn.execute("SELECT COUNT(*) FROM scheduled_tasks").fetchone()[0]
            if count == 0:
                conn.execute(
                    "INSERT INTO scheduled_tasks (cron_expr, prompt_goal, status) VALUES (?, ?, ?)",
                    ("0 10 * * *", "Analyse last night's sleep quality and morning readiness. Include heart rate, HRV, and recovery metrics.", "active"),
                )
                conn.commit()
                logger.info("Inserted default scheduled task (daily 10:00 sleep analysis)")
    except Exception as e:
        logger.warning("Failed to insert default scheduled tasks: %s", e)

    # Insert default trigger rules
    from ..agent.trigger_evaluator import insert_default_trigger_rules
    insert_default_trigger_rules(memory.db_file)
