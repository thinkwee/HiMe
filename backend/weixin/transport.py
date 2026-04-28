"""WeChat ClawBot inbound transport — long-poll the iLink ``getupdates`` endpoint.

The iLink API holds the connection for ~35 s when no updates are pending,
so the poller uses an HTTP timeout that gives the server room to reply
before treating the call as a network failure. A monotonically-advancing
``get_updates_buf`` cursor prevents re-delivery; we still keep a small
in-memory dedup set as a belt-and-braces guard against revisions that
sometimes resend on cursor regressions.

Every inbound message carries a ``context_token`` that the iLink
``sendmessage`` endpoint requires for replies. The poller caches the
latest token per ``from_user_id`` so the sender can thread autonomous
replies back into an existing conversation.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

import httpx

from .models import MessageChannel, MessageEnvelope
from .qr_login import CHANNEL_VERSION, common_headers

logger = logging.getLogger(__name__)

ILINK_BASE = "https://ilinkai.weixin.qq.com"
# iLink's getupdates holds the connection ~35 s when no messages are
# pending. The HTTP timeout is set generously so a clean server reply
# never looks like a network failure to httpx.
_LONG_POLL_HTTP_TIMEOUT = 50.0

OnMessage = Callable[[MessageEnvelope], Awaitable[None]]


class WeixinPoller:
    """Long-poll iLink ``getupdates`` and dispatch normalised envelopes.

    Parameters
    ----------
    bot_token : str
        Persistent token from the QR login step.
    on_message : callable
        ``async def(envelope) -> None`` — invoked per inbound text message.
    allowed_user_ids : set[str] | None
        If non-None, only messages from these user IDs are forwarded
        (default-deny). ``None`` disables the whitelist.
    """

    def __init__(
        self,
        bot_token: str,
        on_message: OnMessage,
        allowed_user_ids: set[str] | None = None,
    ) -> None:
        self._token = bot_token
        self._on_message = on_message
        self._allowed = allowed_user_ids
        self._cursor: str = ""
        self._running = False
        self._client: httpx.AsyncClient | None = None
        self._seen_ids: set[str] = set()
        self._MAX_SEEN = 500
        self._last_context: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(_LONG_POLL_HTTP_TIMEOUT)
        )
        logger.info("WeixinPoller started")

    async def stop(self) -> None:
        self._running = False
        if self._client:
            await self._client.aclose()
            self._client = None
        logger.info("WeixinPoller stopped")

    def latest_context_token(self, user_id: str) -> str | None:
        """Return the most recent context_token observed for ``user_id``."""
        return self._last_context.get(user_id)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    async def poll_loop(self) -> None:
        backoff = 1.0
        while self._running:
            try:
                updates = await self._fetch_updates()
                if updates:
                    backoff = 1.0
                    for upd in updates:
                        await self._process_update(upd)
            except asyncio.CancelledError:
                logger.info("WeixinPoller cancelled")
                break
            except Exception as exc:
                logger.warning(
                    "WeixinPoller error (retry in %.0fs): %s", backoff, exc,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    async def _fetch_updates(self) -> list[dict[str, Any]]:
        if not self._client:
            return []
        # iLink controls the long-poll hold itself (~35 s); the request body
        # only carries the resume cursor + the channel-version handshake.
        body: dict[str, Any] = {
            "get_updates_buf": self._cursor,
            "base_info": {"channel_version": CHANNEL_VERSION},
        }

        resp = await self._client.post(
            f"{ILINK_BASE}/ilink/bot/getupdates",
            json=body,
            headers=common_headers(self._token),
        )
        if resp.status_code == 401:
            raise RuntimeError(
                "iLink: 401 Unauthorized — bot_token expired. "
                "Re-run `python -m backend.weixin.qr_login`."
            )
        if resp.status_code != 200:
            raise RuntimeError(
                f"iLink getupdates HTTP {resp.status_code}: {resp.text[:200]}"
            )
        data = resp.json()
        # ``ret`` is iLink's app-level status code; non-zero means the
        # request was understood but rejected (e.g. quota, bad cursor).
        ret = data.get("ret")
        if ret not in (None, 0):
            raise RuntimeError(
                f"iLink getupdates app error: ret={ret} "
                f"err_msg={data.get('err_msg', '')!r}"
            )
        nxt = data.get("get_updates_buf") or ""
        if nxt:
            self._cursor = nxt
        return data.get("msgs") or []

    async def _process_update(self, update: dict[str, Any]) -> None:
        msg_id = str(update.get("message_id") or update.get("id") or "")
        if msg_id and msg_id in self._seen_ids:
            return
        if msg_id:
            self._seen_ids.add(msg_id)
            if len(self._seen_ids) > self._MAX_SEEN:
                self._seen_ids = set(
                    list(self._seen_ids)[self._MAX_SEEN // 2 :]
                )

        from_user = str(update.get("from_user_id") or "")
        if not from_user:
            return

        if self._allowed is not None and from_user not in self._allowed:
            logger.warning(
                "WeChat: rejecting message from non-whitelisted user %s "
                "(set WEIXIN_ALLOWED_USER_IDS to allow)",
                from_user,
            )
            return

        ctx_token = str(update.get("context_token") or "")
        if ctx_token:
            self._last_context[from_user] = ctx_token

        text = _extract_text(update)
        if not text:
            return

        ts_raw = update.get("timestamp") or update.get("ts") or 0
        try:
            ts = datetime.fromtimestamp(int(ts_raw), tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            ts = datetime.now(timezone.utc)

        envelope = MessageEnvelope(
            message_id=msg_id or f"weixin:{ts.timestamp()}",
            channel=MessageChannel.WEIXIN,
            sender_id=from_user,
            content=text,
            timestamp=ts,
            # WeChat ClawBot is 1-on-1 — the user ID *is* the chat.
            chat_id=from_user,
            conversation_id=from_user,
            platform_message_id=msg_id,
            metadata={
                "context_token": ctx_token,
                "raw": update,
            },
        )

        try:
            await self._on_message(envelope)
        except Exception as exc:
            logger.error(
                "WeixinPoller dispatch error: %s", exc, exc_info=True,
            )


def _extract_text(update: dict[str, Any]) -> str:
    """Pull the text payload out of an iLink message.

    Each entry in ``item_list`` is a typed wrapper — text items expose the
    body at ``text_item.text``. Non-text items (image/file/audio/video)
    are intentionally ignored; a future revision can wire them into the
    agent once the AES-128-ECB CDN handshake is implemented.
    """
    parts: list[str] = []
    for item in update.get("item_list") or []:
        if not isinstance(item, dict):
            continue
        text_item = item.get("text_item")
        if isinstance(text_item, dict):
            text = text_item.get("text") or ""
            if text:
                parts.append(text)
            continue
        logger.info(
            "WeChat: skipping non-text item type=%s keys=%s",
            item.get("type"), list(item.keys()),
        )
    return "\n".join(parts).strip()
