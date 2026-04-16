"""
Agent diagnostics — activity log, memory inspection, tool listing, messaging-gateway info.
"""
from __future__ import annotations

import logging
import sqlite3

from fastapi import APIRouter, HTTPException, Query

from ..config import settings
from .agent_state import _get_or_create_memory, active_agents

logger = logging.getLogger(__name__)

diagnostics_router = APIRouter()


# ---------------------------------------------------------------------------
# GET /chat-info  (platform-agnostic; /telegram-info below is a legacy alias)
# ---------------------------------------------------------------------------

@diagnostics_router.get("/chat-info")
async def get_chat_info():
    """Return the iOS chat-button target for whichever gateway is active.

    Resolution order:
    * Feishu gateway enabled → ``{platform: "feishu", label: "Chat on Feishu",
      url: <best-effort lark deep link>}``.
    * Telegram gateway enabled → ``{platform: "telegram", label: "Chat on Telegram",
      url: <best telegram link>}``.
    * Neither → ``{platform: "none", label: "Chat", url: ""}``.

    The iOS app calls this on the chat button so it doesn't need to know
    which gateway HIME is configured for.
    """
    if getattr(settings, "FEISHU_GATEWAY_ENABLED", False):
        chat_id = ""
        allowed = getattr(settings, "FEISHU_ALLOWED_CHAT_IDS", "") or ""
        if allowed:
            chat_id = allowed.split(",")[0].strip()
        elif getattr(settings, "FEISHU_DEFAULT_CHAT_ID", ""):
            chat_id = settings.FEISHU_DEFAULT_CHAT_ID.strip()
        # Lark scheme link — opens the conversation in the Feishu/Lark app
        # if installed.  ``oc_*`` is a chat (group / p2p) open-id.
        url = f"lark://im/chat?chatId={chat_id}" if chat_id else "lark://"
        return {
            "platform": "feishu",
            "label":    "Chat on Feishu",
            "url":      url,
            "chat_id":  chat_id,
        }

    if getattr(settings, "TELEGRAM_GATEWAY_ENABLED", False):
        info = await get_telegram_info()
        # Pick the best link the legacy endpoint returns
        link = info.get("group_link") or ""
        if not link:
            chat_id = info.get("chat_id", "")
            if chat_id:
                numeric = chat_id
                if numeric.startswith("-100"):
                    numeric = numeric[4:]
                elif numeric.startswith("-"):
                    numeric = numeric[1:]
                link = f"tg://openmessage?chat_id={numeric}"
        if not link and info.get("bot_username"):
            link = f"https://t.me/{info['bot_username']}"
        return {
            "platform":     "telegram",
            "label":        "Chat on Telegram",
            "url":          link,
            "bot_username": info.get("bot_username", ""),
            "chat_id":      info.get("chat_id", ""),
            "group_link":   info.get("group_link", ""),
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
