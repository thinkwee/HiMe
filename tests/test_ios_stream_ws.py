"""Tests for the agent-stream WebSocket: heartbeat/presence, idle timeout,
agent_waiting, and EventHub backlog semantics."""
from __future__ import annotations

import asyncio
import contextlib
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import backend.api.agent_state as agent_state
from backend.api import stream_routes
from backend.api.event_hub import ensure_hub
from backend.ios_gateway.connections import IOSConnectionRegistry, ios_connections

UID = "wsuser"
IOS = f"/api/stream/agent/{UID}?client=ios"
WEB = f"/api/stream/agent/{UID}"


@pytest.fixture
def app_client(monkeypatch):
    monkeypatch.setattr(stream_routes, "AGENT_WAIT_POLL_S", 0.05)
    monkeypatch.setattr(stream_routes, "IOS_IDLE_TIMEOUT_S", 0.4)
    monkeypatch.setattr(stream_routes.settings, "API_AUTH_TOKEN", "", raising=False)
    saved = dict(agent_state.active_agents)
    agent_state.active_agents.clear()
    app = FastAPI()
    app.include_router(stream_routes.router)
    with TestClient(app) as client:
        yield client
    agent_state.active_agents.clear()
    agent_state.active_agents.update(saved)
    ios_connections._conns.pop(UID, None)


def _add_agent() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=500)
    agent_state.active_agents[UID] = {"agent": None, "event_queue": q, "_starting": True}
    return q


def _wait(pred, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


@contextlib.contextmanager
def _connect(client, url=IOS):
    """websocket_connect that lets the handler finish before TestClient's
    teardown cancels the app task (otherwise a handler still in its cleanup
    surfaces as a spurious CancelledError)."""
    with client.websocket_connect(url) as ws:
        try:
            yield ws
        finally:
            try:
                ws.close()
            except Exception:
                pass
            if url == IOS:
                _wait(lambda: not ios_connections.is_online(UID))
                time.sleep(0.05)


def test_ping_pong_and_presence_touch(app_client):
    _add_agent()
    with _connect(app_client) as ws:
        assert ws.receive_json()["type"] == "monitor_connected"
        assert ios_connections.is_online(UID)
        conn = next(iter(ios_connections._conns[UID].values()))
        assert conn.active is False  # legacy until the client speaks
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}
        assert conn.active is True
    assert _wait(lambda: not ios_connections.is_online(UID))


def test_no_status_update_for_ios(app_client, monkeypatch):
    monkeypatch.setattr(stream_routes, "STATUS_INTERVAL_S", 0.05)
    _add_agent()
    with _connect(app_client) as ws:
        assert ws.receive_json()["type"] == "monitor_connected"
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"  # nothing else interleaved


def test_idle_timeout_only_after_first_client_message(app_client):
    _add_agent()
    # Legacy client (never sends): stays online past the idle timeout.
    with _connect(app_client) as ws:
        assert ws.receive_json()["type"] == "monitor_connected"
        time.sleep(0.8)
        assert ios_connections.is_online(UID)
    assert _wait(lambda: not ios_connections.is_online(UID))

    # Heartbeat-aware client that goes silent: closed + unregistered.
    with _connect(app_client) as ws:
        assert ws.receive_json()["type"] == "monitor_connected"
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"
        assert _wait(lambda: not ios_connections.is_online(UID))
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


def test_agent_waiting_then_streams(app_client):
    with _connect(app_client) as ws:
        assert ws.receive_json() == {"type": "agent_waiting"}
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}  # still answers while waiting
        assert ios_connections.is_online(UID)
        q = _add_agent()
        assert ws.receive_json()["type"] == "monitor_connected"
        app_client.portal.call(q.put, {"type": "chat_reply", "text": "hi"})
        assert ws.receive_json() == {"type": "chat_reply", "text": "hi"}
    assert _wait(lambda: not ios_connections.is_online(UID))


def test_web_client_still_gets_error_without_agent(app_client):
    with _connect(app_client, WEB) as ws:
        msg = ws.receive_json()
        assert msg["type"] == "error"
        assert "No active agent" in msg["error"]
    assert not ios_connections.is_online(UID)


def test_ios_resubscribes_after_agent_restart(app_client):
    _add_agent()
    with _connect(app_client) as ws:
        assert ws.receive_json()["type"] == "monitor_connected"
        agent_state.active_agents.pop(UID)
        assert ws.receive_json() == {"type": "agent_waiting"}
        q = _add_agent()
        assert ws.receive_json()["type"] == "monitor_connected"
        app_client.portal.call(q.put, {"type": "chat_reply", "text": "again"})
        assert ws.receive_json()["text"] == "again"


# ---------------------------------------------------------------- unit tests

async def test_unregister_idempotent_and_ttl():
    reg = IOSConnectionRegistry(ttl=0.05)
    await reg.register("u", "c")
    assert reg.is_online("u")
    await reg.unregister("u", "c")
    await reg.unregister("u", "c")  # no error
    assert not reg.is_online("u")

    await reg.register("u", "a")
    await reg.register("u", "legacy")
    reg.touch("u", "a")
    await asyncio.sleep(0.1)
    assert reg.is_online("u")  # legacy conn never ages out
    await reg.unregister("u", "legacy")
    assert not reg.is_online("u")  # active conn went stale -> TTL fires


async def test_hub_non_replay_gets_no_backlog_replay_does():
    q: asyncio.Queue = asyncio.Queue()
    for i in range(3):
        q.put_nowait({"n": i})
    info = {"event_queue": q}
    agent_state.active_agents["hubuser"] = info
    try:
        hub = ensure_hub("hubuser", info)
        live = hub.subscribe(replay=False)
        web = hub.subscribe(replay=True)
        await asyncio.sleep(0.05)
        assert live.empty()
        assert [web.get_nowait()["n"] for _ in range(3)] == [0, 1, 2]
        q.put_nowait({"n": 99})
        assert (await asyncio.wait_for(live.get(), 1.5))["n"] == 99
        assert (await asyncio.wait_for(web.get(), 1.5))["n"] == 99
    finally:
        agent_state.active_agents.pop("hubuser", None)
        info["fanout_task"].cancel()


async def test_apns_first_push_not_suppressed_by_coalesce_window():
    from backend.ios_gateway import IOSGateway

    class _A:
        enabled = True
        calls = 0

        async def send(self, *a, **k):
            _A.calls += 1

    gw = IOSGateway("offline_user", apns_sender=_A())
    await gw._maybe_push_apns("b", {})
    await gw._maybe_push_apns("b", {})  # coalesced
    assert _A.calls == 1
