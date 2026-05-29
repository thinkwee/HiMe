"""WeixinSender — outbound HTTP wrapper for the iLink bot API.

Mirrors :class:`backend.telegram.sender.TelegramSender` so that
:class:`backend.weixin.gateway.WeixinGateway` can forward calls coming
through :class:`backend.messaging.base.BaseGateway` without per-platform
branches at the call site.

Design notes:

* iLink's ``sendmessage`` endpoint requires the ``context_token`` from a
  recent inbound message — the platform does not allow unsolicited
  broadcasts. The sender resolves the token via the ``get_context_token``
  callback supplied by the gateway, which in turn reads from the poller's
  per-user cache. If no token is available the call short-circuits and
  returns ``False`` with a clear log line so callers can surface the issue
  rather than silently dropping the message.
* No card / inline-keyboard surface exists on the ClawBot bot API, so
  ``edit_message`` / ``answer_callback`` are no-ops at the gateway level.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from .cdn import (
    UploadedImage,
    aes_ecb_padded_size,
    encrypt_aes_ecb,
    new_aeskey,
    new_filekey,
    upload_ciphertext,
)
from .qr_login import CHANNEL_VERSION, common_headers

logger = logging.getLogger(__name__)

ILINK_BASE = "https://ilinkai.weixin.qq.com"
# iLink rejects very long single items; keep a conservative cap and
# truncate on the client side instead of waiting for the server to error.
_MAX_TEXT_LENGTH = 4000
_TRUNCATION_SUFFIX = "\n\n…(truncated)"


class WeixinSender:
    """Async wrapper for ``POST /ilink/bot/sendmessage`` and related calls."""

    def __init__(
        self,
        bot_token: str,
        default_user_id: str | None = None,
        get_context_token: Callable[[str], str | None] | None = None,
    ) -> None:
        self._token = bot_token
        self._default_user_id = default_user_id
        self.default_chat_id = default_user_id
        self._get_context_token = get_context_token
        self._client: httpx.AsyncClient | None = None
        self._muted_until: float | None = None

    # ------------------------------------------------------------------
    # Mute control (suppress autonomous report pushes)
    # ------------------------------------------------------------------
    def mute(self, duration_seconds: float) -> None:
        self._muted_until = time.time() + duration_seconds

    def unmute(self) -> None:
        self._muted_until = None

    def is_muted(self) -> bool:
        if self._muted_until is None:
            return False
        if time.time() >= self._muted_until:
            self._muted_until = None
            return False
        return True

    def mute_remaining(self) -> float | None:
        if not self.is_muted():
            return None
        return self._muted_until - time.time() if self._muted_until else None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=15.0)
            logger.info("WeixinSender started")

    async def stop(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
            logger.info("WeixinSender stopped")

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------
    def _resolve_context(
        self, user_id: str, context_token: str | None,
    ) -> str | None:
        if context_token:
            return context_token
        if self._get_context_token is not None:
            return self._get_context_token(user_id)
        return None

    async def send_message(
        self,
        text: str,
        user_id: str | None = None,
        context_token: str | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> bool:
        """Send a text message; returns True on success.

        ``reply_markup`` is accepted for ``BaseGateway`` shape parity but is
        ignored — WeChat ClawBot has no native inline-keyboard surface.
        """
        if reply_markup is not None:
            logger.debug("WeChat: reply_markup ignored (no inline keyboard)")

        target = user_id or self._default_user_id
        if not target:
            logger.warning("WeixinSender.send_message: no user_id available")
            return False

        token = self._resolve_context(target, context_token)
        if not token:
            logger.warning(
                "WeixinSender.send_message: no context_token for user %s — "
                "iLink rejects unsolicited messages. Have they messaged the "
                "bot recently?",
                target,
            )
            return False

        if len(text) > _MAX_TEXT_LENGTH:
            text = (
                text[: _MAX_TEXT_LENGTH - len(_TRUNCATION_SUFFIX)]
                + _TRUNCATION_SUFFIX
            )

        # iLink's sendmessage expects a fully-spelled message envelope:
        # ``message_type=2`` / ``message_state=2`` are the values used by the
        # ClawBot reference implementation for "outbound user-facing reply",
        # and ``client_id`` is a per-call UUID the server uses for idempotency.
        # ``item_list`` items are typed: ``type=1`` + ``text_item.text`` for
        # plain text. ``from_user_id`` is left blank and filled server-side
        # from the bot's own iLink identity tied to the bot_token.
        payload = {
            "msg": {
                "from_user_id": "",
                "to_user_id": target,
                "client_id": uuid.uuid4().hex,
                "message_type": 2,
                "message_state": 2,
                "context_token": token,
                "item_list": [
                    {"type": 1, "text_item": {"text": text}},
                ],
            },
            "base_info": {"channel_version": CHANNEL_VERSION},
        }
        return await self._post("/ilink/bot/sendmessage", payload)

    async def send_photo(
        self,
        photo_path: str,
        user_id: str | None = None,
        context_token: str | None = None,
    ) -> bool:
        """Upload a local image to the iLink CDN and emit it as an image
        message. Returns True on success.

        Note: WeChat has no native "photo with caption" surface — the image
        and any accompanying text must be separate ``sendmessage`` items.
        Callers that want a caption should emit a follow-up ``send_message``
        themselves (or rely on :class:`WeixinGateway.send_photo` which does
        nothing extra — see the gateway for the rationale).
        """
        target = user_id or self._default_user_id
        if not target:
            logger.warning("WeixinSender.send_photo: no user_id available")
            return False
        token = self._resolve_context(target, context_token)
        if not token:
            logger.warning(
                "WeixinSender.send_photo: no context_token for user %s — "
                "iLink rejects unsolicited messages.", target,
            )
            return False
        path = Path(photo_path)
        if not path.is_file():
            logger.warning(
                "WeixinSender.send_photo: file not found: %s", photo_path,
            )
            return False
        try:
            uploaded = await self._upload_image(path, target, token)
        except Exception as exc:
            logger.warning(
                "WeixinSender.send_photo: CDN upload failed (%s): %s",
                photo_path, exc,
            )
            return False
        # ``aes_key`` in the wire format is base64 of the *hex* representation
        # of the AES key (not base64 of the raw bytes). This double-encoding
        # matches what the public SDKs do and is what the receiving WeChat
        # client expects when decrypting the CDN download.
        aes_key_field = base64.b64encode(
            uploaded.aeskey_hex.encode("ascii"),
        ).decode("ascii")
        payload = {
            "msg": {
                "from_user_id": "",
                "to_user_id": target,
                "client_id": uuid.uuid4().hex,
                "message_type": 2,
                "message_state": 2,
                "context_token": token,
                "item_list": [
                    {
                        "type": 2,
                        "image_item": {
                            "media": {
                                "encrypt_query_param": uploaded.encrypt_query_param,
                                "aes_key": aes_key_field,
                                "encrypt_type": 1,
                            },
                            "mid_size": uploaded.ciphertext_size,
                        },
                    },
                ],
            },
            "base_info": {"channel_version": CHANNEL_VERSION},
        }
        return await self._post("/ilink/bot/sendmessage", payload)

    async def _upload_image(
        self, path: Path, to_user_id: str, context_token: str,
    ) -> UploadedImage:
        """Run the full getuploadurl → CDN POST pipeline for one image."""
        # Offload the blocking file read off the event loop.
        plaintext = await asyncio.to_thread(path.read_bytes)
        rawsize = len(plaintext)
        rawmd5 = hashlib.md5(plaintext, usedforsecurity=False).hexdigest()
        ciphersize = aes_ecb_padded_size(rawsize)
        filekey = new_filekey()
        aeskey = new_aeskey()
        upload_payload = {
            "filekey": filekey,
            "media_type": 1,  # IMAGE
            "to_user_id": to_user_id,
            "rawsize": rawsize,
            "rawfilemd5": rawmd5,
            "filesize": ciphersize,
            "no_need_thumb": True,
            "aeskey": aeskey.hex(),
            "context_token": context_token,
            "base_info": {"channel_version": CHANNEL_VERSION},
        }
        client = self._client or httpx.AsyncClient(timeout=30.0)
        owns_client = self._client is None
        try:
            resp = await client.post(
                f"{ILINK_BASE}/ilink/bot/getuploadurl",
                json=upload_payload,
                headers=common_headers(self._token),
                timeout=30.0,
            )
            if resp.status_code != 200:
                raise RuntimeError(
                    f"getuploadurl HTTP {resp.status_code}: {resp.text[:200]}",
                )
            data = resp.json()
            ret = data.get("ret")
            if ret not in (None, 0):
                raise RuntimeError(
                    f"getuploadurl ret={ret} err_msg={data.get('err_msg', '')!r}",
                )
            # AES-encrypting a multi-MB image is CPU-bound — keep it off the loop.
            ciphertext = await asyncio.to_thread(encrypt_aes_ecb, plaintext, aeskey)
            # CDN POST is allowed to take longer than the 15s default —
            # large images on slow networks routinely need 30s+.
            cdn_client = httpx.AsyncClient(timeout=60.0, follow_redirects=True)
            try:
                download_ref = await upload_ciphertext(
                    cdn_client,
                    upload_full_url=data.get("upload_full_url"),
                    upload_param=data.get("upload_param"),
                    filekey=filekey,
                    ciphertext=ciphertext,
                )
            finally:
                await cdn_client.aclose()
            return UploadedImage(
                encrypt_query_param=download_ref,
                aeskey_hex=aeskey.hex(),
                plaintext_size=rawsize,
                ciphertext_size=ciphersize,
            )
        finally:
            if owns_client:
                await client.aclose()

    async def send_typing(
        self,
        user_id: str,
        context_token: str | None = None,
    ) -> bool:
        """Best-effort typing indicator. Failures are not surfaced."""
        token = self._resolve_context(user_id, context_token)
        if not token:
            return False
        payload = {
            "msg": {
                "from_user_id": "",
                "to_user_id": user_id,
                "context_token": token,
            },
            "base_info": {"channel_version": CHANNEL_VERSION},
        }
        return await self._post(
            "/ilink/bot/sendtyping", payload, quiet=True,
        )

    # ------------------------------------------------------------------
    # Internal POST helper
    # ------------------------------------------------------------------
    async def _post(
        self,
        path: str,
        payload: dict[str, Any],
        quiet: bool = False,
    ) -> bool:
        client = self._client or httpx.AsyncClient(timeout=15.0)
        try:
            resp = await client.post(
                f"{ILINK_BASE}{path}",
                json=payload,
                headers=common_headers(self._token),
            )
            if resp.status_code != 200:
                if not quiet:
                    logger.warning(
                        "WeChat %s failed (%d): %s",
                        path, resp.status_code, resp.text[:300],
                    )
                return False
            # iLink returns HTTP 200 even for app-level errors; check ``ret``
            # so we don't silently swallow ``ret=1, err_msg="..."`` payloads.
            try:
                data = resp.json()
            except Exception:
                data = None
            ret = (data or {}).get("ret") if isinstance(data, dict) else None
            if ret in (None, 0):
                return True
            if not quiet:
                logger.warning(
                    "WeChat %s app error: ret=%s err_msg=%r",
                    path, ret, (data or {}).get("err_msg", ""),
                )
            return False
        except Exception as exc:
            if not quiet:
                logger.warning("WeChat %s error: %s", path, exc)
            return False
        finally:
            if self._client is None:
                await client.aclose()
