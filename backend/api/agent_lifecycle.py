"""
Agent lifecycle management — start, stop, status, restore, ingestion, supervisor.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request

from ..agent import MemoryManager, create_provider
from ..agent.autonomous_agent import AutonomousHealthAgent
from ..agent.data_store import DataStore
from ..config import settings
from ..utils import ts_fmt
from .agent_state import (
    QuickAnalysisResponse,
    StartAgentRequest,
    _check_rate_limit,
    _client_ip,
    active_agents,
    startup_lock,
    system_ingest_tasks,
)

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
    "token_usage",       # per-turn token stats (cumulative is in status)
    "startup_progress",  # init progress steps (transient UI feedback)
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
    except Exception:
        pass
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
    _check_rate_limit(_client_ip(request))

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
        # Clean up the placeholder
        async with startup_lock:
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
    llm = create_provider(body.llm_provider, model=body.model, api_key=api_key, **kwargs)

    # 2. Data store (wearable health data, stored under data/data_stores)
    _step(2, 7, "Initialising health data store")
    data_store = DataStore(
        db_path=settings.DATA_STORE_PATH,
        user_id=body.user_id,
    )

    # 3. Memory manager (schema owner)
    _step(3, 7, "Initialising memory")
    memory = MemoryManager(settings.MEMORY_DB_PATH, body.user_id)

    # 4. Resolve messaging gateway registry (Telegram / Feishu / …)
    telegram_sender = None
    default_chat_id = None
    gateway_registry = None
    try:
        from ..main import get_gateway_registry, get_telegram_gateway
        gw = get_telegram_gateway()
        if gw:
            telegram_sender = gw.sender
            default_chat_id = getattr(settings, "chat_id", None)
        gateway_registry = get_gateway_registry()
    except Exception:
        pass

    # 5. Agent (ToolRegistry picks up the shared GatewayRegistry — which
    #    holds every enabled channel — and falls back to the legacy
    #    telegram_sender path if the registry is empty/unavailable.)
    _step(4, 7, "Building agent and tools")
    from ..agent.skills.registry import SkillRegistry
    from ..agent.tools.registry import ToolRegistry
    skill_registry = SkillRegistry(roots=AutonomousHealthAgent._resolve_skill_roots())
    registry = ToolRegistry.with_default_tools(
        data_store, settings.MEMORY_DB_PATH, body.user_id,
        telegram_sender=telegram_sender,
        default_chat_id=default_chat_id,
        gateway_registry=gateway_registry,
        skill_registry=skill_registry,
    )
    agent = AutonomousHealthAgent(
        user_id=body.user_id,
        llm_provider=llm,
        data_store=data_store,
        memory_db_path=settings.MEMORY_DB_PATH,
        tool_registry=registry,
    )

    # 6. Data ingestion stream (Check if system-level ingestion is already running)
    _step(5, 7, "Setting up data ingestion")
    if body.user_id in system_ingest_tasks:
        logger.info("Reusing existing system-level ingestion task for %s", body.user_id)
        ingest_task = system_ingest_tasks[body.user_id]
    else:
        ingest_task = await _build_ingest_task(body, data_store)
        system_ingest_tasks[body.user_id] = ingest_task

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
    """Build the live data-ingestion background task."""
    from pathlib import Path as _Path

    data_path = (_Path(__file__).parent.parent.parent / "ios" / "Server").resolve()

    from ..data_readers.watch_db_reader import WatchDBReader
    reader = WatchDBReader(data_path)
    return asyncio.create_task(
        _live_ingest_loop(reader, data_store, body.user_id),
        name=f"ingest-{body.user_id}",
    )


async def _live_ingest_loop(reader, data_store: DataStore, user_id: str) -> None:
    """
    Continuously poll the live watch.db and forward ALL samples into the DataStore.
    Performs full historical sync on startup, then lossless incremental polling.
    After each batch, evaluates trigger rules and queues analysis if conditions are met.
    """
    from ..agent.trigger_evaluator import TriggerEvaluator

    # 1. Initialize high-water marks from existing data in the store
    last_id = data_store.get_last_ingested_id()
    last_updated_at = data_store.get_last_updated_at()

    # Trigger evaluator — checks rules against newly ingested data
    trigger_eval = TriggerEvaluator(
        memory_db_path=settings.MEMORY_DB_PATH,
        user_id=user_id,
        health_db_path=data_store.db_file,
    )

    poll_interval = 5  # seconds
    logger.info("Live ingest loop started for %s. ID HWM: %d, updated_at HWM: %.0f",
                user_id, last_id, last_updated_at)
    data_store.is_ingesting = True

    def _build_records(samples: list, pid: str) -> list:
        """Convert raw watch.db rows to DataStore ingest format."""
        records = []
        for s in samples:
            ts = s["ts"]
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            records.append({
                "date": ts_fmt(dt),
                "value": s["value"],
                "feature_type": s["feature_type"],
                "pid": pid,
            })
        return records

    # --- PHASE 1: Historical Sync (id-based, catches all rows) ---
    try:
        logger.info("Starting historical back-fill for %s...", user_id)
        all_samples = await asyncio.to_thread(reader.get_all_samples_since_id, last_id)
        if all_samples:
            new_records = _build_records(all_samples, user_id)
            max_id = max(s["id"] for s in all_samples)
            max_ts = max(s["ts"] for s in all_samples)

            batch = {
                "data": new_records,
                "data_timestamp": ts_fmt(datetime.fromtimestamp(max_ts, tz=timezone.utc)),
                "num_records": len(new_records),
                "is_live": True,
            }
            await asyncio.to_thread(data_store.ingest_batch, batch)
            await asyncio.to_thread(data_store.save_ingestion_id, max_id)
            last_id = max_id

            # Seed updated_at HWM from the backfill if not set yet
            for s in all_samples:
                ua = s.get("updated_at", 0.0) or 0.0
                if ua > last_updated_at:
                    last_updated_at = ua
            if last_updated_at > 0:
                await asyncio.to_thread(data_store.save_last_updated_at, last_updated_at)

            logger.info("Historical back-fill complete: %d records for %s (id=%d, ua=%.0f)",
                        len(new_records), user_id, max_id, last_updated_at)
    except Exception as exc:
        logger.error("Historical back-fill error for %s: %s — skipping to incremental polling",
                      user_id, exc, exc_info=True)
        last_id = data_store.get_last_ingested_id()
        last_updated_at = data_store.get_last_updated_at()

    # --- PHASE 2: Incremental Polling (dual: id for new rows + updated_at for changed rows) ---
    try:
        while data_store.is_ingesting:
            all_records: list = []
            ingested_features: set = set()

            # 2a. New rows (id-based, catches brand-new inserts)
            try:
                new_samples = await asyncio.to_thread(reader.get_all_samples_since_id, last_id)
            except Exception as exc:
                logger.warning("Live ingest poll (new) error: %s", exc)
                new_samples = []

            if new_samples:
                records = _build_records(new_samples, user_id)
                all_records.extend(records)
                max_id_in_batch = max(s["id"] for s in new_samples)
                last_id = max_id_in_batch
                # Track updated_at from new rows too
                for s in new_samples:
                    ua = s.get("updated_at", 0.0) or 0.0
                    if ua > last_updated_at:
                        last_updated_at = ua

            # 2b. Updated rows (updated_at-based, catches in-place value changes)
            try:
                updated_samples = await asyncio.to_thread(
                    reader.get_samples_updated_since, last_updated_at
                )
            except Exception as exc:
                logger.warning("Live ingest poll (updated) error: %s", exc)
                updated_samples = []

            if updated_samples:
                # Filter out rows already captured by the id-based poll above
                new_ids = {s["id"] for s in new_samples} if new_samples else set()
                extra = [s for s in updated_samples if s["id"] not in new_ids]
                if extra:
                    records = _build_records(extra, user_id)
                    all_records.extend(records)
                # Advance updated_at HWM
                max_ua = max(s["updated_at"] for s in updated_samples)
                if max_ua > last_updated_at:
                    last_updated_at = max_ua

            # Ingest combined batch
            if all_records:
                ingested_features = {r["feature_type"] for r in all_records}
                max_ts_in_batch = 0.0
                for r in all_records:
                    try:
                        rdt = datetime.fromisoformat(r["date"])
                        rts = rdt.replace(tzinfo=timezone.utc).timestamp()
                        if rts > max_ts_in_batch:
                            max_ts_in_batch = rts
                    except Exception:
                        pass

                batch = {
                    "data": all_records,
                    "data_timestamp": ts_fmt(datetime.fromtimestamp(max_ts_in_batch, tz=timezone.utc)) if max_ts_in_batch > 0 else None,
                    "num_records": len(all_records),
                    "is_live": True,
                }

                await asyncio.to_thread(data_store.ingest_batch, batch)
                await asyncio.to_thread(data_store.save_ingestion_id, last_id)
                await asyncio.to_thread(data_store.save_last_updated_at, last_updated_at)
                logger.info("Live ingest: %d records synced (%d new, %d updated) hwm_id=%d ua=%.0f",
                            len(all_records),
                            len(new_samples) if new_samples else 0,
                            len(all_records) - (len(new_samples) if new_samples else 0),
                            last_id, last_updated_at)

                # Evaluate trigger rules against newly ingested data
                agent_queue = _get_agent_analysis_queue(user_id)
                try:
                    triggered = await trigger_eval.evaluate_after_ingest(
                        agent_queue=agent_queue,
                        ingested_features=ingested_features,
                    )
                    if triggered:
                        logger.info(
                            "Triggers fired: %s",
                            [t["name"] for t in triggered],
                        )
                except Exception as exc:
                    logger.debug("Trigger evaluation error: %s", exc)

            await asyncio.sleep(poll_interval)

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
                    asyncio.create_task(memory.persist_activity(event))
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

@lifecycle_router.post("/stop")
async def stop_autonomous_agent(request: Request, user_id: str = Query("LiveUser")):
    """Stop the running agent. Defaults to LiveUser (single-user mode)."""
    _check_rate_limit(_client_ip(request))

    async with startup_lock:
        info = active_agents.pop(user_id, None)
        if not info:
            raise HTTPException(status_code=404, detail=f"No agent running for '{user_id}'")

        try:
            if info.get("agent"):
                info["agent"].stop()

            for key in ("task",):  # removed ingest_task from auto-cancel
                t: asyncio.Task | None = info.get(key)
                if t is None:
                    continue
                t.cancel()
                try:
                    await asyncio.wait_for(asyncio.shield(t), timeout=5.0)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    pass

            logger.info("Stopped autonomous agent for %s (Ingestion continues)", user_id)
            return {"success": True, "user_id": user_id}

        except Exception as exc:
            logger.error("Error stopping agent: %s", exc, exc_info=True)
            raise HTTPException(status_code=500, detail=str(exc)) from exc


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

@lifecycle_router.post("/quick-analysis")
async def quick_analysis():
    """
    Trigger a rapid health status analysis (max 3 tool calls, 30s timeout).
    Returns {state: CatState, message: str} for iOS cat animation.
    """
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
        if not pid:
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
        if info.get("agent"):
            info["agent"].stop()
        t = info.get("task")
        if t:
            t.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(t), timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass

    await try_restore_agent()
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
            if info.get("data_store"):
                info["data_store"].stop_ingestion()
            for key in ("task", "ingest_task"):
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
        def _write_config():
            cfg_path.parent.mkdir(parents=True, exist_ok=True)
            with open(cfg_path, "w") as f:
                json.dump(
                    {
                        "user_id":  body.user_id,
                        "llm_provider":    body.llm_provider,
                        "model":           body.model or "",
                        "granularity":     body.granularity,
                        "speed_multiplier": body.speed_multiplier,
                    },
                    f,
                    indent=2,
                )
        await asyncio.to_thread(_write_config)
    except Exception as exc:
        logger.debug("Failed to save agent config: %s", exc)


async def start_system_ingestion(user_id: str = "LiveUser") -> None:
    """Start data ingestion for a user without starting the agent."""
    if user_id in system_ingest_tasks:
        return

    data_store = DataStore(
        db_path=settings.DATA_STORE_PATH,
        user_id=user_id,
    )

    # Create a dummy request body for _build_ingest_task
    body = StartAgentRequest(
        user_id=user_id,
        granularity="1hour",  # default
        speed_multiplier=1.0
    )

    logger.info("Starting system-level background ingestion for %s", user_id)
    task = await _build_ingest_task(body, data_store)
    system_ingest_tasks[user_id] = task


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
