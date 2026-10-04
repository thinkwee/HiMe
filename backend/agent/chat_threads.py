"""In-app chat threads: auto-title generation (off the chat critical path)."""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

TITLE_MAX_CHARS = 24
_TITLE_TIMEOUT_S = 10.0
_TITLE_PROMPT = (
    "Write a very short title (at most 24 characters) for a chat that starts "
    "with the user message below. Use the same language as the user message. "
    "Reply with the title only: no quotes, no trailing punctuation."
)


def fallback_title(user_text: str) -> str:
    """First user message, whitespace-collapsed and truncated."""
    text = re.sub(r"\s+", " ", user_text or "").strip()
    return text[:TITLE_MAX_CHARS]


def clean_title(raw: str) -> str:
    """Normalise an LLM title answer (first line, no quotes), capped in length."""
    line = (raw or "").strip().splitlines()[0] if (raw or "").strip() else ""
    line = line.strip().strip("\"'`“”‘’「」").strip().rstrip(".。!！")
    return line[:TITLE_MAX_CHARS].strip()


async def generate_title(llm: Any, user_text: str, reply_text: str = "") -> str:
    """One cheap, bounded LLM call; falls back to the truncated user message."""
    from .agent_loops import _ResilientProvider

    prompt = f"{_TITLE_PROMPT}\n\nUser message:\n{(user_text or '')[:400]}"

    async def _call() -> str:
        parts: list[str] = []
        async for chunk in _ResilientProvider(llm).complete(
            messages=[{"role": "user", "content": prompt}],
            tools=None, stream=True, max_tokens=48, temperature=0.3,
        ):
            if chunk.get("type") == "content":
                parts.append(chunk.get("content", ""))
        return "".join(parts)

    try:
        title = clean_title(await asyncio.wait_for(_call(), timeout=_TITLE_TIMEOUT_S))
        if title:
            return title
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.debug("thread auto-title LLM failed: %s", exc)
    return fallback_title(user_text)


async def auto_title_thread(agent: Any, thread_id: str, user_text: str, reply_text: str) -> None:
    """Title an untitled non-main thread and announce it. Never raises."""
    try:
        mem = agent._chat_memory()
        thread = await asyncio.to_thread(mem.get_chat_thread, thread_id)
        if not thread or thread.get("title"):
            return
        title = await generate_title(agent.llm, user_text, reply_text)
        if not title:
            return
        # Re-check inside the write so a user rename during the LLM call wins.
        thread = await asyncio.to_thread(mem.get_chat_thread, thread_id)
        if not thread or thread.get("title"):
            return
        updated = await asyncio.to_thread(mem.update_chat_thread, thread_id, title=title)
        if updated:
            await agent._emit({"type": "chat_thread_updated", "thread": updated})
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.debug("thread auto-title skipped: %s", exc)


def envelope_thread_id(envelope: Any, user_id: str) -> str | None:
    """Thread id of an in-app (iOS) envelope; None for IM channels."""
    from ..ios_gateway.threads import thread_id_from_chat_id
    from ..messaging.base import MessageChannel

    if getattr(envelope, "channel", None) != MessageChannel.IOS:
        return None
    return thread_id_from_chat_id(getattr(envelope, "chat_id", None), user_id)
