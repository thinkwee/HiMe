"""
Shared state and helpers for the agent API sub-modules.

This module owns all mutable module-level state so that
``agent_lifecycle``, ``agent_diagnostics``, and ``agent_tasks``
can import from a single source without circular dependencies.
"""
from __future__ import annotations

import asyncio
import re
import time
from collections import defaultdict

from fastapi import HTTPException, Request
from pydantic import BaseModel, field_validator

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
            # Each proxy appends the address it saw to the RIGHT of the list;
            # everything to the left is client-supplied and spoofable. Our
            # (single, trusted) proxy's entry is therefore the rightmost one.
            parts = [p.strip() for p in forwarded.split(",") if p.strip()]
            if parts:
                return parts[-1]
    return request.client.host if request.client else "unknown"


# ---------------------------------------------------------------------------
# Identifier validation (user_id / pid end up in file names under memory/ and
# data/data_stores/, so they must never contain path separators or dots).
# ---------------------------------------------------------------------------

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def validate_user_id(value: str) -> str:
    """Return *value* if it is a safe identifier, else raise HTTP 400."""
    if not isinstance(value, str) or not _ID_RE.match(value):
        raise HTTPException(
            status_code=400,
            detail="Invalid user id: use 1-64 letters, digits, '_' or '-'.",
        )
    return value


async def require_valid_ids(request: Request) -> None:
    """Router dependency: validate every ``pid`` / ``user_id`` path or query value."""
    for key in ("pid", "user_id"):
        val = request.path_params.get(key)
        if val is None:
            val = request.query_params.get(key)
        if val is not None:
            validate_user_id(val)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class StartAgentRequest(BaseModel):
    user_id:  str   = "LiveUser"
    llm_provider:    str   = "gemini"
    model:           str | None  = None
    granularity:     str   = "real-time"
    speed_multiplier: float = 1.0

    @field_validator("user_id")
    @classmethod
    def _check_user_id(cls, v: str) -> str:
        if not _ID_RE.match(v):
            raise ValueError("user_id must be 1-64 letters, digits, '_' or '-'")
        return v


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


class ChatStopRequest(BaseModel):
    """Request body for POST /api/agent/chat/stop (all fields optional)."""
    user_id: str | None = None


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
    """Return existing MemoryManager from registry or create a transient one.

    While the agent is still starting its registry entry is a placeholder with
    ``memory=None``; in that case fall through to the on-disk DB so readers
    (chat history, reports, ...) keep working instead of seeing nothing.
    """
    info = active_agents.get(pid)
    if info is not None and info.get("memory") is not None:
        return info["memory"]
    # Only create a transient MemoryManager if the DB file actually exists,
    # otherwise every poll creates one and logs "MemoryManager ready".
    if not _ID_RE.match(pid or ""):
        return None
    db_file = settings.MEMORY_DB_PATH / f"{pid}.db"
    if not db_file.exists():
        return None
    return MemoryManager(settings.MEMORY_DB_PATH, pid)


async def aget_or_create_memory(pid: str) -> MemoryManager | None:
    """Async variant of :func:`_get_or_create_memory`.

    Constructing a transient ``MemoryManager`` opens SQLite and runs schema
    migrations, so it is done off the event loop.
    """
    info = active_agents.get(pid)
    if info is not None and info.get("memory") is not None:
        return info["memory"]
    return await asyncio.to_thread(_get_or_create_memory, pid)


def get_active_agent(user_id: str):
    """Return the AutonomousHealthAgent for *user_id*, or None."""
    info = active_agents.get(user_id)
    return info["agent"] if info else None
