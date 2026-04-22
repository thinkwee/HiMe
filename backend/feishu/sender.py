"""FeishuSender — thin async wrapper over ``lark_oapi`` for outbound calls.

The sender exposes the same surface as :class:`backend.telegram.sender.TelegramSender`
so :class:`backend.feishu.gateway.FeishuGateway` can forward calls coming
through :class:`backend.messaging.base.BaseGateway` without any translation.

Design notes:

* ``lark_oapi`` is imported *lazily* inside each method so that merely
  importing this module does not require the SDK. Tests and minimal
  deployments can exercise the card builders and envelope routing without
  installing ``lark-oapi``.
* Blocking SDK calls are pushed to a worker thread via
  :func:`asyncio.to_thread` because the current Python SDK only exposes a
  synchronous ``Client``.
* Markdown → Feishu ``lark_md`` conversion is intentionally minimal (bold,
  inline code, code fence, bullet list, link). Anything else falls through
  unmodified — the Feishu client tolerates plain text inside ``lark_md``
  blocks gracefully.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from .cards import build_evidence_card, build_plain_card

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Markdown → Feishu lark_md conversion
# ---------------------------------------------------------------------------

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_CODE_FENCE_RE = re.compile(r"```([a-zA-Z0-9_+-]*)\n(.*?)```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_BULLET_RE = re.compile(r"^[\t ]*[-*] +(.*)$", re.MULTILINE)


def markdown_to_feishu_post(text: str) -> str:
    """Convert a markdown subset to Feishu ``lark_md`` syntax.

    Feishu's ``lark_md`` supports a restricted subset of Markdown plus a
    small set of custom tags (``<at user_id=\"...\">``). This helper handles
    the overlap HIME messages actually use:

    * ``**bold**`` → ``**bold**`` (unchanged — lark_md already supports it)
    * ```` ```...``` ```` → lark_md fence (converted to plain indented block
      because lark_md has no dedicated fence; emitted as text/`code` lines)
    * `` `code` `` → unchanged
    * ``[text](url)`` → ``[text](url)`` (lark_md honours this)
    * ``- item`` lines → ``• item`` (lark_md has no bullet tag)

    Any construct that falls outside this whitelist is passed through
    unchanged. On an unexpected error the original text is returned —
    rendering broken markdown is always preferable to dropping the message.
    """
    if not text:
        return ""
    try:
        out = text

        # Code fences → indented block. Feishu lark_md treats leading spaces
        # as preformatted, which is good enough for chart captions etc.
        def _fence_sub(match: re.Match[str]) -> str:
            body = match.group(2)
            lines = body.splitlines()
            return "\n".join(f"    {line}" for line in lines)

        out = _CODE_FENCE_RE.sub(_fence_sub, out)

        # Bullets → "• "
        out = _BULLET_RE.sub(r"• \1", out)

        # Bold / inline code / links are already lark_md-native: no-op, but
        # running the regexes ensures we catch malformed input early.
        out = _BOLD_RE.sub(r"**\1**", out)
        out = _INLINE_CODE_RE.sub(r"`\1`", out)
        out = _LINK_RE.sub(r"[\1](\2)", out)

        return out
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("markdown_to_feishu_post failed, falling back to plain: %s", exc)
        return text


# ---------------------------------------------------------------------------
# FeishuSender
# ---------------------------------------------------------------------------


class FeishuSender:
    """Async wrapper over ``lark_oapi.Client`` for outbound Feishu calls.

    Parameters
    ----------
    app_id, app_secret:
        Credentials from the Feishu developer console.
    default_chat_id:
        Optional fallback ``open_chat_id`` used when callers don't pass one.
    """

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        default_chat_id: str | None = None,
    ) -> None:
        self._app_id = app_id
        self._app_secret = app_secret
        self.default_chat_id = default_chat_id
        self._client: Any = None  # lazily built lark_oapi.Client
        self._muted = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _ensure_client(self) -> Any:
        """Build the ``lark_oapi.Client`` on first use (lazy SDK import)."""
        if self._client is not None:
            return self._client
        try:
            import lark_oapi as lark  # type: ignore
        except ImportError as exc:  # pragma: no cover — verified in tests via mock
            raise RuntimeError(
                "lark_oapi is not installed. Add 'lark-oapi>=1.4.0' to "
                "backend/requirements.txt and reinstall."
            ) from exc
        self._client = (
            lark.Client.builder()
            .app_id(self._app_id)
            .app_secret(self._app_secret)
            .build()
        )
        return self._client

    async def start(self) -> None:
        """No-op — the SDK client is built on first use."""
        return None

    async def stop(self) -> None:
        """No-op — ``lark_oapi.Client`` has no close method."""
        self._client = None

    def is_muted(self) -> bool:
        return self._muted

    def set_muted(self, muted: bool) -> None:
        self._muted = bool(muted)

    # ------------------------------------------------------------------
    # Outbound helpers
    # ------------------------------------------------------------------

    async def send_message(
        self,
        chat_id: str,
        text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> bool:
        """Send a text message. If ``reply_markup`` is supplied it is treated
        as a signal to promote the message to an interactive card so the
        associated buttons survive the transport.
        """
        target = chat_id or self.default_chat_id
        if not target:
            logger.warning("FeishuSender.send_message: no chat_id / default set")
            return False

        if reply_markup is not None:
            # Promote to an interactive card so buttons survive. Reply markup
            # from the legacy Telegram path uses ``inline_keyboard``; we
            # convert it into a plain evidence card on the fly.
            card = _reply_markup_to_card(text, reply_markup)
            return await self.send_card(target, card)

        body = markdown_to_feishu_post(text)
        return await self._send_text(target, body)

    async def send_photo(
        self,
        chat_id: str,
        image_path: str,
        caption: str = "",
        reply_markup: dict[str, Any] | None = None,
    ) -> bool:
        """Upload an image then send it as an interactive card with caption."""
        target = chat_id or self.default_chat_id
        if not target:
            logger.warning("FeishuSender.send_photo: no chat_id / default set")
            return False

        try:
            image_key = await asyncio.to_thread(self._upload_image_sync, image_path)
        except Exception as exc:
            logger.error("Feishu image upload failed: %s", exc)
            return False
        if not image_key:
            return False

        # Compose a card: caption (if any) → image → action buttons
        elements: list[dict[str, Any]] = []
        if caption:
            elements.append(
                {"tag": "div", "text": {"tag": "lark_md", "content": markdown_to_feishu_post(caption)}}
            )
        elements.append({"tag": "img", "img_key": image_key, "alt": {"tag": "plain_text", "content": caption or "image"}})

        if reply_markup is not None:
            card = _reply_markup_to_card("", reply_markup)
            # Prepend our image/caption elements before the reply-markup body.
            card_elements = card.get("elements", [])
            card["elements"] = elements + card_elements
            return await self.send_card(target, card)

        card = {"config": {"wide_screen_mode": True}, "elements": elements}
        return await self.send_card(target, card)

    async def send_card(self, chat_id: str, card: dict[str, Any]) -> bool:
        """Send an interactive card (``msg_type="interactive"``)."""
        target = chat_id or self.default_chat_id
        if not target:
            return False
        return await asyncio.to_thread(self._send_card_sync, target, card)

    async def edit_message(
        self,
        message_id: str,
        text: str,
        reply_markup: dict[str, Any] | None = None,
        card: dict[str, Any] | None = None,
    ) -> bool:
        """Edit an existing card in place via ``im.v1.message.patch``.

        Resolution order for the new card body:

        1. Explicit ``card=`` argument — fully-constructed Feishu card,
           used by :class:`FeishuGateway` to swap evidence ↔ back views.
        2. ``reply_markup`` (legacy Telegram path) — translated via
           :func:`_reply_markup_to_card`, which can only emit an evidence
           card and is therefore unsuitable for the toggle case.
        3. Plain text fallback — wraps ``text`` in a minimal card.
        """
        if card is None:
            if reply_markup is not None:
                card = _reply_markup_to_card(text, reply_markup)
            else:
                card = build_plain_card(markdown_to_feishu_post(text))
        return await asyncio.to_thread(self._patch_card_sync, message_id, card)

    async def answer_callback(
        self,
        callback_id: str,
        text: str = "",
        show_alert: bool = False,
    ) -> bool:
        """Feishu has no standalone callback ACK endpoint — the card
        transport replies to the HTTP/WS event inline with a ``toast`` field.
        This method exists for :class:`BaseGateway` compatibility and is a
        no-op in practice.
        """
        return True

    # ------------------------------------------------------------------
    # Sync helpers running inside ``asyncio.to_thread``
    # ------------------------------------------------------------------

    def _send_text_sync(self, chat_id: str, text: str) -> bool:
        try:
            import json as _json

            from lark_oapi.api.im.v1 import (  # type: ignore
                CreateMessageRequest,
                CreateMessageRequestBody,
            )

            client = self._ensure_client()
            req = (
                CreateMessageRequest.builder()
                .receive_id_type("chat_id")
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(chat_id)
                    .msg_type("text")
                    .content(_json.dumps({"text": text}, ensure_ascii=False))
                    .build()
                )
                .build()
            )
            resp = client.im.v1.message.create(req)
            if not resp.success():
                logger.warning(
                    "Feishu send_text failed: code=%s msg=%s", resp.code, resp.msg
                )
                return False
            return True
        except Exception as exc:
            logger.error("Feishu _send_text_sync error: %s", exc, exc_info=True)
            return False

    async def _send_text(self, chat_id: str, text: str) -> bool:
        return await asyncio.to_thread(self._send_text_sync, chat_id, text)

    def _send_card_sync(self, chat_id: str, card: dict[str, Any]) -> bool:
        try:
            import json as _json

            import lark_oapi as lark  # type: ignore  # noqa: F401
            from lark_oapi.api.im.v1 import (  # type: ignore
                CreateMessageRequest,
                CreateMessageRequestBody,
            )

            client = self._ensure_client()
            req = (
                CreateMessageRequest.builder()
                .receive_id_type("chat_id")
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(chat_id)
                    .msg_type("interactive")
                    .content(_json.dumps(card, ensure_ascii=False))
                    .build()
                )
                .build()
            )
            resp = client.im.v1.message.create(req)
            if not resp.success():
                logger.warning(
                    "Feishu send_card failed: code=%s msg=%s", resp.code, resp.msg
                )
                return False
            return True
        except Exception as exc:
            logger.error("Feishu _send_card_sync error: %s", exc, exc_info=True)
            return False

    def _patch_card_sync(self, message_id: str, card: dict[str, Any]) -> bool:
        try:
            import json as _json

            from lark_oapi.api.im.v1 import (  # type: ignore
                PatchMessageRequest,
                PatchMessageRequestBody,
            )

            client = self._ensure_client()
            req = (
                PatchMessageRequest.builder()
                .message_id(message_id)
                .request_body(
                    PatchMessageRequestBody.builder()
                    .content(_json.dumps(card, ensure_ascii=False))
                    .build()
                )
                .build()
            )
            resp = client.im.v1.message.patch(req)
            if not resp.success():
                logger.warning(
                    "Feishu patch_card failed: code=%s msg=%s", resp.code, resp.msg
                )
                return False
            return True
        except Exception as exc:
            logger.error("Feishu _patch_card_sync error: %s", exc, exc_info=True)
            return False

    def _upload_image_sync(self, image_path: str) -> str | None:
        try:
            from lark_oapi.api.im.v1 import (  # type: ignore
                CreateImageRequest,
                CreateImageRequestBody,
            )

            client = self._ensure_client()
            with open(image_path, "rb") as fh:
                req = (
                    CreateImageRequest.builder()
                    .request_body(
                        CreateImageRequestBody.builder()
                        .image_type("message")
                        .image(fh)
                        .build()
                    )
                    .build()
                )
                resp = client.im.v1.image.create(req)
            if not resp.success():
                logger.warning(
                    "Feishu upload_image failed: code=%s msg=%s",
                    resp.code,
                    resp.msg,
                )
                return None
            return getattr(resp.data, "image_key", None)
        except Exception as exc:
            logger.error("Feishu _upload_image_sync error: %s", exc, exc_info=True)
            return None


# ---------------------------------------------------------------------------
# Legacy reply_markup → card adapter
# ---------------------------------------------------------------------------


def _reply_markup_to_card(text: str, reply_markup: dict[str, Any]) -> dict[str, Any]:
    """Translate a Telegram-style ``inline_keyboard`` blob into a Feishu card.

    The Telegram shape is ``{"inline_keyboard": [[{"text": ..., "callback_data": "evidence:<hash>"}]]}``.
    We extract the first ``evidence:``/``restore:`` button and re-emit an
    equivalent Feishu card via :func:`build_evidence_card`, keeping the same
    message hash so :class:`FactVerifier` lookups remain identical.

    Unknown shapes fall back to a plain text card.
    """
    try:
        keyboard = reply_markup.get("inline_keyboard") or []
        for row in keyboard:
            for btn in row:
                data = str(btn.get("callback_data", ""))
                if ":" not in data:
                    continue
                action, msg_hash = data.split(":", 1)
                if action in ("evidence", "restore"):
                    return build_evidence_card(
                        markdown_to_feishu_post(text), msg_hash,
                    )
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("reply_markup_to_card fallback: %s", exc)
    return build_plain_card(markdown_to_feishu_post(text))
