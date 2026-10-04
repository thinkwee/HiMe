"""Regression tests for the backend bug-sweep (ingest loop, auth, gateways, ...)."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import types
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from backend.api import agent_lifecycle, agent_state

# ---------------------------------------------------------------------------
# Live ingest loop
# ---------------------------------------------------------------------------


class _FakeStore:
    def __init__(self, last_id: int = 0) -> None:
        self.last_id = last_id
        self.last_ua = 0.0
        self.batches: list[dict] = []
        self.fail_next_ingest = False
        self.is_ingesting = False
        self.db_file = Path("unused.db")

    def get_last_ingested_id(self) -> int:
        return self.last_id

    def get_last_updated_at(self) -> float:
        return self.last_ua

    def save_ingestion_id(self, i: int) -> None:
        self.last_id = i

    def save_last_updated_at(self, ua: float) -> None:
        self.last_ua = ua

    def ingest_batch(self, batch: dict) -> None:
        if self.fail_next_ingest:
            self.fail_next_ingest = False
            raise RuntimeError("disk is on fire")
        self.batches.append(batch)


class _FakeReader:
    def __init__(self, rows: list[dict], max_id: int | None = None) -> None:
        self.rows = rows
        self._max_id = max_id

    def get_max_id(self) -> int | None:
        if self._max_id is not None:
            return self._max_id
        return max((r["id"] for r in self.rows), default=0)

    def get_all_samples_since_id(self, since_id: int, limit: int = 100000) -> list[dict]:
        return [r for r in self.rows if r["id"] > since_id]

    def get_samples_updated_since(self, ua: float, limit: int = 100000) -> list[dict]:
        return []


class _NoTriggers:
    async def evaluate_after_ingest(self, **_kw):
        return []


def test_coerce_epoch_handles_ms_and_garbage():
    f = agent_lifecycle._coerce_epoch
    assert f(1_700_000_000) == 1_700_000_000
    assert f(1_700_000_000_000) == pytest.approx(1_700_000_000)  # ms -> s
    assert f(None) is None
    assert f("abc") is None
    assert f(float("nan")) is None
    assert f(-5) is None


def test_build_records_skips_bad_rows_individually():
    rows = [
        {"ts": 1_700_000_000, "value": 70.0, "feature_type": "heart_rate"},
        {"ts": 1e30, "value": 1.0, "feature_type": "heart_rate"},       # absurd ts
        {"ts": 1_700_000_001, "value": None, "feature_type": "steps"},  # no value
        {"ts": 1_700_000_002, "value": float("inf"), "feature_type": "steps"},
        {"ts": 1_700_000_003_000, "value": 5.0, "feature_type": "steps"},  # ms
    ]
    recs = agent_lifecycle._build_records(rows, "LiveUser")
    assert [r["feature_type"] for r in recs] == ["heart_rate", "steps"]


async def test_ingest_cycle_resets_hwm_when_watch_db_recreated():
    store = _FakeStore(last_id=5000)
    store.last_ua = 123.0
    reader = _FakeReader([{"id": 1, "ts": 1_700_000_000, "value": 60.0, "feature_type": "hr"}])
    hwm = {"id": 5000, "ua": 123.0}
    consumed = await agent_lifecycle._ingest_cycle(reader, store, "LiveUser", hwm, _NoTriggers())
    assert consumed == 1
    assert hwm["id"] == 1 and store.last_id == 1
    assert len(store.batches) == 1


async def test_ingest_cycle_does_not_advance_hwm_on_failed_write():
    store = _FakeStore()
    store.fail_next_ingest = True
    reader = _FakeReader([{"id": 7, "ts": 1_700_000_000, "value": 60.0, "feature_type": "hr"}])
    hwm = {"id": 0, "ua": 0.0}
    with pytest.raises(RuntimeError):
        await agent_lifecycle._ingest_cycle(reader, store, "LiveUser", hwm, _NoTriggers())
    assert hwm["id"] == 0  # row is retried, not lost
    await agent_lifecycle._ingest_cycle(reader, store, "LiveUser", hwm, _NoTriggers())
    assert hwm["id"] == 7 and len(store.batches) == 1


async def test_ingest_loop_survives_errors_and_keeps_polling(monkeypatch):
    monkeypatch.setattr(agent_lifecycle, "_INGEST_POLL_INTERVAL_S", 0.01)
    monkeypatch.setattr(agent_lifecycle, "_INGEST_MAX_BACKOFF_S", 0.02)
    store = _FakeStore()
    store.fail_next_ingest = True
    reader = _FakeReader([{"id": 1, "ts": 1_700_000_000, "value": 60.0, "feature_type": "hr"}])
    task = asyncio.create_task(agent_lifecycle._live_ingest_loop(reader, store, "LiveUser"))
    try:
        for _ in range(200):
            if store.batches:
                break
            await asyncio.sleep(0.01)
        assert store.batches, "loop died on the first DB error"
        assert not task.done()
    finally:
        store.is_ingesting = False
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_dead_ingest_task_is_restarted(monkeypatch):
    monkeypatch.setattr(agent_lifecycle, "_INGEST_RESTART_DELAY_S", 0.01)
    calls: list[int] = []

    async def fake_loop(reader, data_store, user_id):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        await asyncio.sleep(10)

    monkeypatch.setattr(agent_lifecycle, "_live_ingest_loop", fake_loop)
    uid = "ingest_test_user"
    store = _FakeStore()
    try:
        first = agent_lifecycle._spawn_ingest_task(object(), store, uid)
        for _ in range(100):
            await asyncio.sleep(0.01)
            cur = agent_state.system_ingest_tasks.get(uid)
            if cur is not None and cur is not first:
                break
        await asyncio.sleep(0.02)  # let the replacement task start running
        cur = agent_state.system_ingest_tasks.get(uid)
        assert cur is not None and cur is not first and not cur.done()
        assert len(calls) == 2
    finally:
        t = agent_state.system_ingest_tasks.pop(uid, None)
        if t:
            t.cancel()
            await asyncio.gather(t, return_exceptions=True)


def test_watch_reader_get_max_id(tmp_path):
    import sqlite3

    from backend.data_readers.watch_db_reader import WatchDBReader

    r = WatchDBReader(tmp_path)
    assert r.get_max_id() is None  # no db yet
    with sqlite3.connect(tmp_path / "watch.db") as c:
        c.execute("CREATE TABLE health_samples_eav (id INTEGER PRIMARY KEY, ts REAL, f TEXT, v REAL)")
    assert r.get_max_id() == 0
    with sqlite3.connect(tmp_path / "watch.db") as c:
        c.execute("INSERT INTO health_samples_eav(id, ts, f, v) VALUES (42, 1, 'x', 1)")
    assert r.get_max_id() == 42


# ---------------------------------------------------------------------------
# agent_state: memory fallthrough, id validation, XFF
# ---------------------------------------------------------------------------


def test_memory_falls_through_to_disk_while_agent_is_starting(tmp_path, monkeypatch):
    from backend.agent import MemoryManager

    monkeypatch.setattr(agent_state.settings, "MEMORY_DB_PATH", tmp_path)
    MemoryManager(tmp_path, "LiveUser")  # create the db on disk
    monkeypatch.setitem(agent_state.active_agents, "LiveUser", {"agent": None, "memory": None, "_starting": True})
    mem = agent_state._get_or_create_memory("LiveUser")
    assert mem is not None
    assert mem.db_file == tmp_path / "LiveUser.db"


async def test_async_memory_variant(tmp_path, monkeypatch):
    from backend.agent import MemoryManager

    monkeypatch.setattr(agent_state.settings, "MEMORY_DB_PATH", tmp_path)
    MemoryManager(tmp_path, "LiveUser")
    assert await agent_state.aget_or_create_memory("LiveUser") is not None
    assert await agent_state.aget_or_create_memory("nobody") is None


@pytest.mark.parametrize("bad", ["../etc/passwd", "a/b", "a.b", "", "x" * 65, "a b", "..%2f"])
def test_user_id_validation_rejects_traversal(bad):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as ei:
        agent_state.validate_user_id(bad)
    assert ei.value.status_code == 400


def test_user_id_validation_accepts_live_user():
    assert agent_state.validate_user_id("LiveUser") == "LiveUser"
    assert agent_state.validate_user_id("user_1-x") == "user_1-x"
    req = agent_state.StartAgentRequest(user_id="LiveUser")
    assert req.user_id == "LiveUser"
    with pytest.raises(ValueError):
        agent_state.StartAgentRequest(user_id="../x")


def test_get_or_create_memory_refuses_traversal_id(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_state.settings, "MEMORY_DB_PATH", tmp_path)
    assert agent_state._get_or_create_memory("../LiveUser") is None


# ---------------------------------------------------------------------------
# HTTP-level: auth middleware, CORS, validation, device routes
# ---------------------------------------------------------------------------


@pytest.fixture
def authed_app(monkeypatch):
    from backend.main import app, settings

    monkeypatch.setattr(settings, "API_AUTH_TOKEN", "s3cret-tok")
    return app


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_query_token_rejected_on_regular_api_routes(authed_app):
    async with _client(authed_app) as c:
        r = await c.get("/api/data/source?token=s3cret-tok")
        assert r.status_code == 401
        r = await c.get("/api/data/source", headers={"Authorization": "Bearer s3cret-tok"})
        assert r.status_code == 200


async def test_query_token_still_works_for_pages_and_chat_images(authed_app):
    async with _client(authed_app) as c:
        # Auth passes (404: no such page / image), not 401.
        r = await c.get("/api/personalised-pages/nope/?token=s3cret-tok")
        assert r.status_code != 401
        r = await c.post("/api/personalised-pages/nope/data?token=s3cret-tok", json={})
        assert r.status_code != 401
        r = await c.get("/api/agent/chat-image/abc123?token=s3cret-tok")
        assert r.status_code != 401


async def test_non_ascii_token_gives_401_not_500(authed_app):
    async with _client(authed_app) as c:
        r = await c.get("/api/data/source", headers={"Authorization": "Bearer café".encode()})
        assert r.status_code == 401


async def test_401_carries_cors_headers(authed_app):
    async with _client(authed_app) as c:
        r = await c.get("/api/data/source", headers={"Origin": "http://localhost:5173"})
        assert r.status_code == 401
        assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"


async def test_path_traversal_ids_are_rejected(monkeypatch):
    from backend.main import app, settings

    monkeypatch.setattr(settings, "API_AUTH_TOKEN", None)
    async with _client(app) as c:
        r = await c.get("/api/agent/memory/..%2f..%2fetc")
        assert r.status_code in (400, 404)
        r = await c.get("/api/agent/memory/bad.id")
        assert r.status_code == 400
        r = await c.post("/api/agent/start", json={"user_id": "../x"})
        assert r.status_code == 422
        r = await c.post("/api/agent/stop?user_id=a/b")
        assert r.status_code in (400, 404)


async def test_device_route_validation(monkeypatch, tmp_path):
    from backend.ios_gateway import device_store
    from backend.main import app, settings

    monkeypatch.setattr(settings, "API_AUTH_TOKEN", None)
    monkeypatch.setattr(device_store, "_db_path", lambda: str(tmp_path / "d.db"))
    good = "ab" * 32
    async with _client(app) as c:
        r = await c.post("/api/devices/register", json={"device_token": "not-hex!", "environment": "sandbox"})
        assert r.status_code == 400
        r = await c.post("/api/devices/register", json={"device_token": good, "environment": "weird"})
        assert r.status_code == 400
        r = await c.post("/api/devices/register", json={"device_token": good, "environment": "Sandbox"})
        assert r.status_code == 200
        assert device_store.list_device_tokens("LiveUser", "sandbox")


async def test_scheduled_task_rejects_impossible_cron(monkeypatch, tmp_path):
    from backend.agent import MemoryManager
    from backend.main import app, settings

    monkeypatch.setattr(settings, "API_AUTH_TOKEN", None)
    monkeypatch.setattr(agent_state.settings, "MEMORY_DB_PATH", tmp_path)
    MemoryManager(tmp_path, "LiveUser")
    async with _client(app) as c:
        r = await c.post("/api/agent/scheduled-tasks/LiveUser",
                         json={"cron_expr": "0 0 31 2 *", "prompt_goal": "x"})
        assert r.status_code == 400
        r = await c.post("/api/agent/scheduled-tasks/LiveUser",
                         json={"cron_expr": "0 8 * * *", "prompt_goal": "  "})
        assert r.status_code == 422
        r = await c.post("/api/agent/scheduled-tasks/LiveUser",
                         json={"cron_expr": "0 8 * * *", "prompt_goal": "sleep"})
        assert r.status_code == 200


async def test_trigger_rule_constraints(monkeypatch, tmp_path):
    from backend.agent import MemoryManager
    from backend.main import app, settings

    monkeypatch.setattr(settings, "API_AUTH_TOKEN", None)
    monkeypatch.setattr(agent_state.settings, "MEMORY_DB_PATH", tmp_path)
    MemoryManager(tmp_path, "LiveUser")
    base = {"name": "n", "feature_type": "heart_rate", "condition": "gt",
            "threshold": 100, "window_minutes": 60, "cooldown_minutes": 30, "prompt_goal": "g"}
    async with _client(app) as c:
        ok = await c.post("/api/agent/trigger-rules/LiveUser", json=base)
        assert ok.status_code == 200
        for patch in ({"window_minutes": 0}, {"cooldown_minutes": -1},
                      {"prompt_goal": ""}, {"name": ""}):
            r = await c.post("/api/agent/trigger-rules/LiveUser", json={**base, **patch})
            assert r.status_code == 422, patch
        r = await c.post("/api/agent/trigger-rules/LiveUser",
                         content=json.dumps({**base, "threshold": "NaN"}),
                         headers={"content-type": "application/json"})
        assert r.status_code == 422


async def test_onboarding_survey_reports_real_queue_state(monkeypatch, tmp_path):
    from backend.main import app, settings

    monkeypatch.setattr(settings, "API_AUTH_TOKEN", None)
    monkeypatch.setattr(agent_state.settings, "MEMORY_DB_PATH", tmp_path)
    monkeypatch.setattr("backend.api.agent_diagnostics.settings.MEMORY_DB_PATH", tmp_path)
    async with _client(app) as c:
        r = await c.post("/api/agent/onboarding-survey", json={"goals": ["sleep"], "trigger_now": True})
        body = r.json()
        assert body["triggered_now"] is False
        assert body["queued_plan"] is False  # redesign requested but agent not running
        r = await c.post("/api/agent/onboarding-survey", json={"goals": ["sleep"]})
        assert r.json()["queued_plan"] is True  # onboarding defers to first chat


async def test_tools_endpoint_survives_starting_agent(monkeypatch, tmp_path):
    from backend.main import app, settings

    monkeypatch.setattr(settings, "API_AUTH_TOKEN", None)
    monkeypatch.setattr(settings, "MEMORY_DB_PATH", tmp_path)
    monkeypatch.setitem(agent_state.active_agents, "LiveUser", {"agent": None, "_starting": True})
    async with _client(app) as c:
        r = await c.get("/api/agent/tools?user_id=LiveUser")
        assert r.status_code == 200
        assert r.json()["tools"]


# ---------------------------------------------------------------------------
# /stop racing startup
# ---------------------------------------------------------------------------


async def test_stop_during_startup_is_not_undone(monkeypatch):
    uid = "LiveUser"
    started = asyncio.Event()
    release = asyncio.Event()
    stopped: list[str] = []

    class _Agent:
        def stop(self) -> None:
            stopped.append("agent")

    async def fake_internal(body, progress=None, event_queue=None):
        started.set()
        await release.wait()
        return {"agent": _Agent(), "task": None, "data_store": None, "ingest_task": None,
                "event_queue": event_queue, "config": {}, "memory": object()}

    monkeypatch.setattr(agent_lifecycle, "_start_agent_internal", fake_internal)
    saved: list[int] = []

    async def fake_save(body):
        saved.append(1)

    monkeypatch.setattr(agent_lifecycle, "_save_last_config", fake_save)
    agent_state.active_agents.pop(uid, None)
    q: asyncio.Queue = asyncio.Queue(maxsize=50)
    agent_state.active_agents[uid] = {"agent": None, "task": None, "data_store": None,
                                      "ingest_task": None, "event_queue": q, "config": {},
                                      "memory": None, "_starting": True}
    t = asyncio.create_task(agent_lifecycle._start_agent_background(
        agent_state.StartAgentRequest(user_id=uid), q))
    await started.wait()
    # simulate POST /stop: placeholder removed
    agent_state.active_agents.pop(uid)
    release.set()
    await t
    assert uid not in agent_state.active_agents
    assert stopped == ["agent"]
    assert not saved


async def test_cancel_and_wait_propagates_callers_cancellation():
    async def stubborn():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            await asyncio.sleep(0.2)  # slow cleanup
            raise

    inner = asyncio.create_task(stubborn())
    await asyncio.sleep(0)
    outer = asyncio.create_task(agent_lifecycle._cancel_and_wait(inner, timeout=5))
    await asyncio.sleep(0.05)
    outer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await outer
    await asyncio.gather(inner, return_exceptions=True)


# ---------------------------------------------------------------------------
# Feishu
# ---------------------------------------------------------------------------


def _feishu_settings(**kw):
    base = dict(FEISHU_APP_ID="a", FEISHU_APP_SECRET="b", FEISHU_TRANSPORT="webhook",
                FEISHU_WEBHOOK_PATH="/api/feishu/webhook", FEISHU_VERIFICATION_TOKEN="",
                FEISHU_ENCRYPT_KEY="", FEISHU_DEFAULT_CHAT_ID="oc_1", FEISHU_ALLOWED_CHAT_IDS="")
    base.update(kw)
    return SimpleNamespace(**base)


def test_feishu_webhook_refuses_to_start_without_secret():
    from backend.feishu.gateway import FeishuGateway

    with pytest.raises(ValueError, match="FEISHU_VERIFICATION_TOKEN"):
        FeishuGateway(settings=_feishu_settings())
    FeishuGateway(settings=_feishu_settings(FEISHU_VERIFICATION_TOKEN="t"))
    FeishuGateway(settings=_feishu_settings(FEISHU_ENCRYPT_KEY="k"))


def test_feishu_ws_card_route_only_mounted_with_secret():
    from fastapi import FastAPI

    from backend.feishu.gateway import FeishuGateway

    app = FastAPI()
    gw = FeishuGateway(settings=_feishu_settings(FEISHU_TRANSPORT="ws"))
    gw.register_routes(app)
    assert not any(getattr(r, "path", "") == "/api/feishu/webhook" for r in app.routes)

    app2 = FastAPI()
    gw2 = FeishuGateway(settings=_feishu_settings(FEISHU_TRANSPORT="ws", FEISHU_VERIFICATION_TOKEN="t"))
    gw2.register_routes(app2)
    assert any(getattr(r, "path", "") == "/api/feishu/webhook" for r in app2.routes)


async def test_feishu_ws_dedupes_and_keeps_task_refs():
    from backend.feishu.transport import FeishuWsTransport

    got: list = []

    async def on_msg(env):
        got.append(env)

    async def on_card(ev):
        return None

    t = FeishuWsTransport("a", "b", on_msg, on_card, allowed_chat_ids={"oc_1"})
    event = {
        "header": {"event_id": "ev1", "event_type": "im.message.receive_v1"},
        "event": {"sender": {"sender_id": {"open_id": "ou_1"}},
                  "message": {"message_id": "om_1", "chat_id": "oc_1", "message_type": "text",
                              "content": json.dumps({"text": "hi"})}},
    }
    t._on_sdk_message(event)
    t._on_sdk_message(event)  # Feishu redelivery
    assert len(t._bg_tasks) == 1  # strong ref while running
    await asyncio.sleep(0.05)
    assert len(got) == 1
    assert not t._bg_tasks


def test_feishu_verification_token_check_is_constant_time_api():
    import inspect

    from backend.feishu import transport

    assert "hmac.compare_digest" in inspect.getsource(transport.FeishuWebhookTransport.register_routes)


# ---------------------------------------------------------------------------
# Messaging inbox
# ---------------------------------------------------------------------------


def test_debounce_keys_on_chat_id():
    from datetime import datetime, timezone

    from backend.messaging.base import MessageChannel, MessageEnvelope
    from backend.messaging.inbox import _debounce

    now = datetime.now(timezone.utc)

    def env(chat, text):
        return MessageEnvelope(message_id=text, channel=MessageChannel.TELEGRAM, sender_id="u",
                               content=text, timestamp=now, chat_id=chat)

    out = _debounce([env("c1", "a"), env("c1", "b"), env("c2", "c")])
    assert [m.content for m in out] == ["a\nb", "c"]


async def test_inbox_warns_when_dropping(caplog):
    from backend.messaging.base import MessageChannel, MessageEnvelope
    from backend.messaging.inbox import InboxQueue

    q = InboxQueue(maxsize=1)
    for i in range(2):
        await q.push(MessageEnvelope(message_id=str(i), channel=MessageChannel.IOS, sender_id="u",
                                     content=str(i), chat_id="c"))
    assert any("dropping oldest" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------


class _Resp:
    def __init__(self, status, body=None, text=""):
        self.status_code = status
        self._body = body or {}
        self.text = text or json.dumps(self._body)
        self.headers = {}

    def json(self):
        return self._body


class _FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    async def post(self, url, **kw):
        self.calls.append({"url": url, **kw})
        return self.responses.pop(0)


async def test_telegram_sender_retries_after_429(monkeypatch):
    from backend.telegram import sender as sender_mod

    slept: list[float] = []

    async def fake_sleep(d):
        slept.append(d)

    monkeypatch.setattr(sender_mod.asyncio, "sleep", fake_sleep)
    s = sender_mod.TelegramSender("tok", "1")
    s._client = _FakeClient([_Resp(429, {"parameters": {"retry_after": 3}}), _Resp(200)])
    assert await s.send_message("hi", reply_to_message_id=9) is True
    assert slept == [3.0]
    assert s._client.calls[0]["json"]["allow_sending_without_reply"] is True


async def test_telegram_send_photo_retries_with_plain_caption(tmp_path):
    from backend.telegram import sender as sender_mod

    img = tmp_path / "c.png"
    img.write_bytes(b"\x89PNG")
    s = sender_mod.TelegramSender("tok", "1")
    s._client = _FakeClient([_Resp(400, text="Bad Request: can't parse entities"), _Resp(200)])
    assert await s.send_photo(str(img), caption="**bold** x") is True
    second = s._client.calls[1]["data"]
    assert "<b>" not in second["caption"] and "parse_mode" not in second


async def test_telegram_poller_resets_backoff_after_empty_poll(monkeypatch, tmp_path):
    from backend.telegram.poller import TelegramPoller

    p = TelegramPoller("tok", on_message=lambda e: None, poll_timeout=0, state_path=tmp_path / "s.json")
    assert p._poll_timeout == 1  # clamped
    seq = iter([RuntimeError("x"), [], RuntimeError("y")])
    sleeps: list[float] = []

    async def fake_fetch():
        item = next(seq)
        if isinstance(item, Exception):
            raise item
        return item

    async def fake_sleep(d):
        sleeps.append(d)
        if len(sleeps) == 2:
            p._running = False

    monkeypatch.setattr(p, "_fetch_updates", fake_fetch)
    monkeypatch.setattr("backend.telegram.poller.asyncio.sleep", fake_sleep)
    p._running = True
    await p.poll_loop()
    assert sleeps == [1.0, 1.0]  # second failure starts from 1s again, not 2s


# ---------------------------------------------------------------------------
# WeChat
# ---------------------------------------------------------------------------


def _wx_settings(tmp_path, allowed=""):
    return SimpleNamespace(WEIXIN_BOT_TOKEN_PATH=str(tmp_path / "tok.json"),
                           WEIXIN_DEFAULT_USER_ID="", WEIXIN_ALLOWED_USER_IDS=allowed)


def test_weixin_default_deny_uses_scanner_id(tmp_path):
    from backend.weixin.gateway import WeixinGateway

    (tmp_path / "tok.json").write_text(json.dumps(
        {"bot_token": "t", "ilink_user_id": "me@im.wechat", "baseurl": "https://eu.example.com/"}))
    gw = WeixinGateway(settings=_wx_settings(tmp_path))
    assert gw.allowed_chat_ids == {"me@im.wechat"}
    assert gw._allow_all_legacy is False
    assert gw._base_url == "https://eu.example.com"


def test_weixin_legacy_token_file_still_works_with_warning(tmp_path, caplog):
    from backend.weixin.gateway import WeixinGateway

    (tmp_path / "tok.json").write_text(json.dumps({"bot_token": "t"}))
    gw = WeixinGateway(settings=_wx_settings(tmp_path))
    assert gw._allow_all_legacy is True
    assert any("WEIXIN_ALLOWED_USER_IDS" in r.message for r in caplog.records)


def test_weixin_explicit_allowlist_wins(tmp_path):
    from backend.weixin.gateway import WeixinGateway

    (tmp_path / "tok.json").write_text(json.dumps({"bot_token": "t", "ilink_user_id": "me"}))
    gw = WeixinGateway(settings=_wx_settings(tmp_path, allowed="other@im.wechat"))
    assert gw.allowed_chat_ids == {"other@im.wechat"}


def test_weixin_rejects_insecure_baseurl():
    from backend.weixin.qr_login import ILINK_BASE, resolve_base_url

    assert resolve_base_url("http://evil.example") == ILINK_BASE
    assert resolve_base_url("") == ILINK_BASE


# ---------------------------------------------------------------------------
# APNs
# ---------------------------------------------------------------------------


async def test_apns_payload_levels_and_aps_last(monkeypatch, tmp_path):
    from backend.ios_gateway import apns as apns_mod

    sent: list = []

    class _Req:
        def __init__(self, device_token, message, push_type):
            self.message = message

    class _Client:
        async def send_notification(self, req):
            sent.append(req.message)
            return SimpleNamespace(is_successful=True)

    fake = types.ModuleType("aioapns")
    fake.NotificationRequest = _Req
    fake.PushType = SimpleNamespace(ALERT="alert")
    monkeypatch.setitem(sys.modules, "aioapns", fake)
    monkeypatch.setattr(apns_mod.device_store, "list_device_tokens",
                        lambda u, e: [{"device_token": "aa" * 32}])
    s = apns_mod.APNSSender(SimpleNamespace(APNS_ENABLED=True, APNS_ENV="production"))
    s._client = _Client()
    n = await s.send("LiveUser", "T", "B", data={"aps": {"evil": 1}, "chat_id": "c"})
    assert n == 1
    assert sent[0]["aps"]["interruption-level"] == "active" and "evil" not in sent[0]["aps"]
    await s.send("LiveUser", "T", "B", time_sensitive=True)
    assert sent[1]["aps"]["interruption-level"] == "time-sensitive"


async def test_apns_send_times_out(monkeypatch):
    from backend.ios_gateway import apns as apns_mod

    class _Req:
        def __init__(self, **kw):
            pass

    class _Client:
        async def send_notification(self, req):
            await asyncio.sleep(10)

    fake = types.ModuleType("aioapns")
    fake.NotificationRequest = _Req
    fake.PushType = SimpleNamespace(ALERT="alert")
    monkeypatch.setitem(sys.modules, "aioapns", fake)
    monkeypatch.setattr(apns_mod, "_SEND_TIMEOUT_S", 0.05)
    monkeypatch.setattr(apns_mod.device_store, "list_device_tokens",
                        lambda u, e: [{"device_token": "aa" * 32}])
    s = apns_mod.APNSSender(SimpleNamespace(APNS_ENABLED=True, APNS_ENV="production"))
    s._client = _Client()
    t0 = time.monotonic()
    assert await s.send("LiveUser", "T", "B") == 0
    assert time.monotonic() - t0 < 2


async def test_ios_gateway_time_sensitive_only_for_report_pushes(monkeypatch):
    from backend.ios_gateway.connections import ios_connections
    from backend.ios_gateway.gateway import IOSGateway

    calls: list[dict] = []

    class _APNs:
        enabled = True

        async def send(self, user_id, title, body, data=None, **kw):
            calls.append(kw)

    class _Agent:
        async def _emit(self, ev):
            pass

    monkeypatch.setitem(agent_state.active_agents, "apnsuser", {"agent": _Agent()})
    monkeypatch.setattr(ios_connections, "is_online", lambda uid: False)
    gw = IOSGateway("apnsuser", apns_sender=_APNs())
    await gw.send_message("chat reply", chat_id="apnsuser")
    gw._last_apns_ts = None
    await gw.send_message("report", chat_id="apnsuser", report_id=3)
    assert calls == [{}, {"time_sensitive": True}]


# ---------------------------------------------------------------------------
# Retention of media, atomic JSON, config, timezone
# ---------------------------------------------------------------------------


async def test_retention_reaps_old_uploads_and_chat_images(tmp_path, monkeypatch):
    from backend.agent import retention

    monkeypatch.setattr(retention.settings, "DATA_STORE_PATH", tmp_path)
    monkeypatch.setattr(retention.settings, "MEMORY_DB_PATH", tmp_path / "mem")
    old_t = time.time() - 40 * 86400
    keep = {}
    for sub in ("uploads", "chat_images"):
        d = tmp_path / "LiveUser" / sub
        d.mkdir(parents=True)
        (d / "old.png").write_bytes(b"x")
        os.utime(d / "old.png", (old_t, old_t))
        (d / "new.png").write_bytes(b"x")
        keep[sub] = d
    res = await retention.prune_expired_data(30)
    for d in keep.values():
        assert not (d / "old.png").exists()
        assert (d / "new.png").exists()
    assert res["LiveUser/uploads"] == 1 and res["LiveUser/chat_images"] == 1


def test_atomic_write_json(tmp_path):
    from backend.utils import atomic_write_json

    p = tmp_path / "sub" / "c.json"
    atomic_write_json(p, {"a": 1})
    assert json.loads(p.read_text()) == {"a": 1}
    with pytest.raises(TypeError):
        atomic_write_json(p, {"a": object()})
    assert json.loads(p.read_text()) == {"a": 1}  # old content intact
    assert [f.name for f in p.parent.iterdir()] == ["c.json"]  # no temp litter


def test_app_timezone_never_raises(monkeypatch):
    from backend import utils
    from backend.config import settings

    for bad in ("/etc/localtime", "Not/AZone", "../../x", "\x00"):
        monkeypatch.setattr(settings, "TIMEZONE", bad)
        assert utils.app_timezone().key == "UTC"


def test_dotenv_export_does_not_override_and_skips_empty(tmp_path, monkeypatch):
    from backend.config import _export_dotenv_to_environ

    env = tmp_path / ".env"
    env.write_text("ZHIPUAI_API_KEY=zk\nGROQ_API_KEY=\nGEMINI_API_KEY=from-file\n")
    monkeypatch.delenv("ZHIPUAI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert _export_dotenv_to_environ(str(env)) == 1
    assert os.environ["ZHIPUAI_API_KEY"] == "zk"
    assert "GROQ_API_KEY" not in os.environ
    assert os.environ["GEMINI_API_KEY"] == "test-placeholder"  # real env wins
    monkeypatch.delenv("ZHIPUAI_API_KEY")


def test_settings_know_zhipuai_and_poll_timeout_clamp():
    from backend.config import Settings

    assert "ZHIPUAI_API_KEY" in Settings.model_fields
    assert Settings(TELEGRAM_POLL_TIMEOUT=0).TELEGRAM_POLL_TIMEOUT == 1
    assert "TELEGRAM_WAKE_ON_MESSAGE" not in Settings.model_fields


def test_exposed_without_auth_warns(monkeypatch, caplog):
    from backend import main

    monkeypatch.setattr(main.settings, "API_HOST", "0.0.0.0")
    monkeypatch.setattr(main.settings, "API_AUTH_TOKEN", None)
    main._warn_if_exposed_without_auth()
    assert any("SECURITY WARNING" in r.message for r in caplog.records)
    caplog.clear()
    monkeypatch.setattr(main.settings, "API_HOST", "127.0.0.1")
    main._warn_if_exposed_without_auth()
    assert not caplog.records


# ---------------------------------------------------------------------------
# Personalised pages: timeout + module cache
# ---------------------------------------------------------------------------


async def test_page_handler_timeout_and_module_cache(tmp_path, monkeypatch):
    from backend.api import page_routes

    monkeypatch.setattr(page_routes, "_PAGES_DIR", tmp_path)
    monkeypatch.setattr(page_routes, "_PAGE_HANDLER_TIMEOUT_S", 0.2)
    page_routes._module_cache.clear()
    d = tmp_path / "p1"
    d.mkdir()
    (d / "route.py").write_text(
        "COUNT = []\nCOUNT.append(1)\nasync def route_handler(request):\n    return {'n': len(COUNT)}\n")
    r1 = await page_routes._exec_route_handler("p1", None)
    r2 = await page_routes._exec_route_handler("p1", None)
    assert json.loads(r1.body) == json.loads(r2.body) == {"n": 1}  # module executed once

    (d / "route.py").write_text(
        "import asyncio\n# rewritten with a different size so the mtime/size cache key changes\n"
        "async def route_handler(request):\n    await asyncio.sleep(5)\n    return {}\n")
    r3 = await page_routes._exec_route_handler("p1", None)
    assert r3.status_code == 504

    (d / "route.py").write_text("def route_handler(request:\n")
    r4 = await page_routes._exec_route_handler("p1", None)
    assert r4.status_code == 422


# ---------------------------------------------------------------------------
# X-Forwarded-For
# ---------------------------------------------------------------------------


def test_forwarded_for_rightmost(monkeypatch):
    monkeypatch.setattr(agent_state.settings, "TRUST_PROXY_HEADERS", True)
    req = SimpleNamespace(headers={"X-Forwarded-For": "1.1.1.1, 2.2.2.2 ,3.3.3.3"},
                          client=SimpleNamespace(host="10.0.0.1"))
    assert agent_state._client_ip(req) == "3.3.3.3"
