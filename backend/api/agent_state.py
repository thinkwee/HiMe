"""
Shared state and helpers for the agent API sub-modules.

This module owns all mutable module-level state so that
``agent_lifecycle``, ``agent_diagnostics``, and ``agent_tasks``
can import from a single source without circular dependencies.
"""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict

from fastapi import HTTPException
from pydantic import BaseModel

from ..agent import MemoryManager
from ..config import settings

# ---------------------------------------------------------------------------
# In-memory agent registry
# {user_id: {agent, task, data_store, ingest_task, event_queue, config, memory}}
# ---------------------------------------------------------------------------
active_agents: dict[str, dict] = {}
system_ingest_tasks: dict[str, asyncio.Task] = {}
startup_lock = asyncio.Lock()

# ---------------------------------------------------------------------------
# Rate limiter (simple token-bucket, in-process)
# ---------------------------------------------------------------------------
_RATE_LIMIT_WINDOW_S = 60
# Per-endpoint budgets. Lifecycle mutations (/start, /stop) stay tight; chat is
# a normal interactive action and must not share the lifecycle bucket, or a
# chatty user locks themselves out of starting/stopping the agent.
_RATE_LIMIT_MAX_CALLS = 5           # default (lifecycle endpoints)
_RATE_LIMIT_CHAT_MAX_CALLS = 60     # /chat — one message per second sustained
# Keyed by (client_ip, endpoint) so the budgets are genuinely independent.
_rate_buckets: dict[tuple[str, str], list[float]] = defaultdict(list)
# Hard cap on the number of tracked keys so a forged-IP flood can't grow the
# dict without bound.
_RATE_BUCKETS_MAX_KEYS = 10_000


def _check_rate_limit(client_ip: str, endpoint: str = "default",
                      max_calls: int = _RATE_LIMIT_MAX_CALLS) -> None:
    """Raise 429 if *client_ip* exceeded the budget for *endpoint*."""
    now = time.monotonic()
    key = (client_ip, endpoint)
    bucket = [t for t in _rate_buckets[key] if now - t < _RATE_LIMIT_WINDOW_S]
    if len(bucket) >= max_calls:
        _rate_buckets[key] = bucket
        raise HTTPException(
            status_code=429,
            detail=(
                f"Rate limit exceeded: max {max_calls} calls "
                f"per {_RATE_LIMIT_WINDOW_S}s per IP for this endpoint."
            ),
        )
    bucket.append(now)
    _rate_buckets[key] = bucket
    _evict_stale_buckets(now)


def _evict_stale_buckets(now: float) -> None:
    """Drop buckets whose timestamps have all aged out of the window."""
    if len(_rate_buckets) <= _RATE_BUCKETS_MAX_KEYS // 2:
        return
    for k in [k for k, v in _rate_buckets.items()
              if not v or now - v[-1] >= _RATE_LIMIT_WINDOW_S]:
        _rate_buckets.pop(k, None)
    if len(_rate_buckets) > _RATE_BUCKETS_MAX_KEYS:
        # Still oversized (a burst inside one window) — start over rather than
        # let a forged-IP flood consume unbounded memory.
        _rate_buckets.clear()


def _client_ip(request) -> str:
    """Best-effort client IP.

    ``X-Forwarded-For`` is attacker-controlled unless a reverse proxy rewrites
    it, so it is only honoured when the operator opts in via
    ``TRUST_PROXY_HEADERS``. Otherwise a direct caller could rotate the header
    per request and defeat the rate limiter.
    """
    if settings.TRUST_PROXY_HEADERS:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class StartAgentRequest(BaseModel):
    user_id:  str   = "LiveUser"
    llm_provider:    str   = "gemini"
    model:           str | None  = None
    granularity:     str   = "real-time"
    speed_multiplier: float = 1.0


class QuickAnalysisResponse(BaseModel):
    state: str
    message: str


class ChatMessageRequest(BaseModel):
    """Request body for POST /api/agent/chat (in-app iOS chat).

    Single-user mode: the conversation is always the local ``LiveUser`` — the
    body carries no identity, only the message payload.
    """
    text: str = ""
    client_msg_id: str | None = None
    # Optional inbound image (only honoured when IOS_VISION_ENABLED).
    image_base64: str | None = None
    image_mime: str | None = None


# ---------------------------------------------------------------------------
# Memory helpers
# ---------------------------------------------------------------------------

def get_active_agents_dict() -> dict[str, dict]:
    """Public accessor for the active agents registry (read-only use)."""
    return active_agents


def get_memory_manager_for(pid: str) -> MemoryManager | None:
    """Return MemoryManager for *pid* if it exists (from active agent or disk)."""
    return _get_or_create_memory(pid)


def _get_or_create_memory(pid: str) -> MemoryManager | None:
    """Return existing MemoryManager from registry or create a transient one."""
    if pid in active_agents:
        return active_agents[pid]["memory"]
    # Only create a transient MemoryManager if the DB file actually exists,
    # otherwise every poll creates one and logs "MemoryManager ready".
    db_file = settings.MEMORY_DB_PATH / f"{pid}.db"
    if not db_file.exists():
        return None
    return MemoryManager(settings.MEMORY_DB_PATH, pid)


def get_active_agent(user_id: str):
    """Return the AutonomousHealthAgent for *user_id*, or None."""
    info = active_agents.get(user_id)
    return info["agent"] if info else None
