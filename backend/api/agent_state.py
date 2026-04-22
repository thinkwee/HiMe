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
_RATE_LIMIT_MAX_CALLS = 5
_rate_buckets: dict[str, list[float]] = defaultdict(list)


def _check_rate_limit(client_ip: str) -> None:
    """Raise 429 if the client IP has exceeded the mutation rate limit."""
    now = time.monotonic()
    bucket = _rate_buckets[client_ip]
    # Prune old timestamps
    _rate_buckets[client_ip] = [t for t in bucket if now - t < _RATE_LIMIT_WINDOW_S]
    if len(_rate_buckets[client_ip]) >= _RATE_LIMIT_MAX_CALLS:
        raise HTTPException(
            status_code=429,
            detail=(
                f"Rate limit exceeded: max {_RATE_LIMIT_MAX_CALLS} calls "
                f"per {_RATE_LIMIT_WINDOW_S}s per IP."
            ),
        )
    _rate_buckets[client_ip].append(now)


def _client_ip(request) -> str:
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
