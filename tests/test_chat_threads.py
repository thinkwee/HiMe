"""In-app chat threads: storage, API, agent isolation, events, auto-title."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.agent import agent_loops
from backend.agent.agent_loops import AgentLoopsMixin
from backend.agent.autonomous_agent import AutonomousHealthAgent
from backend.agent.chat_threads import (
    auto_title_thread,
    clean_title,
    fallback_title,
    generate_title,
)
from backend.agent.memory_manager import MemoryManager
from backend.ios_gateway import IOSGateway
from backend.ios_gateway.threads import (
    chat_id_for,
    history_key_for,
    is_valid_thread_id,
    thread_id_from_chat_id,
    thread_id_from_history_key,
)
from backend.messaging.base import MessageChannel

TID = "a" * 32
TID2 = "b" * 32


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def test_thread_id_helpers():
    assert is_valid_thread_id("main") and is_valid_thread_id(TID)
    assert not is_valid_thread_id("") and not is_valid_thread_id("A" * 32)
    assert not is_valid_thread_id("abc") and not is_valid_thread_id(None)
    assert chat_id_for("LiveUser", "main") == "LiveUser"
    assert chat_id_for("LiveUser", None) == "LiveUser"
    assert chat_id_for("LiveUser", TID) == f"LiveUser:{TID}"
    assert history_key_for("LiveUser", "main") == "ios:LiveUser"
    assert history_key_for("LiveUser", TID) == f"ios:LiveUser:{TID}"
    assert thread_id_from_chat_id("LiveUser", "LiveUser") == "main"
    assert thread_id_from_chat_id(f"LiveUser:{TID}", "LiveUser") == TID
    assert thread_id_from_chat_id(42, "LiveUser") == "main"
    assert thread_id_from_history_key("ios:LiveUser") == "main"
    assert thread_id_from_history_key(f"ios:LiveUser:{TID}") == TID
    assert thread_id_from_history_key("telegram:9") is None


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

@pytest.fixture
def mm(tmp_path):
    return MemoryManager(tmp_path, "LiveUser")


def test_main_thread_ensured_and_listed_first(mm):
    threads = mm.list_chat_threads()
    assert [t["id"] for t in threads] == ["main"]
    assert threads[0]["title"] == "Hime" and threads[0]["pinned"] is True
    assert threads[0]["last_message"] is None
    t = mm.create_chat_thread()
    assert t["title"] == "" and len(t["id"]) == 32
    mm.update_chat_thread(t["id"], pinned=True)
    ids = [x["id"] for x in mm.list_chat_threads()]
    assert ids[0] == "main" and ids[1] == t["id"]


def test_archive_hides_unless_requested(mm):
    t = mm.create_chat_thread("x")
    mm.update_chat_thread(t["id"], archived=True)
    assert [x["id"] for x in mm.list_chat_threads()] == ["main"]
    assert {x["id"] for x in mm.list_chat_threads(include_archived=True)} == {"main", t["id"]}


async def test_persist_isolates_threads_and_bumps_updated_at(mm):
    t = mm.create_chat_thread("x")
    await mm.persist_chat_turns(history_key_for("LiveUser", t["id"]), [
        {"role": "user", "content": "hi in thread"},
        {"role": "assistant", "content": "y" * 300},
    ])
    await mm.persist_chat_turn("ios:LiveUser", "user", "hi in main")
    assert [r["content"] for r in mm.get_chat_history("ios:LiveUser")] == ["hi in main"]
    rows = mm.get_chat_history(f"ios:LiveUser:{t['id']}")
    assert [r["role"] for r in rows] == ["user", "assistant"]
    got = mm.get_chat_thread(t["id"])
    assert got["last_message"]["role"] == "assistant"
    assert len(got["last_message"]["content"]) == 120
    assert mm.get_chat_thread("main")["last_message"]["content"] == "hi in main"


async def test_delete_removes_rows_but_not_main(mm):
    t = mm.create_chat_thread("x")
    await mm.persist_chat_turn(f"ios:LiveUser:{t['id']}", "user", "bye")
    await mm.persist_chat_turn("ios:LiveUser", "user", "keep")
    assert mm.delete_chat_thread(t["id"]) is True
    assert mm.get_chat_thread(t["id"]) is None
    assert mm.get_chat_history(f"ios:LiveUser:{t['id']}") == []
    assert mm.delete_chat_thread("main") is False
    assert len(mm.get_chat_history("ios:LiveUser")) == 1


# ---------------------------------------------------------------------------
# HTTP API
# ---------------------------------------------------------------------------

def _patch_agents(agents):
    return (
        patch("backend.api.agent_lifecycle.active_agents", agents),
        patch("backend.api.agent_state.active_agents", agents),
        patch("backend.api.agent_threads.active_agents", agents),
    )


class TestThreadRoutes:
    async def test_crud_flow_and_events(self, test_client):
        agent = MagicMock()
        agent._emit = AsyncMock()
        agent._chat_histories = {history_key_for("LiveUser", "x"): []}
        agents = {"LiveUser": {"agent": agent}}
        p1, p2, p3 = _patch_agents(agents)
        with p1, p2, p3:
            r = await test_client.get("/api/agent/chat/threads")
            assert r.status_code == 200
            assert [t["id"] for t in r.json()["threads"]] == ["main"]

            r = await test_client.post("/api/agent/chat/threads", json={})
            tid = r.json()["thread"]["id"]
            assert r.json()["thread"]["title"] == "" and len(tid) == 32
            ev = agent._emit.await_args.args[0]
            assert ev["type"] == "chat_thread_updated" and ev["thread"]["id"] == tid

            r = await test_client.post("/api/agent/chat/threads", json={"title": "Sleep"})
            assert r.json()["thread"]["title"] == "Sleep"

            r = await test_client.patch(
                f"/api/agent/chat/threads/{tid}", json={"title": "Renamed", "pinned": True},
            )
            th = r.json()["thread"]
            assert th["title"] == "Renamed" and th["pinned"] is True

            r = await test_client.patch(f"/api/agent/chat/threads/{tid}", json={"archived": True})
            assert r.json()["thread"]["archived"] is True
            r = await test_client.get("/api/agent/chat/threads")
            assert tid not in [t["id"] for t in r.json()["threads"]]
            r = await test_client.get("/api/agent/chat/threads?include_archived=true")
            assert tid in [t["id"] for t in r.json()["threads"]]

            agent._chat_histories[history_key_for("LiveUser", tid)] = [{"role": "user", "content": "q"}]
            r = await test_client.delete(f"/api/agent/chat/threads/{tid}")
            assert r.json() == {"success": True}
            assert history_key_for("LiveUser", tid) not in agent._chat_histories
            agent.stop_chat.assert_called_with(tid)
            ev = agent._emit.await_args.args[0]
            assert ev == {"type": "chat_thread_deleted", "thread_id": tid}
            r = await test_client.delete(f"/api/agent/chat/threads/{tid}")
            assert r.status_code == 404

    async def test_main_protections_and_validation(self, test_client):
        with patch("backend.api.agent_lifecycle.active_agents", {}), \
             patch("backend.api.agent_state.active_agents", {}), \
             patch("backend.api.agent_threads.active_agents", {}):
            assert (await test_client.delete("/api/agent/chat/threads/main")).status_code == 400
            r = await test_client.patch("/api/agent/chat/threads/main", json={"archived": True})
            assert r.status_code == 400
            r = await test_client.patch("/api/agent/chat/threads/main", json={"title": "x"})
            assert r.status_code == 400
            # pinning main is a harmless no-op
            r = await test_client.patch("/api/agent/chat/threads/main", json={"pinned": True})
            assert r.status_code == 200 and r.json()["thread"]["title"] == "Hime"
            assert (await test_client.delete("/api/agent/chat/threads/not-hex")).status_code == 400
            r = await test_client.patch(f"/api/agent/chat/threads/{TID}", json={"title": "x"})
            assert r.status_code == 404

    async def test_chat_history_thread_param(self, test_client, tmp_dirs):
        mem = MemoryManager(tmp_dirs["memory"], "LiveUser")
        t = mem.create_chat_thread("t")
        await mem.persist_chat_turn("ios:LiveUser", "user", "main msg")
        await mem.persist_chat_turn(f"ios:LiveUser:{t['id']}", "user", "thread msg")
        with patch("backend.api.agent_lifecycle.active_agents", {}), \
             patch("backend.api.agent_state.active_agents", {}):
            r = await test_client.get("/api/agent/chat-history")
            assert [m["content"] for m in r.json()["messages"]] == ["main msg"]
            r = await test_client.get(f"/api/agent/chat-history?thread_id={t['id']}")
            assert [m["content"] for m in r.json()["messages"]] == ["thread msg"]
            assert (await test_client.get("/api/agent/chat-history?thread_id=zzz")).status_code == 400
            r = await test_client.get(f"/api/agent/chat-history?thread_id={TID}")
            assert r.status_code == 404

    async def test_post_chat_routes_to_thread(self, test_client, tmp_dirs):
        mem = MemoryManager(tmp_dirs["memory"], "LiveUser")
        t = mem.create_chat_thread("t")
        mem.update_chat_thread(t["id"], archived=True)
        agent = MagicMock()
        agent.inbox.push = AsyncMock()
        agent._emit = AsyncMock()
        agents = {"LiveUser": {"agent": agent}}
        p1, p2, p3 = _patch_agents(agents)
        with p1, p2, p3:
            # backward compatible: no thread_id -> main
            r = await test_client.post("/api/agent/chat", json={"text": "hello"})
            assert r.json()["queued"] is True
            assert agent.inbox.push.await_args.args[0].chat_id == "LiveUser"

            # archived thread: auto-unarchive + routed to the thread
            r = await test_client.post(
                "/api/agent/chat", json={"text": "hi", "thread_id": t["id"]},
            )
            assert r.json()["queued"] is True
            env = agent.inbox.push.await_args.args[0]
            assert env.chat_id == f"LiveUser:{t['id']}"
            assert env.channel == MessageChannel.IOS
            assert mem.get_chat_thread(t["id"])["archived"] is False
            assert agent._emit.await_args.args[0]["type"] == "chat_thread_updated"

            r = await test_client.post("/api/agent/chat", json={"text": "x", "thread_id": TID})
            assert r.status_code == 404
            r = await test_client.post("/api/agent/chat", json={"text": "x", "thread_id": "bad"})
            assert r.status_code == 400

    async def test_stop_route_scoped_by_thread(self, test_client):
        agent = MagicMock()
        agent.stop_chat = MagicMock(return_value=False)
        agents = {"LiveUser": {"agent": agent}}
        p1, p2, p3 = _patch_agents(agents)
        with p1, p2, p3:
            r = await test_client.post("/api/agent/chat/stop", json={"thread_id": TID})
            assert r.json() == {"success": True, "stopped": False}
            agent.stop_chat.assert_called_with(TID)
            await test_client.post("/api/agent/chat/stop", json={})
            agent.stop_chat.assert_called_with(None)
            r = await test_client.post("/api/agent/chat/stop", json={"thread_id": "bad"})
            assert r.status_code == 400


# ---------------------------------------------------------------------------
# Agent: emit stamping, stop scoping, per-thread context + persistence
# ---------------------------------------------------------------------------

def _emit_agent():
    agent = MagicMock(spec=AutonomousHealthAgent)
    agent.user_id = "LiveUser"
    agent._chat_task = None
    agent._chat_stop_requested = False
    agent._chat_run_id = None
    agent._chat_thread_id = None
    agent._event_queue = asyncio.Queue()
    agent.stop_chat = AutonomousHealthAgent.stop_chat.__get__(agent)
    agent._emit = AutonomousHealthAgent._emit.__get__(agent)
    return agent


async def test_emit_stamps_thread_id():
    agent = _emit_agent()
    agent._chat_thread_id = TID
    await agent._emit({"type": "chat_content", "content": "x"})
    await agent._emit({"type": "chat_reply", "chat_id": "LiveUser", "content": "proactive"})
    await agent._emit({"type": "chat_reply", "chat_id": f"LiveUser:{TID2}"})
    await agent._emit({"type": "chat_thread_updated", "thread": {"id": TID}})
    await agent._emit({"type": "token_usage"})
    got = [agent._event_queue.get_nowait() for _ in range(5)]
    assert got[0]["thread_id"] == TID
    assert got[1]["thread_id"] == "main"  # explicit chat_id wins over the active run
    assert got[2]["thread_id"] == TID2
    assert "thread_id" not in got[3] and "thread_id" not in got[4]


async def test_stop_chat_scoped_by_thread():
    agent = _emit_agent()
    task = asyncio.create_task(asyncio.sleep(60))
    agent._chat_task = task
    agent._chat_thread_id = TID
    assert agent.stop_chat(TID2) is False and not task.cancelled()
    assert agent.stop_chat("main") is False
    assert agent.stop_chat(TID) is True
    with pytest.raises(asyncio.CancelledError):
        await task
    # omitted thread id: stop whatever is active
    agent._chat_task = task2 = asyncio.create_task(asyncio.sleep(60))
    assert agent.stop_chat() is True
    with pytest.raises(asyncio.CancelledError):
        await task2


def _ios_envelope(chat_id: str, text: str):
    env = MagicMock()
    env.content = text
    env.chat_id = chat_id
    env.sender_id = "LiveUser"
    env.timestamp = datetime.now(timezone.utc)
    env.channel = MessageChannel.IOS
    env.attachments = None
    env.message_id = "m-" + text
    return env


def _chat_agent(mem):
    agent = MagicMock(spec=AgentLoopsMixin)
    agent.user_id = "LiveUser"
    agent._chat_histories = {}
    agent._max_chat_history = 20
    agent.user_messages_received = 0
    agent.max_turns = 10
    agent.tool_registry = MagicMock()
    agent.tool_registry.get_tool = MagicMock(return_value=None)
    agent._set_state = MagicMock()
    agent._event_queue = asyncio.Queue()
    agent._chat_run_id = None
    agent._chat_thread_id = None
    agent._chat_stop_requested = False
    agent._emit = AutonomousHealthAgent._emit.__get__(agent)
    agent._save_state = MagicMock()
    agent._auto_reply = AsyncMock(return_value={"sent": True, "error": ""})
    agent._format_message_timestamp = MagicMock(return_value="2026-04-01 17:31")
    agent._get_system_prompt = MagicMock(return_value="System prompt")
    agent._get_chat_tool_definitions = MagicMock(return_value=[])
    agent._execute_tool = AsyncMock(return_value={"success": True, "message": "sent"})
    agent._chat_memory = lambda: mem
    agent.llm = MagicMock()
    agent._handle_chat_message = AgentLoopsMixin._handle_chat_message.__get__(agent)
    return agent


def _drain(agent):
    out = []
    while not agent._event_queue.empty():
        out.append(agent._event_queue.get_nowait())
    return out


async def _run_turn(agent, chat_id: str, text: str, reply: str):
    script = iter([
        ("", [{"name": "reply_user", "id": "r1", "arguments": {"message": reply}}], "s"),
        ("done", [], "s"),
    ])

    async def _llm(self_ref, messages, tools, **kw):
        return next(script)

    with patch.object(agent_loops, "_llm_call", _llm), \
         patch.object(agent_loops, "settings") as st, \
         patch("backend.agent.chat_threads.auto_title_thread", new=AsyncMock()) as titler:
        st.CHAT_MAX_TURNS = 8
        await agent._handle_chat_message(_ios_envelope(chat_id, text))
    await asyncio.sleep(0.1)  # let fire-and-forget persistence land
    return titler


async def test_per_thread_context_persistence_and_events(tmp_path):
    mem = MemoryManager(tmp_path, "LiveUser")
    t = mem.create_chat_thread("")
    agent = _chat_agent(mem)

    await _run_turn(agent, "LiveUser", "main question", "main answer")
    main_events = _drain(agent)
    titler = await _run_turn(agent, f"LiveUser:{t['id']}", "thread question", "thread answer")
    thread_events = _drain(agent)

    # agent sliding-window context is keyed per thread
    assert set(agent._chat_histories) == {"ios:LiveUser", f"ios:LiveUser:{t['id']}"}
    assert "main question" in agent._chat_histories["ios:LiveUser"][0]["content"]
    assert "thread question" in agent._chat_histories[f"ios:LiveUser:{t['id']}"][0]["content"]
    assert all("thread" not in m["content"] for m in agent._chat_histories["ios:LiveUser"])

    # persisted transcripts are isolated
    assert [r["content"] for r in mem.get_chat_history("ios:LiveUser")] == [
        "main question", "main answer"]
    assert [r["content"] for r in mem.get_chat_history(f"ios:LiveUser:{t['id']}")] == [
        "thread question", "thread answer"]

    # every chat event names its thread
    assert main_events and all(e["thread_id"] == "main" for e in main_events
                               if e["type"].startswith("chat_") or e["type"] == "user_message")
    chat_ev = [e for e in thread_events
               if e["type"].startswith("chat_") or e["type"] == "user_message"]
    assert chat_ev and all(e["thread_id"] == t["id"] for e in chat_ev)
    assert any(e["type"] == "user_message" for e in chat_ev)

    # auto-title only triggers for the non-main thread's first reply
    titler.assert_awaited_once()
    assert titler.await_args.args[1] == t["id"]


async def test_clear_command_scoped_to_thread(tmp_path):
    mem = MemoryManager(tmp_path, "LiveUser")
    t = mem.create_chat_thread("t")
    agent = _chat_agent(mem)
    await _run_turn(agent, "LiveUser", "m", "ma")
    await _run_turn(agent, f"LiveUser:{t['id']}", "q", "qa")
    _drain(agent)
    await agent._handle_chat_message(_ios_envelope(f"LiveUser:{t['id']}", "/clear"))
    ev = _drain(agent)
    cleared = [e for e in ev if e["type"] == "chat_cleared"]
    assert cleared and cleared[0]["thread_id"] == t["id"]
    assert mem.get_chat_history(f"ios:LiveUser:{t['id']}") == []
    assert len(mem.get_chat_history("ios:LiveUser")) == 2
    assert "ios:LiveUser" in agent._chat_histories


# ---------------------------------------------------------------------------
# Gateway: thread routing, proactive -> main
# ---------------------------------------------------------------------------

class _FakeAgent:
    def __init__(self):
        self.events = []

    async def _emit(self, ev):
        self.events.append(ev)


class _FakeAPNs:
    enabled = True

    def __init__(self):
        self.calls = []

    async def send(self, user_id, title, body, data=None, **kw):
        self.calls.append(data)
        return 1


async def test_gateway_events_and_apns_carry_thread():
    import backend.api.agent_state as st
    from backend.ios_gateway import ios_connections

    fa = _FakeAgent()
    apns = _FakeAPNs()
    gw = IOSGateway("LiveUser", apns_sender=apns)
    saved = dict(st.active_agents)
    st.active_agents.clear()
    st.active_agents["LiveUser"] = {"agent": fa}
    try:
        with patch.object(ios_connections, "is_online", return_value=False):
            await gw.send_message("in thread", chat_id=f"LiveUser:{TID}")
            gw._last_apns_ts = None
            await gw.send_message("proactive report", report_id=3)  # default target
    finally:
        st.active_agents.clear()
        st.active_agents.update(saved)
    assert fa.events[0]["thread_id"] == TID
    assert fa.events[1]["thread_id"] == "main"
    assert apns.calls[0]["thread_id"] == TID
    assert "thread_id" not in apns.calls[1]  # main = default deep-link


async def test_proactive_report_persists_to_main(tmp_path):
    from backend.agent.tools.push_report_tool import PushReportTool

    mem = MemoryManager(tmp_path, "LiveUser")
    t = mem.create_chat_thread("t")
    tool = object.__new__(PushReportTool)
    tool.user_id = "LiveUser"
    tool._memory = mem
    await tool._persist_ios_history("daily digest", None, report_id=7)
    rows = mem.get_chat_history("ios:LiveUser")
    assert rows and rows[-1]["report_id"] == 7
    assert mem.get_chat_history(f"ios:LiveUser:{t['id']}") == []


# ---------------------------------------------------------------------------
# Auto-title
# ---------------------------------------------------------------------------

def test_title_cleaning():
    assert clean_title('"Sleep trends."\nextra') == "Sleep trends"
    assert len(clean_title("x" * 80)) == 24
    assert clean_title("") == ""
    assert fallback_title("  how   did I\nsleep last night, really?  ") == "how did I sleep last nig"


class _TitleLLM:
    def __init__(self, text=None, error=None):
        self.text, self.error = text, error
        self.model = "fake"

    async def complete(self, messages, tools=None, stream=True, max_tokens=None, temperature=0.7):
        assert max_tokens is not None and max_tokens <= 64
        if self.error:
            raise self.error
        yield {"type": "content", "content": self.text}


async def test_generate_title_success_and_fallback():
    assert await generate_title(_TitleLLM("Sleep review"), "how was my sleep?") == "Sleep review"
    got = await generate_title(_TitleLLM(error=RuntimeError("boom")), "how was my sleep tonight please")
    assert got == "how was my sleep tonight"
    assert await generate_title(_TitleLLM(""), "short question") == "short question"


async def test_generate_title_timeout_falls_back():
    class _Slow(_TitleLLM):
        async def complete(self, *a, **k):
            await asyncio.sleep(5)
            yield {"type": "content", "content": "late"}

    with patch("backend.agent.chat_threads._TITLE_TIMEOUT_S", 0.05):
        assert await generate_title(_Slow(), "hello there") == "hello there"


async def test_auto_title_thread_sets_title_and_emits(tmp_path):
    mem = MemoryManager(tmp_path, "LiveUser")
    t = mem.create_chat_thread("")
    agent = MagicMock()
    agent._chat_memory = lambda: mem
    agent.llm = _TitleLLM("Heart rate")
    agent._emit = AsyncMock()
    await auto_title_thread(agent, t["id"], "why is my heart rate high", "because")
    assert mem.get_chat_thread(t["id"])["title"] == "Heart rate"
    ev = agent._emit.await_args.args[0]
    assert ev["type"] == "chat_thread_updated" and ev["thread"]["title"] == "Heart rate"

    # already-titled threads are left alone
    agent._emit.reset_mock()
    await auto_title_thread(agent, t["id"], "another", "x")
    agent._emit.assert_not_awaited()
    assert mem.get_chat_thread(t["id"])["title"] == "Heart rate"


async def test_auto_title_thread_failure_uses_first_message(tmp_path):
    mem = MemoryManager(tmp_path, "LiveUser")
    t = mem.create_chat_thread("")
    agent = MagicMock()
    agent._chat_memory = lambda: mem
    agent.llm = _TitleLLM(error=RuntimeError("down"))
    agent._emit = AsyncMock()
    await auto_title_thread(agent, t["id"], "what should I eat before a run?", "x")
    assert mem.get_chat_thread(t["id"])["title"] == "what should I eat before"
    agent._emit.assert_awaited_once()
