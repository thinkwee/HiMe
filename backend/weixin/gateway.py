"""WeixinGateway — :class:`BaseGateway` implementation for WeChat ClawBot.

Composes :class:`WeixinPoller` (long-poll inbound) with
:class:`WeixinSender` (HTTP outbound) over the iLink bot API. Mirrors
:class:`backend.feishu.gateway.FeishuGateway` and
:class:`backend.telegram.gateway.TelegramGateway` so the agent loop and
tool layer can route outbound messages through the shared
:class:`backend.messaging.registry.GatewayRegistry` without branching on
the active platform.

Authentication is intentionally split out from the gateway lifecycle: a
``bot_token`` is produced once by ``python -m backend.weixin.qr_login``
(scan the ClawBot QR with the WeChat app) and persisted to disk. The
gateway reads that token at startup and refuses to start if it is absent,
logging a clear instruction. This keeps backend startup non-interactive.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from ..messaging.base import BaseGateway, MessageChannel, MessageEnvelope
from .qr_login import load_bot_token
from .sender import WeixinSender
from .transport import WeixinPoller

logger = logging.getLogger(__name__)

OnUserMessage = Callable[[MessageEnvelope], Awaitable[None]]


class WeixinGateway(BaseGateway):
    """Bidirectional WeChat ClawBot gateway."""

    channel = MessageChannel.WEIXIN

    def __init__(
        self,
        settings: Any,
        on_user_message: OnUserMessage | None = None,
        allowed_user_ids: set[str] | None = None,
    ) -> None:
        self._settings = settings
        self._on_user_message = on_user_message

        token_path = Path(
            getattr(
                settings,
                "WEIXIN_BOT_TOKEN_PATH",
                "./data/weixin_bot_token.json",
            )
        )
        self._token_path = token_path
        self._bot_token = load_bot_token(token_path)

        # ``default_chat_id`` on this gateway is a WeChat user ID
        # (e.g. ``xxx@im.wechat``). Kept under the BaseGateway field name so
        # tools that consult ``gateway.default_chat_id`` work uniformly.
        self.default_chat_id = (
            getattr(settings, "WEIXIN_DEFAULT_USER_ID", "") or None
        )

        if allowed_user_ids is None:
            raw = getattr(settings, "WEIXIN_ALLOWED_USER_IDS", "") or ""
            allowed = {uid.strip() for uid in raw.split(",") if uid.strip()}
            if self.default_chat_id:
                allowed.add(self.default_chat_id)
            allowed_user_ids = allowed
        # On WeChat the bot is bound to whoever scanned the QR — there is
        # no shareable bot URL like Telegram. An empty allowlist therefore
        # means "trust whoever can talk to this bot" (i.e. the scanner),
        # which is the desired UX. This deliberately differs from
        # Telegram, where the gateway defaults to deny-all on empty.
        self.allowed_chat_ids = allowed_user_ids

        # Built lazily in ``start()`` once a bot_token is confirmed present
        self._poller: WeixinPoller | None = None
        self.sender: WeixinSender | None = None
        self._poll_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        if not self._bot_token:
            logger.warning(
                "WeixinGateway: no bot_token at %s — run "
                "`python -m backend.weixin.qr_login` and scan the ClawBot "
                "QR with WeChat. Skipping start.",
                self._token_path,
            )
            return

        self._poller = WeixinPoller(
            bot_token=self._bot_token,
            on_message=self._handle_message,
            allowed_user_ids=self.allowed_chat_ids or None,
        )
        self.sender = WeixinSender(
            bot_token=self._bot_token,
            default_user_id=self.default_chat_id,
            get_context_token=self._poller.latest_context_token,
        )
        await self.sender.start()
        await self._poller.start()
        self._poll_task = asyncio.create_task(
            self._poller.poll_loop(), name="weixin-poller",
        )
        logger.info("WeixinGateway started")

    async def stop(self) -> None:
        if self._poller:
            await self._poller.stop()
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        if self.sender:
            await self.sender.stop()
        logger.info("WeixinGateway stopped")

    # ------------------------------------------------------------------
    # Inbound dispatch
    # ------------------------------------------------------------------
    async def _handle_message(self, envelope: MessageEnvelope) -> None:
        logger.info(
            "WeChat message from %s: %s",
            envelope.sender_id,
            (envelope.content or "")[:80],
        )
        if self._on_user_message is None:
            logger.warning("WeixinGateway: no on_user_message handler configured")
            return
        try:
            await self._on_user_message(envelope)
        except Exception as exc:
            logger.error(
                "WeixinGateway on_user_message error: %s", exc, exc_info=True,
            )

    # ------------------------------------------------------------------
    # BaseGateway outbound interface
    # ------------------------------------------------------------------
    async def send_message(
        self,
        text: str,
        chat_id: str | None = None,
        reply_to_message_id: int | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> bool:
        if self.sender is None:
            logger.warning("WeixinGateway.send_message: gateway not started")
            return False
        target = chat_id or self.default_chat_id
        if not target:
            logger.warning("WeixinGateway.send_message: no user_id available")
            return False
        return await self.sender.send_message(
            text=text, user_id=target, reply_markup=reply_markup,
        )

    async def send_photo(
        self,
        photo_path: str,
        caption: str = "",
        chat_id: str | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> bool:
        # iLink media upload requires AES-128-ECB CDN handshake — not yet
        # implemented. Degrade to sending the caption as plain text so the
        # report tool surfaces *something* instead of dropping silently.
        logger.warning(
            "WeixinGateway.send_photo: image upload not yet supported; "
            "sending caption as text instead (path=%s)", photo_path,
        )
        return await self.send_message(
            text=caption or "[image]",
            chat_id=chat_id,
            reply_markup=reply_markup,
        )

    async def edit_message(
        self,
        chat_id: str,
        message_id: Any,
        text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> bool:
        # iLink has no edit endpoint; degrade to sending a fresh message so
        # callers that depend on edit_message (e.g. progress updates) still
        # see something land in the chat.
        return await self.send_message(
            text=text, chat_id=chat_id, reply_markup=reply_markup,
        )

    async def answer_callback(
        self,
        callback_id: str,
        text: str = "",
        show_alert: bool = False,
    ) -> bool:
        # No interactive-button surface on WeChat ClawBot.
        return True

    def is_muted(self) -> bool:
        return self.sender.is_muted() if self.sender else False
