import asyncio
import signal
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .agent.retention import retention_loop
from .api.agent_routes import (
    get_active_agents_dict,
    get_memory_manager_for,
    shutdown_agents,
    start_system_ingestion,
    stop_all_ingestions,
    try_restore_agent,
)
from .api.agent_routes import (
    router as agent_router,
)
from .api.config_routes import (
    load_app_state,
    save_app_state,
)
from .api.config_routes import (
    router as config_router,
)
from .api.data_routes import router as data_router
from .api.page_routes import router as page_router
from .api.prompt_routes import router as prompt_router
from .api.skill_routes import router as skill_router
from .api.stream_routes import router as stream_router
from .config import settings
from .logging_config import setup_logging
from .services.streaming_service import shutdown_executor

# Setup logging (stdout only — start.sh redirects to logs/backend.log)
logger = setup_logging()

# Module-level reference so routes / tools can access the gateway
_telegram_gateway = None
_feishu_gateway = None

# Shared GatewayRegistry — populated at startup with every enabled gateway
# (Telegram, Feishu, ...). Tools like reply_user / push_report route
# outbound messages through this registry.
from .messaging.registry import GatewayRegistry  # noqa: E402

_gateway_registry: GatewayRegistry = GatewayRegistry()


def get_telegram_gateway():
    """Return the active TelegramGateway instance (or None)."""
    return _telegram_gateway


def get_feishu_gateway():
    """Return the active FeishuGateway instance (or None)."""
    return _feishu_gateway


def get_gateway_registry() -> GatewayRegistry:
    """Return the shared GatewayRegistry (always non-None)."""
    return _gateway_registry


def _parse_iso(ts: str | None) -> datetime | None:
    """Parse an ISO timestamp stored in the memory DB. Returns None on failure."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None


def _claim_due_tasks(db_file: str, now: datetime) -> list[tuple[int, str, datetime]]:
    """
    Atomically find and claim scheduled tasks whose next cron tick has passed.

    For each active task, computes the next fire time from `last_run_at`
    (or `created_at` if the task has never fired). If that tick is <= now,
    the task is claimed by writing the tick back to `last_run_at` inside the
    same transaction, and returned to the caller for execution.

    Catch-up semantics: if the scheduler was down and multiple ticks were
    missed, we skip forward to the most recent missed tick and fire exactly
    once — no replay storms.

    Returns: list of (task_id, prompt_goal, fired_tick) for tasks to execute.
    """
    import sqlite3 as _sqlite3

    import croniter as _croniter

    due: list[tuple[int, str, datetime]] = []
    with _sqlite3.connect(db_file, timeout=5, isolation_level=None) as conn:
        conn.row_factory = _sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        try:
            rows = conn.execute(
                "SELECT id, cron_expr, prompt_goal, last_run_at, created_at"
                " FROM scheduled_tasks WHERE status='active'"
            ).fetchall()

            for row in rows:
                anchor = _parse_iso(row["last_run_at"]) or _parse_iso(row["created_at"]) or now
                try:
                    itr = _croniter.croniter(row["cron_expr"], anchor)
                    next_tick = itr.get_next(datetime)
                except Exception as e:
                    logger.warning("Scheduler: bad cron_expr for task %d: %s", row["id"], e)
                    continue

                if next_tick > now:
                    continue  # not yet due

                # Catch up: advance to the most recent missed tick so we fire
                # only once even if the scheduler was down for a long time.
                fired_tick = next_tick
                while True:
                    peek = itr.get_next(datetime)
                    if peek > now:
                        break
                    fired_tick = peek

                conn.execute(
                    "UPDATE scheduled_tasks SET last_run_at = ? WHERE id = ?",
                    (fired_tick.isoformat(), row["id"]),
                )
                due.append((row["id"], row["prompt_goal"], fired_tick))

            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return due


async def _run_cron_scheduler():
    """
    Poll the memory DB and dispatch scheduled analysis tasks.

    Design: state-machine driven. Each task's `last_run_at` stores the
    scheduled cron tick it last executed for (not wall-clock time). On each
    poll, we ask croniter "what's the next tick after last_run_at?" and fire
    iff that tick is in the past. After firing we advance `last_run_at` to
    that tick — which makes the whole loop idempotent, drift-free, and
    crash-safe without any magic windows.
    """
    try:
        import croniter as _croniter  # noqa: F401  (imported for availability check)
    except ImportError:
        logger.warning("croniter not installed — scheduled_tasks will not run. pip install croniter")
        return

    logger.info("Cron scheduler started")
    while True:
        try:
            now = datetime.now().replace(microsecond=0)
            for pid, info in get_active_agents_dict().items():
                memory = get_memory_manager_for(pid)
                if not memory:
                    continue
                try:
                    due = await asyncio.to_thread(_claim_due_tasks, str(memory.db_file), now)
                except Exception as e:
                    logger.warning("Cron scheduler: DB error for %s: %s", pid, e)
                    continue

                agent = info["agent"]
                for task_id, goal, fired_tick in due:
                    logger.info(
                        "Scheduler: firing task %d for %s at tick %s: %s",
                        task_id, pid, fired_tick.isoformat(), (goal or "")[:60],
                    )
                    try:
                        await agent.run_scheduled_analysis(goal)
                    except Exception as e:
                        logger.error("Scheduler: failed to enqueue task %d: %s", task_id, e)
        except Exception as e:
            logger.error("Scheduler loop error: %s", e)

        # Align to the next minute boundary.
        now = datetime.now()
        next_minute = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
        await asyncio.sleep(max(0.1, (next_minute - now).total_seconds()))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for startup and shutdown events.
    Guarantees cleanup of resources.
    """
    global _telegram_gateway, _feishu_gateway

    # --- Startup ---
    logger.info("[Startup] 1/4 Application starting up...")

    # Validate LLM API keys early so operators see a clear warning
    settings.validate_llm_keys()

    # Ensure required directories exist
    logger.info("[Startup] 2/4 Creating directories (memory, logs)...")
    settings.MEMORY_DB_PATH.mkdir(parents=True, exist_ok=True)
    settings.AGENT_LOGS_PATH.mkdir(parents=True, exist_ok=True)
    (settings.MEMORY_DB_PATH / "agent_states").mkdir(parents=True, exist_ok=True)
    (settings.MEMORY_DB_PATH / "data_stores").mkdir(parents=True, exist_ok=True)

    # Load general app state
    await asyncio.to_thread(load_app_state)

    # Start Telegram Gateway if enabled (MUST start before agent restore so agents get the sender)
    logger.info("[Startup] 3/4 Starting Telegram Gateway (if enabled)...")
    if settings.TELEGRAM_GATEWAY_ENABLED and settings.TELEGRAM_TOKEN:
        try:
            from .telegram import TelegramGateway

            # Build chat-ID whitelist. The gateway DENIES any inbound
            # message from a chat not on this list (default-deny). If both
            # CHAT_ID and TELEGRAM_ALLOWED_CHAT_IDS are empty, no chats are
            # allowed to interact with the agent — the gateway will still
            # start (so outbound notifications work) but will reject all
            # incoming user messages with a warning log.
            allowed: set = set()
            if settings.CHAT_ID:
                allowed.add(settings.CHAT_ID)
            if settings.TELEGRAM_ALLOWED_CHAT_IDS:
                for cid in settings.TELEGRAM_ALLOWED_CHAT_IDS.split(","):
                    cid = cid.strip()
                    if cid:
                        allowed.add(cid)
            if not allowed:
                logger.warning(
                    "Telegram Gateway: no allowed chat IDs configured "
                    "(neither CHAT_ID nor TELEGRAM_ALLOWED_CHAT_IDS). "
                    "Inbound messages will be rejected. Set CHAT_ID in .env "
                    "to allow your own chat."
                )

            # Callback that pushes user messages to the active agent's inbox
            async def _on_user_message(envelope):
                """Route free-form user messages from any gateway to the running agent's InboxQueue."""
                agents = get_active_agents_dict()
                if not agents:
                    logger.info("on_user_message: no active agent — message ignored")
                    return
                # Push message to the first (and only) active agent's inbox
                pid, info = next(iter(agents.items()))
                agent = info["agent"]
                await agent.inbox.push(envelope)
                logger.info("on_user_message: routed to agent %s", pid)

            _telegram_gateway = TelegramGateway(
                token=settings.TELEGRAM_TOKEN,
                default_chat_id=settings.CHAT_ID,
                poll_timeout=settings.TELEGRAM_POLL_TIMEOUT,
                # Pass the (possibly empty) set explicitly so the poller
                # default-denies messages from unknown chats. Passing None
                # would disable the whitelist entirely.
                allowed_chat_ids=allowed,
                get_active_agents=get_active_agents_dict,
                get_memory_manager=get_memory_manager_for,
                on_user_message=_on_user_message,
            )
            await _telegram_gateway.start()
            _gateway_registry.register(_telegram_gateway)
            logger.info("Telegram Gateway started")
        except Exception as e:
            logger.warning(f"Telegram Gateway start failed: {e}", exc_info=True)
            _telegram_gateway = None
    elif settings.TELEGRAM_GATEWAY_ENABLED and not settings.TELEGRAM_TOKEN:
        logger.warning(
            "TELEGRAM_GATEWAY_ENABLED=true but TELEGRAM_TOKEN is not set — "
            "gateway will not start."
        )

    # Start Feishu Gateway if enabled — shares the same _on_user_message
    # callback so inbound chat from both platforms lands in the same
    # agent inbox. Both gateways are registered in the shared
    # GatewayRegistry so outbound tools (reply_user, push_report) can
    # route messages back on the matching channel.
    if settings.FEISHU_GATEWAY_ENABLED and settings.FEISHU_APP_ID and settings.FEISHU_APP_SECRET:
        try:
            from .feishu import FeishuGateway

            async def _on_feishu_user_message(envelope):
                """Route free-form Feishu messages to the active agent inbox."""
                agents = get_active_agents_dict()
                if not agents:
                    logger.info("Feishu on_user_message: no active agent — message ignored")
                    return
                pid, info = next(iter(agents.items()))
                agent = info["agent"]
                await agent.inbox.push(envelope)
                logger.info("Feishu on_user_message: routed to agent %s", pid)

            _feishu_gateway = FeishuGateway(
                settings=settings,
                on_user_message=_on_feishu_user_message,
                get_memory_manager=get_memory_manager_for,
            )
            # Webhook transport needs to mount its route on the FastAPI app.
            try:
                _feishu_gateway.register_routes(app)
            except Exception as route_exc:
                logger.warning("Feishu webhook route registration skipped: %s", route_exc)
            await _feishu_gateway.start()
            _gateway_registry.register(_feishu_gateway)
            logger.info(
                "Feishu Gateway started (transport=%s)", settings.FEISHU_TRANSPORT,
            )
        except Exception as e:
            logger.warning(f"Feishu Gateway start failed: {e}", exc_info=True)
            _feishu_gateway = None
    elif settings.FEISHU_GATEWAY_ENABLED:
        logger.warning(
            "FEISHU_GATEWAY_ENABLED=true but FEISHU_APP_ID / FEISHU_APP_SECRET "
            "are not set — gateway will not start."
        )

    # --- Live Background Ingestion (Phase 1) ---
    # Automatically start syncing data from watch.db to LiveUser_data.db
    # even if no agent is running.
    if settings.DATA_SOURCE == "live":
        logger.info("[Startup] Starting background data synchronization...")
        await start_system_ingestion("LiveUser")

    # Restore last agent if configured (can be slow: loads data, builds dataset)
    logger.info("[Startup] 4/4 Restoring agent (if AUTO_RESTORE_AGENT)...")
    try:
        await try_restore_agent()
    except Exception as e:
        logger.warning(f"Startup restore failed: {e}")

    # Start cron scheduler for scheduled_tasks
    logger.info("[Startup] Starting cron scheduler...")
    _scheduler_task = asyncio.create_task(_run_cron_scheduler(), name="cron_scheduler")
    _scheduler_task.add_done_callback(
        lambda t: logger.error("Cron scheduler crashed: %s", t.exception(), exc_info=t.exception())
        if not t.cancelled() and t.exception() else None
    )

    # Start the daily data-retention sweep. This enforces the rolling
    # DATA_RETENTION_DAYS window across health stores, watch.db and the
    # agent memory DBs. Runs one sweep immediately on startup, then
    # every 24 hours thereafter.
    logger.info(
        "[Startup] Starting retention loop (retention=%d days)...",
        settings.DATA_RETENTION_DAYS,
    )
    _retention_task = asyncio.create_task(retention_loop(None), name="retention_loop")
    _retention_task.add_done_callback(
        lambda t: logger.error("Retention loop crashed: %s", t.exception(), exc_info=t.exception())
        if not t.cancelled() and t.exception() else None
    )

    logger.info("[Startup] All services ready — binding HTTP server on port %s", settings.API_PORT)
    yield

    # --- Graceful Shutdown ---
    # Register signal handlers for emergency state persistence

    def _emergency_save_handler(signum, frame):
        """Best-effort state save on SIGTERM/SIGINT before process dies."""
        logger.warning("Received signal %d — initiating graceful shutdown", signum)
        agents = get_active_agents_dict()
        for pid, info in agents.items():
            agent = info.get("agent")
            if agent and hasattr(agent, "_save_state"):
                try:
                    agent._save_state()
                    logger.info("Emergency state saved for agent %s", pid)
                except Exception as e:
                    logger.error("Emergency state save failed for %s: %s", pid, e)

    # Install signal handlers (non-blocking — actual shutdown is via lifespan)
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _emergency_save_handler)
        except (OSError, ValueError):
            pass  # May fail in non-main thread

    # --- Shutdown ---
    logger.info("Application shutting down...")

    # Cancel the cron scheduler task
    _scheduler_task.cancel()
    try:
        await _scheduler_task
    except asyncio.CancelledError:
        pass

    # Cancel the retention loop
    _retention_task.cancel()
    try:
        await _retention_task
    except asyncio.CancelledError:
        pass

    # Stop all background ingestions
    await stop_all_ingestions()

    # Save general app state
    await asyncio.to_thread(save_app_state)

    # 1. Stop Telegram Gateway
    if _telegram_gateway:
        try:
            await _telegram_gateway.stop()
        except Exception as e:
            logger.warning(f"Telegram Gateway stop error: {e}")
        _telegram_gateway = None

    # 1b. Stop Feishu Gateway
    if _feishu_gateway:
        try:
            await _feishu_gateway.stop()
        except Exception as e:
            logger.warning(f"Feishu Gateway stop error: {e}")
        _feishu_gateway = None

    # Clear the shared registry so any lingering tool handles don't try to
    # dispatch through a torn-down gateway during shutdown.
    _gateway_registry.clear()

    # 2. Stop all agents (triggers cancellation tokens + state save)
    await shutdown_agents()

    # 3. Stop global thread pools
    shutdown_executor()

    logger.info("Shutdown complete.")

# Create FastAPI app with lifespan
app = FastAPI(
    title="Wearable Data Analysis Platform",
    description="AI-powered analysis of wearable health data with LLM agents",
    version="1.0.0",
    lifespan=lifespan
)

# Add CORS middleware. We allow only the explicit origins configured in
# settings.CORS_ORIGINS (default: localhost dev hosts). Methods and headers
# are restricted to what the SPA actually needs.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "X-Requested-With"],
)


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """
    Optional bearer-token authentication for /api/* routes.

    Activated only when ``settings.API_AUTH_TOKEN`` is set. When unset
    (the default for local development), this middleware is a no-op so
    the FastAPI app behaves exactly as before.

    Public exemptions:
      - GET /              (root info)
      - GET /health        (health check)
      - OPTIONS /*         (CORS preflight)

    WebSocket connections accept the token via either the
    ``Authorization`` header or a ``?token=...`` query string parameter,
    since browsers can't set custom headers on WebSocket handshakes.
    """

    _PUBLIC_PATHS = {"/", "/health"}

    async def dispatch(self, request: Request, call_next):
        token = settings.API_AUTH_TOKEN
        if not token:
            return await call_next(request)

        path = request.url.path
        if request.method == "OPTIONS" or path in self._PUBLIC_PATHS:
            return await call_next(request)
        # Only guard the API surface; static / docs are left alone.
        if not (path.startswith("/api/") or path.startswith("/ws/")):
            return await call_next(request)

        # WebSocket upgrade handshakes go through HTTP middleware too.
        # Accept the token from the query string for browser compatibility.
        provided: str | None = None
        auth_header = request.headers.get("authorization")
        if auth_header and auth_header.lower().startswith("bearer "):
            provided = auth_header.split(" ", 1)[1].strip()
        if provided is None:
            provided = request.query_params.get("token")

        if provided != token:
            return JSONResponse(
                status_code=401,
                content={"detail": "Unauthorized"},
                headers={"WWW-Authenticate": "Bearer"},
            )
        return await call_next(request)


app.add_middleware(BearerAuthMiddleware)

# Include routers
app.include_router(config_router)
app.include_router(data_router)
app.include_router(agent_router)
app.include_router(page_router)
app.include_router(stream_router)
app.include_router(prompt_router)
app.include_router(skill_router)

@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "name": "Wearable Data Analysis Platform",
        "version": "1.0.0",
        "status": "running"
    }


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "data_source": "live",
    }


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """Global exception handler."""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "detail": str(exc)
        }
    )


if __name__ == "__main__":
    import uvicorn

    logger.info(f"Starting server on {settings.API_HOST}:{settings.API_PORT}")

    import os
    enable_reload = os.environ.get("HIME_DEV_RELOAD", "").lower() in ("1", "true", "yes")
    uvicorn.run(
        "backend.main:app",
        host=settings.API_HOST,
        port=settings.API_PORT,
        reload=enable_reload,
        reload_excludes=["data/**", "memory/**", "logs/**", "prompts/**", "*.db", "*.sqlite"],
        log_level="info"
    )
