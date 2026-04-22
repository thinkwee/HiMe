"""FeishuGateway — BaseGateway implementation for Feishu / Lark.

Composes a :class:`FeishuSender` with either :class:`FeishuWsTransport` or
:class:`FeishuWebhookTransport` (selected by ``settings.FEISHU_TRANSPORT``).
Mirrors :class:`backend.telegram.gateway.TelegramGateway` feature-for-feature
so the agent loop and tool layer can push outbound messages through the
shared :class:`backend.messaging.registry.GatewayRegistry` without branching
on the active platform.

The gateway owns a cached per-user :class:`FactVerifier` that handles
card-action button clicks the same way Telegram handles inline-keyboard
callbacks — loading the evidence payload by ``message_hash`` and toggling
the card between "original view" and "evidence view".
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from ..i18n import t
from ..messaging.base import BaseGateway, MessageChannel, MessageEnvelope
from .cards import build_plain_card
from .sender import FeishuSender, markdown_to_feishu_post
from .transport import FeishuWebhookTransport, make_transport

logger = logging.getLogger(__name__)


OnUserMessage = Callable[[MessageEnvelope], Awaitable[None]]


class FeishuGateway(BaseGateway):
    """Gateway orchestrator for the Feishu (Lark) channel."""

    channel = MessageChannel.FEISHU

    def __init__(
        self,
        settings: Any,
        on_user_message: OnUserMessage | None = None,
        get_memory_manager: Callable[[str], Any] | None = None,
        allowed_chat_ids: set[str] | None = None,
    ) -> None:
        self._settings = settings
        self._on_user_message = on_user_message
        self._get_memory_manager = get_memory_manager or (lambda pid: None)

        # Configuration mirrors the Telegram gateway's conventions.
        self.default_chat_id = getattr(settings, "FEISHU_DEFAULT_CHAT_ID", "") or None

        if allowed_chat_ids is None:
            raw = getattr(settings, "FEISHU_ALLOWED_CHAT_IDS", "") or ""
            allowed: set[str] = {
                cid.strip() for cid in raw.split(",") if cid.strip()
            }
            if self.default_chat_id:
                allowed.add(self.default_chat_id)
            allowed_chat_ids = allowed
        self.allowed_chat_ids = allowed_chat_ids

        # Outbound sender
        self.sender = FeishuSender(
            app_id=getattr(settings, "FEISHU_APP_ID", ""),
            app_secret=getattr(settings, "FEISHU_APP_SECRET", ""),
            default_chat_id=self.default_chat_id,
        )

        # Inbound transport (WS or webhook) — drives free-form message
        # delivery according to ``settings.FEISHU_TRANSPORT``.
        self._transport = make_transport(
            settings=settings,
            on_message=self._handle_message,
            on_card_action=self._handle_card_action,
            allowed_chat_ids=self.allowed_chat_ids,
        )

        # Card-callback HTTP route — ALWAYS mounted, regardless of transport.
        # Reason: Feishu sends interactive-card button clicks to the bot's
        # "Message Card Request URL" (configured separately in the developer
        # console under Bot Settings), which is *not* part of the event
        # subscription channel — neither WS long-connection nor the events
        # webhook receives card actions. So even when message events flow
        # over WS, we still need an HTTP endpoint listening for card POSTs.
        # When the primary transport is already a webhook, we re-use it as
        # the card route to avoid mounting twice on the same path.
        if isinstance(self._transport, FeishuWebhookTransport):
            self._card_webhook = self._transport
        else:
            self._card_webhook = FeishuWebhookTransport(
                on_message=self._handle_message,
                on_card_action=self._handle_card_action,
                allowed_chat_ids=self.allowed_chat_ids,
                webhook_path=getattr(
                    settings, "FEISHU_WEBHOOK_PATH", "/api/feishu/webhook",
                ),
                verification_token=getattr(
                    settings, "FEISHU_VERIFICATION_TOKEN", "",
                ),
                encrypt_key=getattr(settings, "FEISHU_ENCRYPT_KEY", ""),
            )

        # Cached FactVerifier per user (mirrors TelegramGateway).
        self._fact_verifiers: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        await self.sender.start()
        await self._transport.start()
        logger.info(
            "FeishuGateway started (transport=%s)",
            getattr(self._settings, "FEISHU_TRANSPORT", "ws"),
        )

    async def stop(self) -> None:
        try:
            await self._transport.stop()
        finally:
            await self.sender.stop()
        logger.info("FeishuGateway stopped")

    # ------------------------------------------------------------------
    # Inbound dispatch
    # ------------------------------------------------------------------

    async def _handle_message(self, envelope: MessageEnvelope) -> None:
        """Forward an inbound free-form message to the registered callback."""
        logger.info(
            "Feishu message from %s (chat=%s): %s",
            envelope.sender_id,
            envelope.chat_id,
            (envelope.content or "")[:80],
        )
        if self._on_user_message is None:
            logger.warning("FeishuGateway: no on_user_message handler configured")
            return
        try:
            await self._on_user_message(envelope)
        except Exception as exc:
            logger.error("FeishuGateway on_user_message error: %s", exc, exc_info=True)

    async def _handle_card_action(self, event: dict[str, Any]) -> dict[str, Any] | None:
        """Handle an interactive-card button click.

        Strategy: send the evidence as a **new message in the same chat**
        rather than trying to update the original card in place.

        Why not in-place update? We tried two paths and neither works
        reliably for cards that mix images + text + buttons (which is the
        common case for chart reports):

        * ``im.v1.message.patch`` returns ``ok=True`` at the API layer but
          Feishu silently refuses to re-render cards whose structure has
          changed (e.g. patching a photo card down to a text-only card).
        * Returning the new card in the HTTP response body (Feishu's
          "local update" protocol) works for some 1.0 cards but is
          ignored for legacy callbacks on photo cards.

        Sending a fresh message is bulletproof: no schema constraints, no
        update mechanism dependencies, and the user still gets the
        evidence trail attached to the same conversation. The trade-off
        is that we lose the toggle behaviour — there is no "Back" view —
        but that's an acceptable simplification.
        """
        try:
            import json as _json

            payload = event.get("event", {}) or {}
            logger.info(
                "Feishu card action received: keys=%s",
                list(payload.keys()),
            )
            try:
                logger.info(
                    "Feishu card action payload: %s",
                    _json.dumps(payload, ensure_ascii=False, default=str)[:2000],
                )
            except Exception:
                pass

            action = payload.get("action", {}) or {}
            value = action.get("value", {}) or {}
            if isinstance(value, str):
                try:
                    value = _json.loads(value)
                except Exception:
                    value = {}
            verb = value.get("action", "")
            msg_hash = value.get("hash", "")

            # ``open_chat_id`` lives in different places across console
            # versions; try every plausible spot.
            context = payload.get("context") or {}
            host = payload.get("host") or {}
            chat_id = (
                context.get("open_chat_id")
                or (host.get("open_chat_id") if isinstance(host, dict) else None)
                or payload.get("open_chat_id")
                or context.get("chat_id")
                or ""
            )

            if not verb or not msg_hash:
                logger.warning(
                    "Feishu card action missing fields: verb=%r hash=%r chat_id=%r",
                    verb, msg_hash, chat_id,
                )
                return None

            if verb != "evidence":
                # The toggle (verb=="restore") path is gone — the original
                # card is never replaced, so there is nothing to restore.
                logger.info("Feishu card action: ignoring verb=%s", verb)
                return None

            if not chat_id:
                logger.warning("Feishu card action: no chat_id available, cannot reply")
                return None

            verifier = self._get_fact_verifier()
            evidence = await asyncio.to_thread(verifier.get_evidence, msg_hash)
            if not evidence:
                logger.info("Feishu card action: no evidence for hash=%s", msg_hash)
                return None

            body_text = await asyncio.to_thread(
                verifier.format_evidence_for_display, evidence,
            )
            evidence_header = t("card.evidence_header")
            evidence_card = build_plain_card(
                markdown_to_feishu_post(
                    f"\U0001f50d **{evidence_header}**\n\n{body_text}"
                ),
            )

            ok = await self.sender.send_card(chat_id, evidence_card)
            logger.info(
                "Feishu evidence reply sent: ok=%s chat_id=%s hash=%s",
                ok, chat_id, msg_hash,
            )
            # Acknowledge the callback with an empty body so Feishu doesn't
            # complain. Returning the original card schema as a "local
            # update" is unreliable here — see the docstring above.
            return None
        except Exception as exc:
            logger.error("FeishuGateway card action handler error: %s", exc, exc_info=True)
            return None

    def _get_fact_verifier(self, user_id: str = "LiveUser") -> Any:
        if user_id not in self._fact_verifiers:
            from ..agent.fact_verifier import FactVerifier
            from ..config import settings as _settings

            self._fact_verifiers[user_id] = FactVerifier(
                _settings.MEMORY_DB_PATH, user_id,
            )
        return self._fact_verifiers[user_id]

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
        target = chat_id or self.default_chat_id or ""
        if not target:
            logger.warning("FeishuGateway.send_message: no chat_id available")
            return False
        return await self.sender.send_message(
            chat_id=target, text=text, reply_markup=reply_markup,
        )

    async def send_photo(
        self,
        photo_path: str,
        caption: str = "",
        chat_id: str | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> bool:
        target = chat_id or self.default_chat_id or ""
        if not target:
            logger.warning("FeishuGateway.send_photo: no chat_id available")
            return False
        return await self.sender.send_photo(
            chat_id=target,
            image_path=photo_path,
            caption=caption,
            reply_markup=reply_markup,
        )

    async def edit_message(
        self,
        chat_id: str,
        message_id: Any,
        text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> bool:
        return await self.sender.edit_message(
            message_id=str(message_id),
            text=text,
            reply_markup=reply_markup,
        )

    async def answer_callback(
        self,
        callback_id: str,
        text: str = "",
        show_alert: bool = False,
    ) -> bool:
        return await self.sender.answer_callback(
            callback_id=callback_id, text=text, show_alert=show_alert,
        )

    def is_muted(self) -> bool:
        return self.sender.is_muted()

    # ------------------------------------------------------------------
    # FastAPI wiring — always mounted so card callbacks have a destination
    # ------------------------------------------------------------------

    def register_routes(self, app: Any) -> None:
        """Mount the HTTP endpoint that handles card-callback POSTs.

        This is **always** mounted, even when ``FEISHU_TRANSPORT=ws``,
        because Feishu delivers interactive-card button clicks via a
        separate "Message Card Request URL" (configured in the bot
        settings page) and not over the long-connection event channel.
        Without this route the user sees a Feishu-side error popup
        whenever they tap an evidence button.
        """
        self._card_webhook.register_routes(app)
