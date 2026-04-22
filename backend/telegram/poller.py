"""
TelegramPoller — long-polling consumer for Telegram Bot API updates.

Design notes (inspired by OpenClaw):
  - Maintains a monotonically-increasing ``offset`` so messages are never
    processed twice.
  - Uses ``httpx.AsyncClient`` for non-blocking I/O.
  - Normalises every incoming Telegram message into a ``MessageEnvelope``
    before handing it off to the router callback.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from datetime import datetime, timezone
from typing import Any

import httpx

from .models import MessageChannel, MessageEnvelope

logger = logging.getLogger(__name__)


class TelegramPoller:
    """
    Long-poll the Telegram ``getUpdates`` endpoint and dispatch envelopes.

    Parameters
    ----------
    token : str
        The Bot API token.
    on_message : callable
        ``async def on_message(envelope: MessageEnvelope) -> None`` — called
        for every incoming message.
    poll_timeout : int
        Telegram long-poll timeout in seconds (default 30).
    allowed_chat_ids : set[str] | None
        If set, only messages from these chat IDs are forwarded.  All others
        are silently dropped (security whitelist).
    """

    def __init__(
        self,
        token: str,
        on_message: Callable[[MessageEnvelope], Coroutine],
        poll_timeout: int = 30,
        allowed_chat_ids: set | None = None,
        on_callback_query: Callable | None = None,
    ) -> None:
        self._token = token
        self._on_message = on_message
        self._on_callback_query = on_callback_query
        self._poll_timeout = poll_timeout
        self._allowed_chat_ids = allowed_chat_ids
        self._base_url = f"https://api.telegram.org/bot{token}"
        self._offset: int = 0
        self._running = False
        self._client: httpx.AsyncClient | None = None
        # Simple deduplication: remember the last N message IDs
        self._seen_ids: set = set()
        self._MAX_SEEN = 500

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Begin the polling loop (non-blocking — returns immediately)."""
        if self._running:
            return
        self._running = True
        # Use a generous timeout for long-poll requests
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(self._poll_timeout + 10))
        logger.info("TelegramPoller started (timeout=%ds)", self._poll_timeout)

    async def stop(self) -> None:
        """Signal the polling loop to exit."""
        self._running = False
        if self._client:
            await self._client.aclose()
            self._client = None
        logger.info("TelegramPoller stopped")

    async def poll_loop(self) -> None:
        """
        Main poll loop — call ``start()`` first, then ``await poll_loop()``.

        Runs until ``stop()`` is called.  Errors are caught and retried with
        exponential back-off (capped at 60 s).
        """
        backoff = 1.0
        while self._running:
            try:
                updates = await self._fetch_updates()
                if updates:
                    backoff = 1.0  # reset on success
                    for update in updates:
                        await self._process_update(update)
                else:
                    # No updates — normal, just loop
                    pass
            except asyncio.CancelledError:
                logger.info("Poller cancelled")
                break
            except Exception as exc:
                logger.warning("Poller error (retry in %.0fs): %s", backoff, exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _fetch_updates(self) -> list[dict[str, Any]]:
        """Call ``getUpdates`` and advance the offset."""
        if not self._client:
            return []

        params: dict[str, Any] = {
            "timeout": self._poll_timeout,
            "allowed_updates": ["message", "callback_query"],
        }
        if self._offset:
            params["offset"] = self._offset

        resp = await self._client.get(f"{self._base_url}/getUpdates", params=params)
        data = resp.json()

        if not data.get("ok"):
            raise RuntimeError(f"Telegram API error: {data}")

        results: list[dict] = data.get("result", [])
        if results:
            # Advance offset past the last received update
            self._offset = results[-1]["update_id"] + 1
        return results

    async def _process_update(self, update: dict[str, Any]) -> None:
        """Convert a raw Telegram update into an ``MessageEnvelope`` and dispatch."""
        # Handle callback queries (inline keyboard button presses)
        callback_query = update.get("callback_query")
        if callback_query and self._on_callback_query:
            try:
                await self._on_callback_query(callback_query)
            except Exception as exc:
                logger.error("Error handling callback query: %s", exc, exc_info=True)
            return

        msg = update.get("message")
        if not msg:
            return

        text = msg.get("text", "")
        if not text:
            return  # ignore non-text messages (photos, stickers, etc.)

        chat = msg.get("chat", {})
        chat_id = str(chat.get("id", ""))
        message_id = msg.get("message_id")

        # Security: chat ID whitelist (default-deny when a set is provided).
        # Passing ``None`` disables the whitelist (allow all). Passing an
        # empty set denies everything — this is what main.py does when no
        # chat_id / TELEGRAM_ALLOWED_CHAT_IDS are configured.
        if self._allowed_chat_ids is not None and chat_id not in self._allowed_chat_ids:
            logger.warning(
                "Telegram: rejecting message from non-whitelisted chat %s "
                "(set TELEGRAM_ALLOWED_CHAT_IDS or chat_id in .env to allow)",
                chat_id,
            )
            return

        # Deduplication
        dedup_key = f"{chat_id}:{message_id}"
        if dedup_key in self._seen_ids:
            return
        self._seen_ids.add(dedup_key)
        if len(self._seen_ids) > self._MAX_SEEN:
            # Trim — keep the most recent half
            to_remove = list(self._seen_ids)[: self._MAX_SEEN // 2]
            self._seen_ids -= set(to_remove)

        # Build envelope
        sender = msg.get("from", {})
        sender_name = sender.get("first_name", "")
        if sender.get("last_name"):
            sender_name += f" {sender['last_name']}"

        ts = datetime.fromtimestamp(msg.get("date", 0), tz=timezone.utc)

        envelope = MessageEnvelope(
            message_id=dedup_key,
            channel=MessageChannel.TELEGRAM,
            sender_id=str(sender.get("id", "")),
            content=text,
            timestamp=ts,
            chat_id=chat_id,
            telegram_message_id=message_id,
            metadata={
                "sender_name": sender_name,
                "chat_type": chat.get("type", "private"),
                "chat_title": chat.get("title", ""),
            },
        )

        try:
            await self._on_message(envelope)
        except Exception as exc:
            logger.error("Error dispatching message: %s", exc, exc_info=True)
