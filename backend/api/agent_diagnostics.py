"""
Agent diagnostics — activity log, memory inspection, tool listing, messaging-gateway info.
"""
from __future__ import annotations

import asyncio
import logging
import mimetypes
import os
import sqlite3

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..config import settings
from .agent_state import _get_or_create_memory, active_agents

logger = logging.getLogger(__name__)

diagnostics_router = APIRouter()

# Single-user identity for the in-app channel.
_LIVE_USER = "LiveUser"


# ---------------------------------------------------------------------------
# GET /evidence/{message_hash}  — fact-verification "Show Evidence" (iOS)
# ---------------------------------------------------------------------------

@diagnostics_router.get("/evidence/{message_hash}")
async def get_message_evidence(message_hash: str):
    """Return the fact-verification evidence trail for a delivered message.

    Backs the in-app "Show Evidence" affordance. The evidence is the same
    trail Telegram/Feishu surface via their inline button — here it is a
    plain authed fetch keyed by the ``message_hash`` carried on the
    ``chat_reply`` stream event.
    """
    def _load():
        from ..agent.fact_verifier import FactVerifier
        fv = FactVerifier(settings.MEMORY_DB_PATH, _LIVE_USER)
        ev = fv.get_evidence(message_hash)
        if not ev:
            return None, ""
        return ev, fv.format_evidence_for_display(ev)

    evidence, formatted = await asyncio.to_thread(_load)
    if not evidence:
        return {"success": True, "found": False, "evidence": None, "formatted": ""}
    return {"success": True, "found": True, "evidence": evidence, "formatted": formatted}


# ---------------------------------------------------------------------------
# GET /chat-history  — in-app conversation transcript (iOS)
# ---------------------------------------------------------------------------

@diagnostics_router.get("/chat-history")
async def get_chat_history(limit: int = Query(200, ge=1, le=2000)):
    """Return the in-app chat transcript, oldest-first.

    Lets the iOS app reload conversation scrollback after a restart or on a
    new device. Scoped to the ``ios:LiveUser`` history key, so it returns
    only the in-app chat (not legacy IM transcripts).
    """
    memory = _get_or_create_memory(_LIVE_USER)
    if memory is None:
        return {"success": True, "messages": []}
    messages = await asyncio.to_thread(memory.get_chat_history, f"ios:{_LIVE_USER}", limit)
    return {"success": True, "messages": messages}


# ---------------------------------------------------------------------------
# POST /onboarding-survey  — record the user's health goals (no LLM)
# ---------------------------------------------------------------------------

class OnboardingSurvey(BaseModel):
    goals: list[str] = []
    answers: dict = {}
    # When true (the Settings "redesign plan" button), kick the plan designer
    # immediately instead of waiting for the next chat reply. Onboarding leaves
    # this false so the run is deferred until the user is warm and engaged.
    trigger_now: bool = False


@diagnostics_router.post("/onboarding-survey")
async def post_onboarding_survey(body: OnboardingSurvey):
    """Record the goal survey verbatim — no LLM runs at capture time.

    The captured goals sit in the memory DB with ``plan_status='pending'``
    (any prior pending survey is superseded). Then:

    - **Onboarding** (``trigger_now=false``): the plan designer fires later,
      after the user's first successful chat reply (LLM warm, user engaged).
    - **Settings "redesign plan"** (``trigger_now=true``): the plan designer is
      kicked right now against the running agent. If the agent isn't up yet we
      simply leave the survey pending and the next-chat-reply hook handles it.
    """
    memory = _get_or_create_memory(_LIVE_USER)
    if memory is None:
        # No memory DB yet — create one so the survey can be recorded.
        from ..agent import MemoryManager
        memory = MemoryManager(settings.MEMORY_DB_PATH, _LIVE_USER)
    await asyncio.to_thread(memory.save_onboarding_survey, body.goals, body.answers)
    logger.info("Onboarding survey saved (%d goals)", len(body.goals))

    triggered = False
    if body.trigger_now:
        info = active_agents.get(_LIVE_USER)
        agent = info.get("agent") if info else None
        if agent is not None and hasattr(agent, "trigger_plan_redesign"):
            triggered = await agent.trigger_plan_redesign()
        if not triggered:
            logger.info(
                "Redesign requested but agent not ready — will run on next chat reply"
            )
    return {"success": True, "queued_plan": True, "triggered_now": triggered}


# ---------------------------------------------------------------------------
# GET /chat-image/{image_id}  — authed fetch of an agent-sent chart (iOS)
# ---------------------------------------------------------------------------

@diagnostics_router.get("/chat-image/{image_id}")
async def get_chat_image(image_id: str):
    """Serve an agent-generated image to the local user only.

    The ``image_id`` is handed to the client on the ``chat_image`` stream
    event. ``image_store.get`` returns the path only when the caller owns it.
    """
    from ..ios_gateway.image_store import image_store

    path = image_store.get(image_id, _LIVE_USER)
    if not path or not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Image not found")
    media_type = mimetypes.guess_type(path)[0] or "image/png"
    return FileResponse(path, media_type=media_type)


# ---------------------------------------------------------------------------
# GET /chat-info  (platform-agnostic; /telegram-info below is a legacy alias)
# ---------------------------------------------------------------------------

@diagnostics_router.get("/chat-info")
async def get_chat_info():
    """Return the iOS chat-button target for whichever gateway is active.

    We intentionally just open the app, NOT a specific chat — see the
    deep-link failure modes in commit history. The iOS side falls back to
    the public web page if the scheme URL can't be handled (e.g. the app
    isn't installed).
    """
    if getattr(settings, "FEISHU_GATEWAY_ENABLED", False):
        return {
            "platform": "feishu",
            "label":    "Chat on Feishu",
            "url":      "lark://",
        }

    if getattr(settings, "TELEGRAM_GATEWAY_ENABLED", False):
        return {
            "platform": "telegram",
            "label":    "Chat on Telegram",
            "url":      "tg://",
        }

    return {"platform": "none", "label": "Chat", "url": ""}


@diagnostics_router.get("/telegram-info")
async def get_telegram_info():
    """Return Telegram bot username and chat ID for iOS deep linking."""
    bot_username = ""
    chat_id = ""
    try:
        if settings.TELEGRAM_TOKEN:
            import httpx
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"https://api.telegram.org/bot{settings.TELEGRAM_TOKEN}/getMe", timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("ok"):
                    bot_username = data["result"].get("username", "")
        chat_ids = settings.TELEGRAM_ALLOWED_CHAT_IDS
        if chat_ids:
            chat_id = chat_ids.split(",")[0].strip()
        elif settings.CHAT_ID:
            chat_id = settings.CHAT_ID.strip()
    except Exception as e:
        logger.debug("telegram-info error: %s", e)
    return {
        "bot_username": bot_username,
        "chat_id": chat_id,
        "group_link": settings.TELEGRAM_GROUP_LINK or "",
    }


# ---------------------------------------------------------------------------
# GET /activity/{pid}
# ---------------------------------------------------------------------------

@diagnostics_router.get("/activity/{pid}")
async def get_agent_activity(pid: str, limit: int = Query(500, ge=1, le=2000)):
    """Return persisted activity events for a user in chronological order."""
    try:
        memory = _get_or_create_memory(pid)
        if not memory:
            return {"success": True, "user_id": pid, "events": []}
        events = memory.get_recent_activity(limit)
        return {"success": True, "user_id": pid, "events": events}
    except Exception as exc:
        logger.exception("Error fetching activity for %s: %s", pid, exc)
        return {"success": True, "user_id": pid, "events": []}


# ---------------------------------------------------------------------------
# GET /memory/{pid}
# ---------------------------------------------------------------------------

@diagnostics_router.get("/memory/{pid}")
async def query_agent_memory(pid: str, query_type: str = "stats"):
    """Query agent memory.  query_type: stats | reports"""
    try:
        memory = _get_or_create_memory(pid)
        if not memory:
            return {"success": True, "user_id": pid, "query_type": query_type, "data": [] if query_type == "reports" else {}}
        if query_type == "stats":
            data = memory.get_stats()
        elif query_type == "reports":
            data = memory.get_recent_reports(limit=20)
        else:
            raise ValueError(f"Invalid query_type '{query_type}'. Use 'stats' or 'reports'.")
        return {"success": True, "user_id": pid, "query_type": query_type, "data": data}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Error querying memory for %s: %s", pid, exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# DELETE /memory/{pid}/reports/{report_id}
# ---------------------------------------------------------------------------

@diagnostics_router.delete("/memory/{pid}/reports/{report_id}")
async def delete_agent_report(pid: str, report_id: int):
    """Delete a single agent-generated report by id."""
    try:
        memory = _get_or_create_memory(pid)
        if not memory:
            raise HTTPException(status_code=404, detail=f"No memory found for user {pid}")
        removed = memory.delete_report(report_id)
        if not removed:
            raise HTTPException(status_code=404, detail=f"Report {report_id} not found")
        return {"success": True, "user_id": pid, "deleted_id": report_id}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Error deleting report %s for %s: %s", report_id, pid, exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# GET /tools
# ---------------------------------------------------------------------------

@diagnostics_router.get("/tools")
async def list_tools(user_id: str | None = None):
    """
    Return definitions of all tools available to the agent.
    If an agent is running for user_id, returns its active registry.
    Otherwise returns default tool set definitions.
    """
    if user_id and user_id in active_agents:
        registry = active_agents[user_id]["agent"].tool_registry
    else:
        # Create a transient registry with dummy dependencies just to get definitions
        from ..agent.autonomous_agent import AutonomousHealthAgent
        from ..agent.skills.registry import SkillRegistry
        from ..agent.tools.registry import ToolRegistry
        skill_registry = SkillRegistry(roots=AutonomousHealthAgent._resolve_skill_roots())
        registry = ToolRegistry.with_default_tools(
            data_store=None,
            memory_db_path=settings.MEMORY_DB_PATH,
            user_id="dummy",
            skill_registry=skill_registry,
        )

    return {
        "success": True,
        "tools": registry.get_definitions()
    }


# ---------------------------------------------------------------------------
# GET /memory/{pid}/inspect
# ---------------------------------------------------------------------------

@diagnostics_router.get("/memory/{pid}/inspect")
async def inspect_memory_table(
    pid: str,
    table_name: str = Query(...),
    limit: int = Query(50, ge=1, le=200)
):
    """Return raw rows from a specific memory table."""
    try:
        memory = _get_or_create_memory(pid)
        if not memory:
            raise HTTPException(status_code=404, detail=f"No memory found for user {pid}")

        with sqlite3.connect(memory.db_file) as conn:
            conn.row_factory = sqlite3.Row
            # Derive the allowlist from the DB itself so it stays in sync with
            # the table list that /memory/{pid} stats exposes to the frontend.
            # Exclude sqlite internals (sqlite_*) to prevent metadata leaks.
            existing = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
            }
            if table_name not in existing:
                raise HTTPException(
                    status_code=400,
                    detail=f"Table '{table_name}' not found. Available: {sorted(existing)}",
                )
            cursor = conn.execute(f"SELECT * FROM [{table_name}] ORDER BY ROWID DESC LIMIT ?", (limit,))
            rows = [dict(r) for r in cursor.fetchall()]

        return {
            "success": True,
            "user_id": pid,
            "table_name": table_name,
            "rows": rows
        }
    except Exception as exc:
        logger.error("Error inspecting memory table %s for %s: %s", table_name, pid, exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
