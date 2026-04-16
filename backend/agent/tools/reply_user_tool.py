"""``reply_user`` tool — send a conversational reply to the user on the
channel the current message came in on.

The tool is platform-agnostic and routes through ``GatewayRegistry`` based
on the envelope the agent is currently handling (Telegram, Feishu, …).
"""
from __future__ import annotations

import logging
from typing import Any

from ...i18n import t
from ...messaging.base import BaseGateway, MessageEnvelope
from ...messaging.registry import GatewayRegistry
from .base import BaseTool

logger = logging.getLogger(__name__)


class ReplyUserTool(BaseTool):
    """Send a conversational reply to the user via whichever channel the
    current message arrived on.

    The agent loop is expected to set ``self._current_envelope`` before each
    invocation so the tool can select the right gateway via
    ``GatewayRegistry.for_envelope``. If no envelope is set (e.g. during
    auto-reply or benchmark fall-backs) the tool falls back to the first
    registered gateway that has a ``default_chat_id``.
    """

    name = "reply_user"

    def __init__(
        self,
        gateway_registry: GatewayRegistry,
        default_chat_id: str | None = None,
        fact_verifier=None,
    ) -> None:
        self._gateway_registry = gateway_registry
        self._default_chat_id = default_chat_id
        self._fact_verifier = fact_verifier
        self._llm_provider = None  # set by agent for LLM semantic verification
        # Accumulates tool results from the current chat loop
        self._current_tool_results: list = []
        # Chat history for fact verification context
        self._current_chat_history: list = []
        # Envelope of the current user message (set by agent loop per call)
        self._current_envelope: MessageEnvelope | None = None

    # ------------------------------------------------------------------
    # Tool definition (schema lives in tools.json under reply_user)
    # ------------------------------------------------------------------

    def get_definition(self) -> dict[str, Any]:
        return self._get_definition_from_json("reply_user")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_gateway(self) -> BaseGateway | None:
        """Pick the gateway to use for this reply."""
        if self._current_envelope is not None:
            gw = self._gateway_registry.for_envelope(self._current_envelope)
            if gw is not None:
                return gw
        # Fallback: first gateway with a default_chat_id, else any gateway.
        for gw in self._gateway_registry.all():
            if getattr(gw, "default_chat_id", None):
                return gw
        gws = self._gateway_registry.all()
        return gws[0] if gws else None

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def execute(
        self,
        message: str,
        chat_id: str = "",
        image_path: str = "",
        reply_to_message_id: int | None = None,
    ) -> dict[str, Any]:
        """Send the reply (text and/or image) and return status."""
        gateway = self._resolve_gateway()
        if gateway is None:
            return {
                "success": False,
                "error": t("gateway.no_gateway_available"),
            }

        # Determine target chat
        target = chat_id
        if not target and self._current_envelope is not None:
            target = self._current_envelope.chat_id or ""
        if not target:
            target = getattr(gateway, "default_chat_id", None) or self._default_chat_id or ""
        if not target:
            return {
                "success": False,
                "error": t("gateway.no_chat_id"),
            }

        # Verify message before sending
        verification = await self._verify_and_build_markup(message)
        if verification["status"] in ("fabricated", "unverified"):
            logger.warning(
                "reply_user blocked (%s): %s",
                verification["status"], verification["detail"],
            )
            return {
                "success": False,
                "error": t(
                    "reply.fact_blocked",
                    status=verification["status"],
                    detail=verification["detail"],
                ),
            }
        reply_markup = verification["reply_markup"]

        # Send image first (if provided), then text message
        if image_path:
            import os
            if os.path.isfile(image_path):
                photo_ok = await gateway.send_photo(
                    photo_path=image_path,
                    caption=message[:1024] if message else "",
                    chat_id=target,
                    reply_markup=reply_markup,
                )
                if photo_ok:
                    logger.info(
                        "reply_user photo sent via %s to %s",
                        gateway.channel.value, target,
                    )
                    if len(message) <= 1024:
                        return {"success": True, "message": "Photo with caption sent."}
                else:
                    logger.warning("Photo send failed, falling back to text-only")
            else:
                logger.warning("image_path not found: %s", image_path)

        ok = await gateway.send_message(
            text=message,
            chat_id=target,
            reply_to_message_id=reply_to_message_id,
            reply_markup=reply_markup,
        )
        if ok:
            logger.info(
                "reply_user sent via %s to %s: %s",
                gateway.channel.value, target, message[:80],
            )
            return {"success": True, "message": "Reply sent successfully."}
        logger.warning(
            "reply_user failed on %s for chat %s (preview: %s)",
            gateway.channel.value, target, message[:100],
        )
        return {
            "success": False,
            "error": t("gateway.send_failed"),
        }

