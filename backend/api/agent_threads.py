"""In-app chat thread routes (``/api/agent/chat/threads``).

Single-user (LiveUser). The permanent ``main`` thread receives every proactive
message and cannot be renamed, archived or deleted.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from ..ios_gateway.threads import MAIN_THREAD_ID, history_key_for, is_valid_thread_id
from .agent_state import (
    _RATE_LIMIT_CHAT_MAX_CALLS,
    _check_rate_limit,
    _client_ip,
    active_agents,
    aget_or_create_memory,
)

_LIVE_USER = "LiveUser"
logger = logging.getLogger(__name__)

threads_router = APIRouter()


class CreateThreadRequest(BaseModel):
    title: str | None = None


class PatchThreadRequest(BaseModel):
    title: str | None = None
    archived: bool | None = None
    pinned: bool | None = None


async def emit_thread_event(event: dict) -> None:
    """Best-effort: announce a thread change on the agent stream."""
    info = active_agents.get(_LIVE_USER)
    agent = info.get("agent") if info else None
    if agent is None:
        return
    try:
        await agent._emit(event)
    except Exception as exc:  # pragma: no cover — defensive
        logger.debug("thread event emit failed: %s", exc)


def check_thread_id(thread_id: str | None) -> str:
    """Return a validated thread id (default main) or raise 400."""
    tid = thread_id or MAIN_THREAD_ID
    if not is_valid_thread_id(tid):
        raise HTTPException(status_code=400, detail="Invalid thread_id")
    return tid


async def get_thread_memory():
    """Memory DB handle; created on demand so threads work before agent start."""
    memory = await aget_or_create_memory(_LIVE_USER)
    if memory is None:
        from ..agent import MemoryManager
        from . import agent_state
        try:
            memory = await asyncio.to_thread(
                MemoryManager, agent_state.settings.MEMORY_DB_PATH, _LIVE_USER,
            )
        except Exception as exc:
            logger.warning("thread memory unavailable: %s", exc)
            raise HTTPException(status_code=503, detail="Memory unavailable") from exc
    return memory


@threads_router.get("/chat/threads")
async def list_threads(include_archived: bool = Query(False)):
    memory = await get_thread_memory()
    threads = await asyncio.to_thread(memory.list_chat_threads, include_archived)
    return {"success": True, "threads": threads}


@threads_router.post("/chat/threads")
async def create_thread(request: Request, body: CreateThreadRequest | None = None):
    _check_rate_limit(_client_ip(request), "chat", _RATE_LIMIT_CHAT_MAX_CALLS)
    memory = await get_thread_memory()
    thread = await asyncio.to_thread(
        memory.create_chat_thread, (body.title if body else None) or ""
    )
    await emit_thread_event({"type": "chat_thread_updated", "thread": thread})
    return {"success": True, "thread": thread}


@threads_router.patch("/chat/threads/{thread_id}")
async def patch_thread(request: Request, thread_id: str, body: PatchThreadRequest):
    _check_rate_limit(_client_ip(request), "chat", _RATE_LIMIT_CHAT_MAX_CALLS)
    tid = check_thread_id(thread_id)
    if tid == MAIN_THREAD_ID and (body.archived or body.title is not None):
        raise HTTPException(status_code=400, detail="The main thread cannot be renamed or archived")
    memory = await get_thread_memory()
    if await asyncio.to_thread(memory.get_chat_thread, tid) is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    thread = await asyncio.to_thread(
        lambda: memory.update_chat_thread(
            tid, title=body.title, pinned=body.pinned, archived=body.archived,
        )
    )
    await emit_thread_event({"type": "chat_thread_updated", "thread": thread})
    return {"success": True, "thread": thread}


@threads_router.delete("/chat/threads/{thread_id}")
async def delete_thread(request: Request, thread_id: str):
    _check_rate_limit(_client_ip(request), "chat", _RATE_LIMIT_CHAT_MAX_CALLS)
    tid = check_thread_id(thread_id)
    if tid == MAIN_THREAD_ID:
        raise HTTPException(status_code=400, detail="The main thread cannot be deleted")
    memory = await get_thread_memory()
    # Stop a run in flight for this thread, then drop its in-memory context.
    info = active_agents.get(_LIVE_USER)
    agent = info.get("agent") if info else None
    if agent is not None:
        agent.stop_chat(tid)
        agent._chat_histories.pop(history_key_for(_LIVE_USER, tid), None)
        agent._save_state()
    if not await asyncio.to_thread(memory.delete_chat_thread, tid):
        raise HTTPException(status_code=404, detail="Thread not found")
    await emit_thread_event({"type": "chat_thread_deleted", "thread_id": tid})
    return {"success": True}
