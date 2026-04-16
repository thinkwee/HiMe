"""Unit tests for the Feishu gateway surface.

These tests exercise the pieces of the Feishu gateway that do not
require a live Lark API: envelope routing through the shared
GatewayRegistry, interactive-card JSON shape, HMAC signature
verification, url_verification challenge handling, default-deny
allowlist behaviour, and the Telegram command-parser re-export shim.

The ``lark_oapi`` SDK is never touched — the FeishuSender instance
inside FeishuGateway is replaced with ``unittest.mock.MagicMock``
whenever outbound behaviour needs to be observed, and the WS transport
is constructed but never started.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from backend.feishu.cards import (
    build_back_card,
    build_evidence_card,
    build_plain_card,
)
from backend.feishu.transport import (
    FeishuWebhookTransport,
    _event_to_envelope,
)
from backend.messaging.base import BaseGateway, MessageChannel, MessageEnvelope
from backend.messaging.registry import GatewayRegistry

# ---------------------------------------------------------------------------
# Helpers — lightweight stub gateways to avoid pulling in real network code.
# ---------------------------------------------------------------------------


class _StubGateway(BaseGateway):
    """Test double: records every outbound call and does nothing else."""

    def __init__(self, channel: MessageChannel) -> None:
        self.channel = channel
        self.default_chat_id = f"default-{channel.value}"
        self.allowed_chat_ids = {self.default_chat_id}
        self.calls: list[dict[str, Any]] = []

    async def start(self) -> None:  # pragma: no cover
        return None

    async def stop(self) -> None:  # pragma: no cover
        return None

    async def send_message(self, text, chat_id=None, reply_to_message_id=None, reply_markup=None):
        self.calls.append({"op": "send_message", "text": text, "chat_id": chat_id})
        return True

    async def send_photo(self, photo_path, caption="", chat_id=None, reply_markup=None):
        self.calls.append({"op": "send_photo", "photo_path": photo_path})
        return True

    async def edit_message(self, chat_id, message_id, text, reply_markup=None):
        self.calls.append({"op": "edit_message", "chat_id": chat_id, "text": text})
        return True

    async def answer_callback(self, callback_id, text="", show_alert=False):
        self.calls.append({"op": "answer_callback", "callback_id": callback_id})
        return True


def _make_envelope(channel: MessageChannel, chat_id: str) -> MessageEnvelope:
    return MessageEnvelope(
        message_id=f"msg-{channel.value}",
        channel=channel,
        sender_id="u1",
        content="hello",
        chat_id=chat_id,
        conversation_id=chat_id,
    )


# ---------------------------------------------------------------------------
# 1. Envelope routing through GatewayRegistry
# ---------------------------------------------------------------------------


def test_envelope_routing() -> None:
    """Registering both gateways routes each envelope to its own channel."""
    registry = GatewayRegistry()
    tg = _StubGateway(MessageChannel.TELEGRAM)
    fs = _StubGateway(MessageChannel.FEISHU)
    registry.register(tg)
    registry.register(fs)

    tg_env = _make_envelope(MessageChannel.TELEGRAM, "default-telegram")
    fs_env = _make_envelope(MessageChannel.FEISHU, "default-feishu")

    assert registry.for_envelope(tg_env) is tg
    assert registry.for_envelope(fs_env) is fs
    assert len(registry) == 2


# ---------------------------------------------------------------------------
# 2-3. Card shape
# ---------------------------------------------------------------------------


def _find_button_value(card: dict[str, Any]) -> dict[str, Any]:
    """Traverse card JSON and return the first button's ``value`` dict."""
    for element in card.get("elements", []):
        if element.get("tag") == "action":
            for action in element.get("actions", []):
                if action.get("tag") == "button":
                    return action.get("value", {})
    raise AssertionError("No button value found in card")


def test_evidence_card_shape() -> None:
    card = build_evidence_card("Hi there", "abc123")
    assert "header" in card
    assert "elements" in card
    value = _find_button_value(card)
    assert value["action"] == "evidence"
    assert value["hash"] == "abc123"


def test_back_card_shape() -> None:
    card = build_back_card("Evidence trail...", "abc123")
    assert "header" in card
    assert "elements" in card
    value = _find_button_value(card)
    assert value["action"] == "restore"
    assert value["hash"] == "abc123"


def test_plain_card_shape() -> None:
    card = build_plain_card("plain text body")
    assert card["elements"]
    assert card["elements"][0]["tag"] == "div"


# ---------------------------------------------------------------------------
# 4-5. Webhook signature verification
# ---------------------------------------------------------------------------


def _make_webhook_transport(
    allowed: set[str] | None = None,
    *,
    encrypt_key: str = "",
    verification_token: str = "token-xyz",
) -> FeishuWebhookTransport:
    return FeishuWebhookTransport(
        on_message=_noop_async,
        on_card_action=_noop_async,
        webhook_path="/api/feishu/webhook",
        verification_token=verification_token,
        encrypt_key=encrypt_key,
        allowed_chat_ids=allowed or set(),
    )


async def _noop_async(*args: Any, **kwargs: Any) -> None:
    return None


def _feishu_signature(
    timestamp: str, nonce: str, encrypt_key: str, body: bytes,
) -> str:
    """Reproduce Feishu's official signing algorithm.

    Mirrors :func:`backend.feishu.transport.FeishuWebhookTransport.verify_signature`
    and ``lark_oapi.event.dispatcher_handler._verify_sign`` exactly.
    """
    digest_src = (timestamp + nonce + encrypt_key).encode("utf-8") + body
    return hashlib.sha256(digest_src).hexdigest()


def test_webhook_signature_pass() -> None:
    transport = _make_webhook_transport(encrypt_key="enc-key-abc")
    body = b'{"event":{}}'
    expected = _feishu_signature("ts1", "nonce1", "enc-key-abc", body)
    assert transport.verify_signature("ts1", "nonce1", body, expected) is True


def test_webhook_signature_fail() -> None:
    transport = _make_webhook_transport(encrypt_key="enc-key-abc")
    assert transport.verify_signature("ts", "n", b'{"x":1}', "wrongsig") is False
    assert transport.verify_signature("ts", "n", b'{"x":1}', "") is False


def test_verify_signature_skipped_without_encrypt_key() -> None:
    """No encrypt_key configured → verify_signature is a no-op (returns True)."""
    transport = _make_webhook_transport(encrypt_key="")
    assert transport.verify_signature("", "", b"{}", "anything") is True


def test_verify_body_token_match() -> None:
    transport = _make_webhook_transport(verification_token="tk-1")
    assert transport.verify_body_token({"token": "tk-1"}) is True
    assert transport.verify_body_token({"header": {"token": "tk-1"}}) is True


def test_verify_body_token_mismatch() -> None:
    transport = _make_webhook_transport(verification_token="tk-1")
    assert transport.verify_body_token({"token": "wrong"}) is False
    assert transport.verify_body_token({"header": {"token": "wrong"}}) is False


def test_verify_body_token_missing_is_permissive() -> None:
    """When the body has no token at all, accept (avoid breaking card POSTs)."""
    transport = _make_webhook_transport(verification_token="tk-1")
    assert transport.verify_body_token({}) is True
    assert transport.verify_body_token({"event": {}}) is True


def test_verify_body_token_skipped_without_local_token() -> None:
    transport = _make_webhook_transport(verification_token="")
    assert transport.verify_body_token({"token": "anything"}) is True
    assert transport.verify_body_token({}) is True


# ---------------------------------------------------------------------------
# 6. URL verification challenge — run dispatch via a real FastAPI app so we
#    exercise the route exactly as Feishu would call it.
# ---------------------------------------------------------------------------


def test_webhook_url_verification() -> None:
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    transport = _make_webhook_transport()
    transport.register_routes(app)

    with TestClient(app) as client:
        resp = client.post(
            "/api/feishu/webhook",
            json={"type": "url_verification", "challenge": "chal-1", "token": "token-xyz"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"challenge": "chal-1"}


# ---------------------------------------------------------------------------
# 7. Default-deny when allowlist is empty
# ---------------------------------------------------------------------------


def test_default_deny_empty_allowed_chat_ids() -> None:
    received: list[MessageEnvelope] = []

    async def capture(envelope: MessageEnvelope) -> None:
        received.append(envelope)

    transport = FeishuWebhookTransport(
        on_message=capture,
        on_card_action=_noop_async,
        webhook_path="/api/feishu/webhook",
        verification_token="token-xyz",
        encrypt_key="",
        allowed_chat_ids=set(),  # empty => default-deny
    )

    event = {
        "header": {"event_type": "im.message.receive_v1"},
        "event": {
            "sender": {"sender_id": {"open_id": "ou_user"}},
            "message": {
                "message_id": "om_1",
                "chat_id": "oc_unknown",
                "message_type": "text",
                "content": json.dumps({"text": "hi"}),
            },
        },
    }

    asyncio.get_event_loop().run_until_complete(transport.dispatch_event(event))
    assert received == [], "default-deny should reject inbound messages"


def test_allowed_chat_id_passes() -> None:
    received: list[MessageEnvelope] = []

    async def capture(envelope: MessageEnvelope) -> None:
        received.append(envelope)

    transport = FeishuWebhookTransport(
        on_message=capture,
        on_card_action=_noop_async,
        webhook_path="/api/feishu/webhook",
        verification_token="token-xyz",
        encrypt_key="",
        allowed_chat_ids={"oc_allowed"},
    )

    event = {
        "header": {"event_type": "im.message.receive_v1"},
        "event": {
            "sender": {"sender_id": {"open_id": "ou_user"}},
            "message": {
                "message_id": "om_1",
                "chat_id": "oc_allowed",
                "message_type": "text",
                "content": json.dumps({"text": "hi"}),
            },
        },
    }

    asyncio.get_event_loop().run_until_complete(transport.dispatch_event(event))
    assert len(received) == 1
    assert received[0].channel == MessageChannel.FEISHU
    assert received[0].chat_id == "oc_allowed"
    assert received[0].content == "hi"


# ---------------------------------------------------------------------------
# 8. Telegram command_parser re-export shim
# ---------------------------------------------------------------------------


def test_command_parser_reexport() -> None:
    from backend.messaging.command_parser import CommandParser as MsgParser
    from backend.telegram.command_parser import CommandParser as TgParser

    assert TgParser is MsgParser

    parsed = TgParser.parse("/help")
    assert parsed.command_type.value == "help"


# ---------------------------------------------------------------------------
# 9. FeishuGateway basic construction with mocked SDK
# ---------------------------------------------------------------------------


def test_feishu_gateway_constructs_with_mocked_sdk() -> None:
    """Constructing the gateway doesn't require lark_oapi to be installed."""
    from backend.feishu.gateway import FeishuGateway

    fake_settings = SimpleNamespace(
        FEISHU_APP_ID="app",
        FEISHU_APP_SECRET="secret",
        FEISHU_TRANSPORT="webhook",
        FEISHU_WEBHOOK_PATH="/api/feishu/webhook",
        FEISHU_VERIFICATION_TOKEN="token",
        FEISHU_ENCRYPT_KEY="",
        FEISHU_DEFAULT_CHAT_ID="oc_default",
        FEISHU_ALLOWED_CHAT_IDS="oc_default,oc_other",
    )
    gw = FeishuGateway(settings=fake_settings)
    assert gw.channel == MessageChannel.FEISHU
    assert gw.default_chat_id == "oc_default"
    assert "oc_default" in gw.allowed_chat_ids
    assert "oc_other" in gw.allowed_chat_ids

    # Replace sender with a mock to verify BaseGateway wrappers forward.
    gw.sender = MagicMock()

    async def _fake_send(*args: Any, **kwargs: Any) -> bool:
        return True

    gw.sender.send_message = _fake_send  # type: ignore[attr-defined]
    ok = asyncio.get_event_loop().run_until_complete(
        gw.send_message("hello", chat_id="oc_default"),
    )
    assert ok is True


# ---------------------------------------------------------------------------
# 10. _event_to_envelope parses Feishu im.message.receive_v1 correctly
# ---------------------------------------------------------------------------


def test_event_to_envelope_text_message() -> None:
    event = {
        "header": {
            "event_type": "im.message.receive_v1",
            "event_id": "e1",
            "tenant_key": "t1",
        },
        "event": {
            "sender": {"sender_id": {"open_id": "ou_abc"}},
            "message": {
                "message_id": "om_42",
                "chat_id": "oc_main",
                "message_type": "text",
                "content": json.dumps({"text": "hello world"}),
            },
        },
    }
    env = _event_to_envelope(event)
    assert env is not None
    assert env.channel == MessageChannel.FEISHU
    assert env.chat_id == "oc_main"
    assert env.sender_id == "ou_abc"
    assert env.content == "hello world"
    assert env.platform_message_id == "om_42"


def test_event_to_envelope_non_text_skipped() -> None:
    event = {
        "header": {"event_type": "im.message.receive_v1"},
        "event": {
            "sender": {"sender_id": {"open_id": "ou_x"}},
            "message": {
                "message_id": "om_1",
                "chat_id": "oc_main",
                "message_type": "image",
                "content": "{}",
            },
        },
    }
    assert _event_to_envelope(event) is None


# ---------------------------------------------------------------------------
# lark_oapi optional — skip tests that actually touch the SDK
# ---------------------------------------------------------------------------


_importorskip = pytest.importorskip
