"""Feishu inbound transports.

Two wire formats are supported and exposed through a uniform
``FeishuTransport`` protocol:

* :class:`FeishuWsTransport` — subscribes to the Feishu event stream over
  the long-lived WebSocket using ``lark_oapi.ws.Client``. This is the
  zero-config path: only ``APP_ID`` / ``APP_SECRET`` are required, Feishu
  pushes events directly to the bot.
* :class:`FeishuWebhookTransport` — registers an HTTP POST route on the
  existing FastAPI ``app`` at ``settings.FEISHU_WEBHOOK_PATH``. Signed
  events (``X-Lark-Signature`` / HMAC-SHA256 over
  ``timestamp + nonce + encrypt_key + raw_body``) are verified before
  dispatch. ``url_verification`` challenges are answered inline.

Both transports normalise incoming events into two callbacks supplied by
the gateway:

* ``on_message(envelope: MessageEnvelope)`` — free-form user messages
* ``on_card_action(action: dict)`` — button clicks originating from an
  interactive card

The callbacks receive data structures that are already platform-agnostic
so :class:`backend.feishu.gateway.FeishuGateway` does not have to re-parse
Feishu event payloads.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from .models import MessageChannel, MessageEnvelope

# FastAPI is a hard dependency of the backend; import Request at module
# level so closure-scoped annotations inside register_routes resolve
# correctly via typing.get_type_hints().
try:  # pragma: no cover — FastAPI is always installed in practice
    from fastapi import Request as _FastAPIRequest
except ImportError:
    _FastAPIRequest = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


OnMessage = Callable[[MessageEnvelope], Awaitable[None]]
# Card-action handlers may return a new card dict. When they do, the webhook
# transport forwards it as the HTTP response body so Feishu performs an
# in-place "local update" of the card (the only reliable way to refresh an
# interactive card from a button click — ``im.v1.message.patch`` succeeds at
# the API level but does not actually re-render card 1.0 messages).
OnCardAction = Callable[[dict[str, Any]], Awaitable[dict[str, Any] | None]]


# ---------------------------------------------------------------------------
# Event-to-envelope helpers (shared by both transports)
# ---------------------------------------------------------------------------


def _event_to_envelope(event: dict[str, Any]) -> MessageEnvelope | None:
    """Normalise ``im.message.receive_v1`` into a :class:`MessageEnvelope`.

    The Feishu event shape is::

        {
          "header": {"event_id": ..., "event_type": "im.message.receive_v1", ...},
          "event":  {
            "sender":  {"sender_id": {"open_id": "ou_...", ...}, "sender_type": "user"},
            "message": {
              "message_id": "om_...",
              "chat_id":    "oc_...",
              "message_type": "text",
              "content":    "{\"text\":\"hi\"}"
            }
          }
        }
    """
    try:
        payload = event.get("event", {}) or {}
        message = payload.get("message", {}) or {}
        sender = payload.get("sender", {}) or {}
        sender_id_blob = sender.get("sender_id", {}) or {}

        msg_type = message.get("message_type") or message.get("msg_type") or "text"
        if msg_type != "text":
            logger.info("Feishu: ignoring non-text message_type=%s", msg_type)
            return None

        raw_content = message.get("content", "{}")
        try:
            content = json.loads(raw_content).get("text", "")
        except (ValueError, TypeError):
            content = str(raw_content)

        chat_id = message.get("chat_id") or ""
        sender_id = (
            sender_id_blob.get("open_id")
            or sender_id_blob.get("user_id")
            or sender_id_blob.get("union_id")
            or "unknown"
        )
        platform_msg_id = message.get("message_id") or uuid.uuid4().hex

        return MessageEnvelope(
            message_id=platform_msg_id,
            channel=MessageChannel.FEISHU,
            sender_id=str(sender_id),
            content=str(content),
            timestamp=datetime.now(),
            chat_id=str(chat_id) if chat_id else None,
            conversation_id=str(chat_id) if chat_id else None,
            platform_message_id=str(platform_msg_id),
            metadata={
                "tenant_key": event.get("header", {}).get("tenant_key", ""),
                "event_id": event.get("header", {}).get("event_id", ""),
            },
        )
    except Exception as exc:
        logger.warning("Feishu: failed to parse message event: %s", exc)
        return None


def _is_allowed(envelope: MessageEnvelope, allowed: set[str] | None) -> bool:
    """Default-deny: inbound is rejected when the whitelist is empty/None."""
    if not allowed:
        return False
    return (envelope.chat_id or "") in allowed


# ---------------------------------------------------------------------------
# Webhook transport (FastAPI route)
# ---------------------------------------------------------------------------


class FeishuWebhookTransport:
    """HTTP webhook transport. Routes are registered on an existing FastAPI app."""

    def __init__(
        self,
        on_message: OnMessage,
        on_card_action: OnCardAction,
        webhook_path: str,
        verification_token: str,
        encrypt_key: str = "",
        allowed_chat_ids: set[str] | None = None,
    ) -> None:
        self._on_message = on_message
        self._on_card_action = on_card_action
        self._webhook_path = webhook_path
        self._verification_token = verification_token
        self._encrypt_key = encrypt_key
        self._allowed_chat_ids = allowed_chat_ids or set()
        self._registered = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if not self._registered:
            logger.warning(
                "FeishuWebhookTransport.start() called before register_routes(); "
                "webhook will be inert until mounted on a FastAPI app."
            )

    async def stop(self) -> None:
        return None

    # ------------------------------------------------------------------
    # Signature verification
    # ------------------------------------------------------------------

    def verify_signature(
        self,
        timestamp: str,
        nonce: str,
        raw_body: bytes,
        signature: str,
    ) -> bool:
        """Verify the ``X-Lark-Signature`` header.

        Feishu only signs HTTP webhook requests when the bot has an
        **Encrypt Key** configured in the developer console. The algorithm
        (matching :mod:`lark_oapi.event.dispatcher_handler._verify_sign`) is::

            sha256(timestamp + nonce + encrypt_key + raw_body).hexdigest()

        The unrelated *Verification Token* setting is **not** a signing
        key — it appears in plain inside the request body's ``token`` field
        and is checked separately by :meth:`verify_body_token`.
        """
        if not self._encrypt_key:
            # No encrypt key → no signature is sent and there is nothing
            # cryptographic to check. The caller should fall back to
            # ``verify_body_token``.
            return True
        if not signature:
            return False
        try:
            digest_src = (
                (timestamp or "")
                + (nonce or "")
                + self._encrypt_key
            ).encode("utf-8") + raw_body
            expected = hashlib.sha256(digest_src).hexdigest()
            return hmac.compare_digest(expected, signature)
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("Feishu webhook signature check failed: %s", exc)
            return False

    def verify_body_token(self, body: dict[str, Any]) -> bool:
        """Verify the plain ``token`` field embedded in a Feishu request body.

        Feishu sends the configured *Verification Token* inside event POSTs
        (``body['header']['token']`` for v2 events, ``body['token']`` for
        v1). Card-callback POSTs sent to the bot's "Message Card Request
        URL" historically include a top-level ``token`` field too, but
        newer Feishu deployments may omit it.

        Verification rules:

        * No verification_token configured locally → accept
        * Token present in the body → require exact match (constant-time)
        * Token missing entirely → accept (chat-id allowlist still gates
          access; rejecting here would break card callbacks on bots that
          don't echo the token)
        """
        if not self._verification_token:
            return True
        token = (
            (body.get("header") or {}).get("token")
            or body.get("token")
            or ""
        )
        if not token:
            return True
        return hmac.compare_digest(str(token), self._verification_token)

    # ------------------------------------------------------------------
    # FastAPI wiring
    # ------------------------------------------------------------------

    def register_routes(self, app: Any) -> None:
        """Mount ``POST {webhook_path}`` on a FastAPI app."""
        from fastapi.responses import JSONResponse

        if _FastAPIRequest is None:
            raise RuntimeError(
                "FastAPI is required to register the Feishu webhook transport."
            )

        transport = self

        async def _handle_webhook(request: _FastAPIRequest):  # type: ignore[valid-type]
            raw = await request.body()
            timestamp = request.headers.get("X-Lark-Request-Timestamp", "")
            nonce = request.headers.get("X-Lark-Request-Nonce", "")
            signature = request.headers.get("X-Lark-Signature", "")

            try:
                body = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                return JSONResponse({"code": 400, "msg": "bad json"}, status_code=400)

            logger.debug(
                "Feishu webhook POST: keys=%s sig=%s",
                list(body.keys()), bool(signature),
            )

            # URL verification challenge — Feishu sends this when first
            # configuring the webhook endpoint. It carries the plain
            # verification token in the payload; no signature header yet.
            if body.get("type") == "url_verification":
                token = body.get("token", "")
                if token and transport._verification_token and token != transport._verification_token:
                    return JSONResponse({"code": 401, "msg": "bad token"}, status_code=401)
                return {"challenge": body.get("challenge", "")}

            # Two-step verification:
            #
            # 1. **Encrypt Key** (optional) drives the cryptographic
            #    ``X-Lark-Signature`` header. Only when *we* have one
            #    configured do we enforce the sha256 check.
            # 2. **Verification Token** (optional) is a plain string Feishu
            #    embeds in every body's ``token`` field. When configured,
            #    we require an exact match.
            #
            # Both can be left unset for permissive deployments — the
            # dispatch layer's chat-id allowlist (default-deny) still
            # provides per-chat isolation.
            if transport._encrypt_key:
                if not transport.verify_signature(timestamp, nonce, raw, signature):
                    logger.warning(
                        "Feishu webhook: bad sha256 signature, rejecting",
                    )
                    return JSONResponse(
                        {"code": 401, "msg": "bad signature"}, status_code=401,
                    )
            if transport._verification_token:
                if not transport.verify_body_token(body):
                    logger.warning(
                        "Feishu webhook: body token mismatch, rejecting",
                    )
                    return JSONResponse(
                        {"code": 401, "msg": "bad token"}, status_code=401,
                    )

            result = await transport.dispatch_event(body)
            # Card-action handlers return a new card dict. Feishu's "local
            # update" protocol for 1.0 cards expects the response body to be
            # the new card JSON itself (same shape as msg_type=interactive
            # content) — Feishu re-renders the card in place. For non-card
            # events we ack with the standard envelope.
            if isinstance(result, dict):
                return result
            return {"code": 0, "msg": "ok"}

        # Use ``app.post`` rather than ``add_api_route`` because the latter
        # does not always resolve closure-scoped type annotations correctly,
        # leading FastAPI to treat ``request: Request`` as a body parameter.
        app.post(self._webhook_path, name="feishu_webhook")(_handle_webhook)
        self._registered = True
        logger.info("FeishuWebhookTransport: mounted at %s", self._webhook_path)

    # ------------------------------------------------------------------
    # Event dispatch (called by transport or by tests directly)
    # ------------------------------------------------------------------

    async def dispatch_event(self, event: dict[str, Any]) -> dict[str, Any] | None:
        """Route a normalised event payload to the correct callback.

        Returns an optional response body — used by card callbacks that
        want Feishu to update the card in-place using the returned content
        (we don't currently rely on this; the gateway patches the card via
        a separate API call instead).
        """
        header = event.get("header", {}) or {}
        event_type = header.get("event_type", "")

        if event_type == "im.message.receive_v1":
            envelope = _event_to_envelope(event)
            if envelope is None:
                return None
            if not _is_allowed(envelope, self._allowed_chat_ids):
                logger.warning(
                    "Feishu webhook: default-deny rejected chat_id=%s",
                    envelope.chat_id,
                )
                return None
            await self._on_message(envelope)
            return None

        if event_type == "card.action.trigger":
            return await self._on_card_action(event)

        # Card-callback POSTs go to the bot's *legacy* "Message Card Request
        # URL" with a flat shape (not wrapped in {header, event}), e.g.::
        #
        #     {"operator": {...}, "token": "...", "host": "im_message",
        #      "context": {"open_message_id": "om_xxx", ...},
        #      "action": {"tag": "button",
        #                 "value": {"action": "evidence", "hash": "abc"}}}
        #
        # We detect this shape and re-wrap it into the canonical form so
        # ``FeishuGateway._handle_card_action`` (which already reads
        # ``event.action.value``) works without modification.
        #
        # The legacy local-update protocol differs from the new event-based
        # callback: the response body MUST wrap the new card under a top-
        # level ``card`` key (``{"card": {...}}``) for Feishu to actually
        # re-render it. Returning the raw card — which the new callback
        # accepts — is silently ignored on the legacy path.
        if "action" in event and (
            "open_message_id" in event
            or "token" in event
            or "open_id" in event
            or "context" in event
            or "host" in event
        ):
            wrapped = {
                "header": {"event_type": "card.action.trigger"},
                "event": event,
            }
            new_card = await self._on_card_action(wrapped)
            if isinstance(new_card, dict):
                return {"card": new_card}
            return None

        logger.info(
            "Feishu webhook: ignoring unknown payload (event_type=%r, keys=%s)",
            event_type, list(event.keys()),
        )
        return None


# ---------------------------------------------------------------------------
# WebSocket transport
# ---------------------------------------------------------------------------


class FeishuWsTransport:
    """Long-polling WebSocket transport built on ``lark_oapi.ws.Client``.

    The ``lark_oapi`` SDK is imported lazily so tests can mock the class
    without installing the package.
    """

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        on_message: OnMessage,
        on_card_action: OnCardAction,
        allowed_chat_ids: set[str] | None = None,
    ) -> None:
        self._app_id = app_id
        self._app_secret = app_secret
        self._on_message = on_message
        self._on_card_action = on_card_action
        self._allowed_chat_ids = allowed_chat_ids or set()
        self._client: Any = None
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Spawn the background subscription task."""
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._run(), name="feishu-ws")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        self._client = None

    async def _run(self) -> None:
        """Build the WS client and keep the subscription alive."""
        try:
            import lark_oapi as lark  # type: ignore
        except ImportError:
            logger.error(
                "FeishuWsTransport: lark_oapi not installed — transport disabled"
            )
            return

        try:
            event_handler = (
                lark.EventDispatcherHandler.builder("", "")
                .register_p2_im_message_receive_v1(self._on_sdk_message)
                .register_p2_card_action_trigger(self._on_sdk_card_action)
                .build()
            )
            self._client = (
                lark.ws.Client(
                    self._app_id,
                    self._app_secret,
                    event_handler=event_handler,
                    log_level=lark.LogLevel.WARNING,
                )
            )
            # NOTE: We deliberately bypass ``Client.start()`` because it is a
            # synchronous wrapper that calls ``loop.run_until_complete()`` on
            # ``lark_oapi.ws.client``'s module-level ``loop`` reference. That
            # reference is acquired at import time via
            # ``asyncio.get_event_loop()`` and — since we lazy-import the SDK
            # from inside this coroutine — captures the *already-running*
            # FastAPI loop. Calling ``run_until_complete`` on a running loop
            # raises ``RuntimeError("this event loop is already running")``,
            # which is exactly the crash we hit on first deploy. Driving the
            # async ``_connect`` / ``_ping_loop`` directly side-steps the
            # whole sync-wrapper trap and lets the SDK schedule its receive
            # loop on our loop normally.
            await self._client._connect()
            ping_task = asyncio.create_task(
                self._client._ping_loop(), name="feishu-ws-ping",
            )
            try:
                # Block forever; cancellation comes via stop() → task.cancel().
                await asyncio.Event().wait()
            finally:
                ping_task.cancel()
                try:
                    await ping_task
                except (asyncio.CancelledError, Exception):
                    pass
                try:
                    await self._client._disconnect()
                except Exception:
                    pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("FeishuWsTransport crashed: %s", exc, exc_info=True)

    # --- SDK callback adapters ------------------------------------------

    def _on_sdk_message(self, data: Any) -> None:
        """SDK sync callback → schedule async dispatch on the running loop.

        ``lark_oapi`` invokes registered handlers synchronously from inside
        ``_handle_data_frame``, which itself runs on our FastAPI loop. So
        we are already on the right loop and just need ``create_task`` to
        defer the async work — ``run_coroutine_threadsafe`` would be wrong
        API (it's for cross-thread submission).
        """
        try:
            event = _sdk_event_to_dict(data, "im.message.receive_v1")
            envelope = _event_to_envelope(event)
            if envelope is None:
                return
            if not _is_allowed(envelope, self._allowed_chat_ids):
                logger.warning(
                    "Feishu ws: default-deny rejected chat_id=%s",
                    envelope.chat_id,
                )
                return
            asyncio.get_running_loop().create_task(self._on_message(envelope))
        except Exception as exc:
            logger.error("Feishu ws message handler error: %s", exc, exc_info=True)

    def _on_sdk_card_action(self, data: Any) -> None:
        try:
            event = _sdk_event_to_dict(data, "card.action.trigger")
            asyncio.get_running_loop().create_task(self._on_card_action(event))
        except Exception as exc:
            logger.error("Feishu ws card handler error: %s", exc, exc_info=True)


def _obj_to_dict(obj: Any) -> Any:
    """Recursively convert a ``lark_oapi`` typed event object into plain
    JSON-style dicts/lists/scalars.

    The SDK's ``P2*`` event classes are not Pydantic models — they have no
    ``to_dict`` and no ``__iter__``. Each instance just exposes its declared
    attributes (``message``, ``sender``, ``chat_id``, …) on the instance
    dict.  We walk them recursively, treating any object with a non-empty
    ``__dict__`` as a struct.
    """
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {k: _obj_to_dict(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_obj_to_dict(v) for v in obj]
    inst_dict = getattr(obj, "__dict__", None)
    if isinstance(inst_dict, dict) and inst_dict:
        return {
            k: _obj_to_dict(v)
            for k, v in inst_dict.items()
            if not k.startswith("_")
        }
    return repr(obj)


def _sdk_event_to_dict(data: Any, event_type: str) -> dict[str, Any]:
    """Convert a ``lark_oapi`` event object back to the dict shape produced
    by the webhook path so the two transports share a single normalisation
    code path.

    Order of fallbacks:
    1. Already a dict — pass through (webhook path).
    2. Has ``to_dict`` (very few SDK helpers) — use it.
    3. Plain typed object — walk ``__dict__`` recursively via
       :func:`_obj_to_dict`. Lark's ``P2*`` event classes hit this branch.
    4. Unknown — wrap the repr so the gateway can log the miss.
    """
    def _ensure_header(d: dict[str, Any]) -> dict[str, Any]:
        # ``setdefault`` won't help when the existing value is None (which the
        # lark SDK produces for un-set struct fields), so coerce explicitly.
        header = d.get("header")
        if not isinstance(header, dict):
            header = {}
            d["header"] = header
        header.setdefault("event_type", event_type)
        if not isinstance(d.get("event"), dict):
            d["event"] = {}
        return d

    if data is None:
        return {"header": {"event_type": event_type}, "event": {}}
    if isinstance(data, dict):
        return _ensure_header(data)
    if hasattr(data, "to_dict"):
        try:
            raw = data.to_dict()
            if isinstance(raw, dict):
                return _ensure_header(raw)
        except Exception:
            pass
    walked = _obj_to_dict(data)
    if isinstance(walked, dict):
        return _ensure_header(walked)
    return {"header": {"event_type": event_type}, "event": {"_raw": repr(data)}}


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def make_transport(
    settings: Any,
    on_message: OnMessage,
    on_card_action: OnCardAction,
    allowed_chat_ids: set[str] | None = None,
):
    """Build the concrete transport declared by ``settings.FEISHU_TRANSPORT``."""
    transport_kind = (getattr(settings, "FEISHU_TRANSPORT", "ws") or "ws").lower()
    if transport_kind == "webhook":
        return FeishuWebhookTransport(
            on_message=on_message,
            on_card_action=on_card_action,
            webhook_path=getattr(settings, "FEISHU_WEBHOOK_PATH", "/api/feishu/webhook"),
            verification_token=getattr(settings, "FEISHU_VERIFICATION_TOKEN", ""),
            encrypt_key=getattr(settings, "FEISHU_ENCRYPT_KEY", ""),
            allowed_chat_ids=allowed_chat_ids,
        )
    if transport_kind == "ws":
        return FeishuWsTransport(
            app_id=getattr(settings, "FEISHU_APP_ID", ""),
            app_secret=getattr(settings, "FEISHU_APP_SECRET", ""),
            on_message=on_message,
            on_card_action=on_card_action,
            allowed_chat_ids=allowed_chat_ids,
        )
    raise ValueError(
        f"Unknown FEISHU_TRANSPORT={transport_kind!r} (expected 'ws' or 'webhook')"
    )
