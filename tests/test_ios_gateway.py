"""Tests for the in-app iOS gateway (the native channel alongside IM).

Covers the core delivery contract: full-fidelity outbound replies onto the
agent's event stream, the evidence-button hash extraction, the WS-vs-APNs
offline decision, APNs coalescing, and outbound image delivery through the
per-user-authed image store.
"""
from __future__ import annotations

import pytest

import backend.api.agent_state as agent_state
from backend.ios_gateway import IOSGateway, ios_connections
from backend.ios_gateway.gateway import extract_message_hash
from backend.ios_gateway.image_store import image_store
from backend.messaging.base import MessageChannel


class _FakeAgent:
    def __init__(self):
        self.events = []

    async def _emit(self, ev):
        self.events.append(ev)


class _FakeAPNs:
    enabled = True

    def __init__(self):
        self.calls = []

    async def send(self, user_id, title, body, data=None):
        self.calls.append((user_id, body, data))
        return 1


@pytest.fixture
def agents():
    """Provide a clean active_agents registry, restored after the test."""
    saved = dict(agent_state.active_agents)
    agent_state.active_agents.clear()
    yield agent_state.active_agents
    agent_state.active_agents.clear()
    agent_state.active_agents.update(saved)


_EVIDENCE_MARKUP = {
    "inline_keyboard": [[{"text": "📊 Show Evidence", "callback_data": "evidence:deadbeef00"}]]
}


def test_extract_message_hash():
    assert extract_message_hash(_EVIDENCE_MARKUP) == "deadbeef00"
    assert extract_message_hash(None) is None
    assert extract_message_hash({"inline_keyboard": []}) is None


async def test_send_message_emits_full_reply(agents):
    fa = _FakeAgent()
    agents["userA"] = {"agent": fa}
    gw = IOSGateway("userA")

    ok = await gw.send_message(
        "Your resting HR is trending down over the last week.",
        chat_id="userA", reply_markup=_EVIDENCE_MARKUP,
    )
    assert ok is True
    assert len(fa.events) == 1
    ev = fa.events[0]
    assert ev["type"] == "chat_reply"
    # Full, untruncated text (the gateway is the one place with the whole reply).
    assert ev["content"] == "Your resting HR is trending down over the last week."
    assert ev["message_hash"] == "deadbeef00"
    assert ev["final"] is True


async def test_routing_isolation(agents):
    """A gateway only emits to its own user's agent."""
    fa, fb = _FakeAgent(), _FakeAgent()
    agents["userA"] = {"agent": fa}
    agents["userB"] = {"agent": fb}

    await IOSGateway("userA").send_message("hi A", chat_id="userA")
    assert len(fa.events) == 1
    assert len(fb.events) == 0  # userB's agent never sees userA's reply


async def test_offline_triggers_apns(agents):
    fa = _FakeAgent()
    agents["userC"] = {"agent": fa}
    apns = _FakeAPNs()
    gw = IOSGateway("userC", apns_sender=apns)

    # No live stream connection => offline => APNs alert.
    await gw.send_message("offline reply", chat_id="userC")
    assert apns.calls == [("userC", "offline reply", {"chat_id": "userC", "message_hash": None})]


async def test_online_suppresses_apns(agents):
    fa = _FakeAgent()
    agents["userD"] = {"agent": fa}
    apns = _FakeAPNs()
    gw = IOSGateway("userD", apns_sender=apns)

    await ios_connections.register("userD", "conn1")
    try:
        await gw.send_message("while online", chat_id="userD")
        assert apns.calls == []  # delivered live; no push
    finally:
        await ios_connections.unregister("userD", "conn1")


async def test_apns_coalesced(agents):
    fa = _FakeAgent()
    agents["userE"] = {"agent": fa}
    apns = _FakeAPNs()
    gw = IOSGateway("userE", apns_sender=apns)

    await gw.send_message("first", chat_id="userE")
    await gw.send_message("second", chat_id="userE")  # within 5s => coalesced
    assert len(apns.calls) == 1


async def test_send_photo_emits_chat_image_with_authed_url(agents, tmp_path, monkeypatch):
    # Point the durable chat-image dir at the test's tmp so register() doesn't
    # write into the repo's real data/ tree.
    from backend.config import settings
    monkeypatch.setattr(settings, "DATA_STORE_PATH", str(tmp_path / "data"))

    fa = _FakeAgent()
    agents["userF"] = {"agent": fa}
    png = tmp_path / "chart.png"
    png.write_bytes(b"\x89PNG fake")
    gw = IOSGateway("userF")

    await gw.send_photo(str(png), caption="weekly trend", chat_id="userF")
    ev = fa.events[0]
    assert ev["type"] == "chat_image"
    assert ev["url"].startswith("/api/agent/chat-image/")
    assert ev["caption"] == "weekly trend"
    # register() persists a durable copy (not the raw /tmp path) so the chart
    # survives reaping/restart for history replay; the copy holds the bytes.
    durable = image_store.get(ev["image_id"], "userF")
    assert durable is not None and durable != str(png)
    with open(durable, "rb") as f:
        assert f.read() == b"\x89PNG fake"
    # The image id resolves only for the owning user.
    assert image_store.get(ev["image_id"], "userOther") is None
    # Durable filesystem fallback (post-restart / post-TTL) is owner-scoped too.
    assert image_store.durable_path(ev["image_id"], "userF") == durable
    assert image_store.durable_path(ev["image_id"], "userOther") is None
    # The gateway exposes the id so the chat loop can persist it on the turn.
    assert gw.last_image_id == ev["image_id"]


def test_gateway_identity():
    gw = IOSGateway("u-123")
    assert gw.channel == MessageChannel.IOS
    assert gw.default_chat_id == "u-123"
    assert gw.allowed_chat_ids == {"u-123"}
    assert gw.supports_inline_caption is True
    assert gw.is_muted() is False
