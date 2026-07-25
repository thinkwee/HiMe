"""IOSGateway — delivers agent replies into the native iOS app.

Implements the platform-agnostic :class:`~backend.messaging.base.BaseGateway`
so the existing ``reply_user`` / ``push_report`` tools route through it with
no changes. Unlike the IM gateways there is no transport to poll: inbound
chat arrives via ``POST /api/agent/chat`` and outbound messages are emitted
as events onto the agent's stream, which the iOS app consumes over the
existing ``/api/stream/agent`` WebSocket.

Bound to one ``user_id`` (``LiveUser`` in single-user mode). Proactive pushes
to a closed app go out over APNs.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

from ..messaging.base import BaseGateway, MessageChannel
from .connections import ios_connections
from .image_store import image_store

logger = logging.getLogger(__name__)

_APP_NAME = "HiMe"
# Coalesce APNs alerts within this window so a multi-reply turn (ack +
# findings) produces one banner, not several — the app shows everything on
# open. Mirrors the inbox debounce window.
_APNS_COALESCE_S = 5.0


def extract_message_hash(reply_markup: dict[str, Any] | None) -> str | None:
    """Pull the evidence hash out of a fact-verification ``reply_markup``.

    The markup is built channel-agnostically by
    ``BaseTool._verify_and_build_markup`` as::

        {"inline_keyboard": [[{"text": "...", "callback_data": "evidence:<hash>"}]]}

    iOS uses the hash to fetch the evidence trail via
    ``GET /api/agent/evidence/{hash}``.
    """
    if not reply_markup:
        return None
    try:
        for row in reply_markup.get("inline_keyboard", []) or []:
            for btn in row:
                data = btn.get("callback_data", "") or ""
                if data.startswith("evidence:"):
                    return data.split(":", 1)[1]
    except Exception:  # pragma: no cover — defensive against odd markup
        pass
    return None


class IOSGateway(BaseGateway):
    """In-app channel gateway for one HiMe user."""

    channel = MessageChannel.IOS
    #: iOS renders an image and its caption together, so ``reply_user`` need
    #: not emit a duplicate text follow-up after a photo.
    supports_inline_caption = True

    def __init__(self, user_id: str, apns_sender: Any | None = None) -> None:
        self.user_id = user_id
        # default_chat_id == user_id so push_report fans out to this user and
        # reply targets resolve correctly.
        self.default_chat_id = user_id
        self.allowed_chat_ids = {user_id}
        self.sender = None  # no external sender handle
        self._apns = apns_sender
        self._last_apns_ts = 0.0
        # Set by send_photo to the durable image id of the most recent photo so
        # the chat loop can persist it on the assistant's chat_history turn
        # (enables history replay, not just live delivery). reply_user reads it
        # immediately after a successful send_photo, so no cross-turn races.
        self.last_image_id: str | None = None

    # ------------------------------------------------------------------
    # Lifecycle (no transport — both are no-ops)
    # ------------------------------------------------------------------

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    # ------------------------------------------------------------------
    # Delivery helpers
    # ------------------------------------------------------------------

    async def _emit_to_stream(self, event: dict) -> bool:
        """Emit an event onto the agent's stream (the WS downlink).

        Routed through ``agent._emit`` (the same path as ``chat_content``)
        so the reply is correctly ordered after any streaming chunks for the
        turn. Resolved lazily because the gateway outlives individual agent
        instances (supervisor restarts).
        """
        from ..api.agent_state import active_agents

        info = active_agents.get(self.user_id)
        agent = info.get("agent") if info else None
        if agent is None:
            logger.warning(
                "IOSGateway[user=%s]: no active agent to emit %s",
                self.user_id, event.get("type"),
            )
            return False
        try:
            await agent._emit(event)
            return True
        except Exception as e:  # pragma: no cover — defensive
            logger.warning("IOSGateway[user=%s]: emit failed: %s", self.user_id, e)
            return False

    async def _maybe_push_apns(self, body: str, data: dict) -> None:
        """Send an APNs alert iff the app has no live stream connection."""
        if ios_connections.is_online(self.user_id):
            return  # delivered live over the WebSocket
        if self._apns is None or not getattr(self._apns, "enabled", False):
            logger.info(
                "IOSGateway[user=%s]: offline and APNs not configured — "
                "message persisted, will appear on next app open",
                self.user_id,
            )
            return
        # Coalesce rapid alerts (multi-reply turn) into one banner.
        now = time.monotonic()
        if now - self._last_apns_ts < _APNS_COALESCE_S:
            return
        try:
            await self._apns.send(self.user_id, title=_APP_NAME, body=body, data=data)
        except Exception as e:  # pragma: no cover — network/credential errors
            logger.warning("IOSGateway[user=%s]: APNs send failed: %s", self.user_id, e)
        else:
            # Only start the coalescing window on a send that actually went out —
            # otherwise one failure silently suppresses the whole burst.
            self._last_apns_ts = now

    # ------------------------------------------------------------------
    # Outbound messaging
    # ------------------------------------------------------------------

    async def send_message(
        self,
        text: str,
        chat_id: str | None = None,
        reply_to_message_id: int | None = None,
        reply_markup: dict[str, Any] | None = None,
        report_id: int | None = None,
    ) -> bool:
        target = chat_id or self.default_chat_id
        msg_hash = extract_message_hash(reply_markup)
        delivered = await self._emit_to_stream({
            "type": "chat_reply",
            "content": text,
            "chat_id": target,
            "message_hash": msg_hash,
            "reply_markup": reply_markup,
            # When this message is a proactive report push, carry the report's
            # DB id so the app can offer a "view full report" deep-link beneath
            # the bubble. None for ordinary chat replies.
            "report_id": report_id,
            "auto": False,
            "final": True,
        })
        await self._maybe_push_apns(
            body=(text or "")[:120],
            data={"chat_id": target, "message_hash": msg_hash},
        )
        # Report the real outcome: _emit_to_stream returns False when there is no
        # active agent (e.g. mid supervisor-restart). Returning True regardless
        # made callers believe a dropped message had been delivered.
        return delivered

    async def send_photo(
        self,
        photo_path: str,
        caption: str = "",
        chat_id: str | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> bool:
        target = chat_id or self.default_chat_id
        msg_hash = extract_message_hash(reply_markup)
        # Register the server-local file behind an opaque, per-user-authed id
        # so the client fetches it via GET /api/agent/chat-image/<id> with its
        # bearer token (never expose the raw filesystem path).
        image_id = None
        self.last_image_id = None
        try:
            if photo_path and os.path.exists(photo_path):
                image_id = image_store.register(self.user_id, photo_path)
                self.last_image_id = image_id
        except Exception as e:  # pragma: no cover — defensive
            logger.warning("IOSGateway[user=%s]: image register failed: %s", self.user_id, e)
        delivered = await self._emit_to_stream({
            "type": "chat_image",
            "image_id": image_id,
            "url": f"/api/agent/chat-image/{image_id}" if image_id else None,
            "caption": caption,
            "chat_id": target,
            "message_hash": msg_hash,
            "reply_markup": reply_markup,
        })
        await self._maybe_push_apns(
            body=(caption or "📷 Image")[:120],
            data={"chat_id": target, "message_hash": msg_hash},
        )
        return delivered

    async def edit_message(
        self,
        chat_id: str,
        message_id: Any,
        text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> bool:
        # iOS evidence is pull-based (GET /api/agent/evidence/{hash}); the
        # agent never edits a delivered message on this channel.
        return True

    async def answer_callback(
        self,
        callback_id: str,
        text: str = "",
        show_alert: bool = False,
    ) -> bool:
        return True

    def is_muted(self) -> bool:
        # Proactive pushes to the user's own app are always allowed.
        return False
