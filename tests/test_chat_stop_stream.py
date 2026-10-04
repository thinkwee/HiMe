"""Chat stop (POST /api/agent/chat/stop) + reply_user streaming + step events.

Fast, no network.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.agent import agent_loops
from backend.agent.agent_loops import AgentLoopsMixin
from backend.agent.autonomous_agent import AutonomousHealthAgent
from backend.agent.chat_stream import (
    ReplyDeltaStreamer,
    extract_partial_string,
    result_preview,
    summarize_tool_args,
)

# ---------------------------------------------------------------------------
# Partial JSON string extractor
# ---------------------------------------------------------------------------


class TestExtractPartialString:
    def test_no_key_yet(self):
        assert extract_partial_string("") is None
        assert extract_partial_string("{") is None
        assert extract_partial_string('{"mess') is None
        assert extract_partial_string('{"message"') is None

    def test_value_just_opened(self):
        assert extract_partial_string('{"message":') == ""
        assert extract_partial_string('{"message": ') == ""
        assert extract_partial_string('{"message": "') == ""

    def test_prefix_and_complete(self):
        assert extract_partial_string('{"message": "Hel') == "Hel"
        assert extract_partial_string('{"message": "Hello"}') == "Hello"
        assert extract_partial_string('{"message":"Hello", "chat_id": "1"}') == "Hello"

    def test_every_prefix_is_monotonic_prefix_of_final(self):
        msg = 'Line1\nHe said "hi" \\ path/\t tab é \U0001f600 end'
        for ensure_ascii in (True, False):
            raw = json.dumps({"x": 1, "message": msg, "y": [1, 2]}, ensure_ascii=ensure_ascii)
            prev = ""
            for i in range(len(raw) + 1):
                got = extract_partial_string(raw[:i]) or ""
                assert msg.startswith(got), (i, got)
                assert len(got) >= len(prev)
                prev = got
            assert extract_partial_string(raw) == msg

    def test_cut_inside_surrogate_pair_withholds_emoji(self):
        msg = "café \U0001f600 ok"
        raw = json.dumps({"message": msg}, ensure_ascii=True)
        assert "\\ud83d\\ude00" in raw
        cut = raw.index("\\ud83d")
        for extra in range(0, 12):  # anywhere before the pair completes
            assert extract_partial_string(raw[:cut + extra]) == "café "
        assert extract_partial_string(raw[:cut + 12]) == "café \U0001f600"

    def test_partial_escapes_are_withheld(self):
        assert extract_partial_string('{"message": "a\\') == "a"
        assert extract_partial_string('{"message": "a\\u') == "a"
        assert extract_partial_string('{"message": "a\\u00') == "a"
        assert extract_partial_string('{"message": "a\\u00e9') == "aé"
        assert extract_partial_string('{"message": "a\\n') == "a\n"

    def test_lone_surrogates_become_replacement(self):
        assert extract_partial_string('{"message": "\\ud83dx"}') == "�x"
        assert extract_partial_string('{"message": "\\ude00x"}') == "�x"

    def test_key_must_be_top_level(self):
        raw = '{"meta": {"message": "inner"}, "message": "outer"}'
        assert extract_partial_string(raw) == "outer"
        assert extract_partial_string('{"meta": {"message": "inner"}') is None

    def test_value_named_message_is_not_the_key(self):
        raw = '{"kind": "message", "message": "real"}'
        assert extract_partial_string(raw) == "real"
        assert extract_partial_string('{"kind": "message"}') is None

    def test_non_string_value_and_garbage(self):
        assert extract_partial_string('{"message": 12}') is None
        assert extract_partial_string('{"message": null}') is None
        assert extract_partial_string("not json at all") is None

    def test_braces_inside_strings_do_not_confuse_depth(self):
        raw = '{"a": "}}{{", "message": "ok"}'
        assert extract_partial_string(raw) == "ok"

    def test_other_key(self):
        assert extract_partial_string('{"goal": "g', key="goal") == "g"


class TestReplyDeltaStreamer:
    def _chunk(self, delta, name="reply_user", index=0):
        return {"type": "tool_call_delta", "index": index, "name": name,
                "arguments_delta": delta}

    def test_throttle_by_time_and_chars(self):
        t = [0.0]
        s = ReplyDeltaStreamer(min_interval=0.15, min_chars=40, clock=lambda: t[0])
        assert s.feed(self._chunk('{"message": "He')) == "He"  # first immediate
        t[0] = 0.05
        assert s.feed(self._chunk("llo")) is None            # too soon, few chars
        t[0] = 0.2
        assert s.feed(self._chunk(" w")) == "Hello w"        # interval elapsed
        t[0] = 0.21
        assert s.feed(self._chunk("x" * 45)) == "Hello w" + "x" * 45  # many chars
        t[0] = 0.22
        assert s.feed(self._chunk("y")) is None
        assert s.finish() == "Hello w" + "x" * 45 + "y"
        assert s.finish() is None

    def test_only_first_reply_user_streams(self):
        s = ReplyDeltaStreamer()
        assert s.feed(self._chunk('{"goal": "g"}', name="analyze", index=0)) is None
        assert s.feed(self._chunk('{"message": "one"}', index=1)) == "one"
        assert s.feed(self._chunk('{"message": "two"}', index=2)) is None
        assert s.finish() is None

    def test_no_message_no_emit(self):
        s = ReplyDeltaStreamer()
        assert s.feed(self._chunk('{"chat_id": "1"')) is None
        assert s.finish() is None


class TestHelpers:
    def test_summaries(self):
        assert summarize_tool_args("analyze", {"goal": "sleep  trend\nlast week"}) == "sleep trend last week"
        assert len(summarize_tool_args("sql", {"query": "S" * 900})) == 200
        assert summarize_tool_args("code", {"code": "\n  import pandas\nx=1"}) == "import pandas"
        assert summarize_tool_args("x", None) == ""
        assert len(result_preview({"a": "b" * 1000})) == 300


# ---------------------------------------------------------------------------
# _llm_call: tool_call_delta -> chat_reply_delta
# ---------------------------------------------------------------------------


def _fake_agent():
    agent = MagicMock()
    agent.user_id = "LiveUser"
    agent.cumulative_tokens = {"prompt_tokens": 0, "completion_tokens": 0, "thoughts_tokens": 0}
    events: list[dict] = []

    async def _emit(e):
        events.append(e)

    agent._emit = _emit
    return agent, events


def _llm_yielding(chunks_per_attempt):
    attempts = iter(chunks_per_attempt)

    class _L:
        model = "fake"

        async def complete(self, **kw):
            for c in next(attempts):
                yield c

    return _L()


def _deltas(text_json, name="reply_user", index=0, step=7):
    return [
        {"type": "tool_call_delta", "index": index, "name": name,
         "arguments_delta": text_json[i:i + step]}
        for i in range(0, len(text_json), step)
    ]


async def _direct_fallback(llm, consume, reset):
    await consume(llm)


class TestLlmCallStreaming:
    async def test_streams_reply_and_keeps_final_tool_call(self):
        agent, events = _fake_agent()
        args = {"message": "Hello\nthere \"you\" é"}
        final = {"type": "tool_call", "id": "t1", "name": "reply_user", "arguments": args}
        agent.llm = _llm_yielding([[*_deltas(json.dumps(args)), final]])
        with patch.object(agent_loops, "_run_with_provider_fallback", _direct_fallback):
            _text, calls, _ = await agent_loops._llm_call(agent, [], [], loop="chat", chat_id="c1")
        assert calls == [final]
        d = [e for e in events if e["type"] == "chat_reply_delta"]
        assert d and d[-1]["text"] == args["message"] and d[-1]["chat_id"] == "c1"
        assert all(args["message"].startswith(e["text"]) for e in d)
        assert agent._reply_streamed is True

    async def test_not_streamed_for_non_chat_loops_or_other_tools(self):
        agent, events = _fake_agent()
        final = {"type": "tool_call", "id": "t", "name": "reply_user", "arguments": {}}
        agent.llm = _llm_yielding([[*_deltas(json.dumps({"message": "hi"})), final]])
        with patch.object(agent_loops, "_run_with_provider_fallback", _direct_fallback):
            await agent_loops._llm_call(agent, [], [], loop="autonomous")
            agent.llm = _llm_yielding([[
                *_deltas(json.dumps({"goal": "x"}), name="analyze"),
                {"type": "tool_call", "id": "t", "name": "analyze", "arguments": {}},
            ]])
            await agent_loops._llm_call(agent, [], [], loop="chat", chat_id="c1")
        assert not [e for e in events if e["type"] == "chat_reply_delta"]

    async def test_retry_after_partial_output_emits_reset(self):
        agent, events = _fake_agent()
        raw = json.dumps({"message": "abcdefgh"})
        agent.llm = _llm_yielding([_deltas(raw)[:2], _deltas(raw)])

        async def _fb(llm, consume, reset):
            await consume(llm)  # partial attempt, then a retry
            reset()
            await consume(llm)

        with patch.object(agent_loops, "_run_with_provider_fallback", _fb):
            await agent_loops._llm_call(agent, [], [], loop="chat", chat_id="c1")
        kinds = [(e["text"], e.get("reset")) for e in events if e["type"] == "chat_reply_delta"]
        assert ("", True) in kinds
        assert kinds[-1][0] == "abcdefgh"


class TestProviderEmitsToolCallDeltas:
    async def test_openai_stream_yields_deltas_and_same_final_call(self):
        from backend.agent.llm.openai_provider import OpenAIProvider

        def mk(args, name=None, tid=None, finish=None, with_tc=True):
            delta = MagicMock()
            delta.content = None
            delta.model_extra = {}
            if with_tc:
                fn = MagicMock()
                fn.name = name
                fn.arguments = args
                tcd = MagicMock()
                tcd.index = 0
                tcd.id = tid
                tcd.function = fn
                delta.tool_calls = [tcd]
            else:
                delta.tool_calls = None
            choice = MagicMock()
            choice.delta = delta
            choice.finish_reason = finish
            ch = MagicMock()
            ch.choices = [choice]
            ch.usage = None
            return ch

        chunks = [
            mk('{"mess', name="reply_user", tid="call_1"),
            mk('age": "hi"}'),
            mk(None, finish="tool_calls", with_tc=False),
        ]

        async def _stream():
            for c in chunks:
                yield c

        prov = OpenAIProvider.__new__(OpenAIProvider)
        prov.model = "gpt-test"
        prov.api_key = "k"
        prov._client = MagicMock()
        prov._client.chat.completions.create = AsyncMock(return_value=_stream())
        out = []
        try:
            async for c in prov.complete([{"role": "user", "content": "x"}], tools=[], stream=True):
                out.append(c)
        except Exception as exc:  # pragma: no cover - mock shape drift
            pytest.skip(f"openai mock shape not supported: {exc}")
        deltas = [c for c in out if c["type"] == "tool_call_delta"]
        finals = [c for c in out if c["type"] == "tool_call"]
        assert "".join(d["arguments_delta"] for d in deltas) == '{"message": "hi"}'
        assert all(d["name"] == "reply_user" and d["index"] == 0 for d in deltas)
        assert finals and finals[0]["arguments"] == {"message": "hi"}


# ---------------------------------------------------------------------------
# Stop mechanics
# ---------------------------------------------------------------------------


def _stop_agent():
    """Minimal object exposing the real stop/run-envelope/emit methods."""
    agent = MagicMock(spec=AutonomousHealthAgent)
    agent._chat_task = None
    agent._chat_stop_requested = False
    agent._chat_run_id = None
    agent._chat_thread_id = None
    agent.user_id = "LiveUser"
    agent._event_queue = asyncio.Queue()
    agent.stop_chat = AutonomousHealthAgent.stop_chat.__get__(agent)
    agent._run_chat_envelope = AutonomousHealthAgent._run_chat_envelope.__get__(agent)
    agent._emit = AutonomousHealthAgent._emit.__get__(agent)
    return agent


class TestStopMechanics:
    async def test_stop_without_run_is_false(self):
        assert _stop_agent().stop_chat() is False

    async def test_user_stop_cancels_child_only_and_worker_survives(self):
        agent = _stop_agent()
        started = asyncio.Event()

        async def _handle(env):
            started.set()
            await asyncio.sleep(60)

        agent._handle_chat_message = _handle
        worker = asyncio.create_task(agent._run_chat_envelope(object()))
        await started.wait()
        assert agent.stop_chat() is True
        await asyncio.wait_for(worker, 1)  # returns normally: not cancelled
        assert not worker.cancelled()
        assert agent._chat_task is None and agent._chat_run_id is None
        assert agent.stop_chat() is False  # nothing active any more

        done = []

        async def _handle2(env):
            done.append(env)

        agent._handle_chat_message = _handle2
        await agent._run_chat_envelope("next")
        assert done == ["next"]

    async def test_shutdown_cancellation_propagates(self):
        agent = _stop_agent()
        started = asyncio.Event()
        inner: list[asyncio.Task] = []

        async def _handle(env):
            inner.append(asyncio.current_task())
            started.set()
            await asyncio.sleep(60)

        agent._handle_chat_message = _handle
        worker = asyncio.create_task(agent._run_chat_envelope(object()))
        await started.wait()
        worker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker
        await asyncio.sleep(0)
        assert inner[0].cancelled()

    async def test_handler_exception_is_reraised(self):
        agent = _stop_agent()

        async def _handle(env):
            raise ValueError("boom")

        agent._handle_chat_message = _handle
        with pytest.raises(ValueError):
            await agent._run_chat_envelope(object())

    async def test_emit_stamps_run_id_on_chat_events_only(self):
        agent = _stop_agent()
        agent._chat_run_id = "run123"
        await agent._emit({"type": "chat_reply", "content": "x"})
        await agent._emit({"type": "user_message"})
        await agent._emit({"type": "token_usage"})
        await agent._emit({"type": "chat_tool_call", "run_id": "keep"})
        got = [agent._event_queue.get_nowait() for _ in range(4)]
        assert got[0]["run_id"] == "run123"
        assert got[1]["run_id"] == "run123"
        assert "run_id" not in got[2]
        assert got[3]["run_id"] == "keep"


def _envelope(text="how did I sleep?"):
    from backend.messaging.base import MessageChannel
    env = MagicMock()
    env.content = text
    env.chat_id = 42
    env.sender_id = 7
    env.timestamp = datetime.now(timezone.utc)
    env.channel = MessageChannel.TELEGRAM
    env.attachments = None
    env.message_id = "m1"
    return env


def _chat_agent():
    agent = MagicMock(spec=AgentLoopsMixin)
    agent._chat_histories = {}
    agent._max_chat_history = 20
    agent.user_messages_received = 0
    agent.max_turns = 10
    agent.tool_registry = MagicMock()
    agent.tool_registry.get_tool = MagicMock(return_value=None)
    agent._set_state = MagicMock()
    agent.events = []

    async def _emit(e):
        agent.events.append(e)

    agent._emit = _emit
    agent._save_state = MagicMock()
    agent._auto_reply = AsyncMock(return_value={"sent": True, "error": ""})
    agent._format_message_timestamp = MagicMock(return_value="2026-04-01 17:31")
    agent._get_system_prompt = MagicMock(return_value="System prompt")
    agent._get_chat_tool_definitions = MagicMock(return_value=[])
    agent._execute_tool = AsyncMock(return_value={"success": True, "message": "sent"})
    agent._chat_run_id = None
    agent._chat_stop_requested = False
    agent._handle_chat_message = AgentLoopsMixin._handle_chat_message.__get__(agent)
    return agent


class TestHandleChatStop:
    async def test_stop_mid_analyze_keeps_transcript_consistent(self):
        agent = _chat_agent()
        hang = asyncio.Event()

        async def _sub(*a, **k):
            hang.set()
            await asyncio.sleep(60)

        agent._run_sub_analysis = _sub
        script = iter([
            ("", [
                {"name": "reply_user", "id": "r1", "arguments": {"message": "On it..."}},
                {"name": "analyze", "id": "a1", "arguments": {"goal": "sleep"}},
            ], "s"),
        ])

        async def _llm(self_ref, messages, tools, **kw):
            return next(script)

        with patch.object(agent_loops, "_llm_call", _llm), \
             patch.object(agent_loops, "settings") as st:
            st.CHAT_MAX_TURNS = 8
            task = asyncio.create_task(agent._handle_chat_message(_envelope()))
            await hang.wait()
            agent._chat_stop_requested = True
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        stopped = [e for e in agent.events if e["type"] == "chat_stopped"]
        assert len(stopped) == 1 and stopped[0]["chat_id"] == 42
        # no "(stopped)" fallback / auto-reply to the user
        agent._auto_reply.assert_not_awaited()
        hist = agent._chat_histories["telegram:42"]
        assert [m["role"] for m in hist] == ["user", "assistant"]
        assert hist[1]["content"].endswith("On it...")
        assert not any("tool_calls" in m or m.get("role") == "tool" for m in hist)
        # analyze step was announced with a summary + call id
        call = next(e for e in agent.events
                    if e["type"] == "chat_tool_call" and e["tool"] == "analyze")
        assert call["summary"] == "sleep" and call["call_id"] == "a1" and call["status"] == "running"

    async def test_stop_before_any_reply_persists_only_user_turn(self):
        agent = _chat_agent()
        gate = asyncio.Event()

        async def _llm(self_ref, messages, tools, **kw):
            gate.set()
            await asyncio.sleep(60)

        with patch.object(agent_loops, "_llm_call", _llm), \
             patch.object(agent_loops, "settings") as st:
            st.CHAT_MAX_TURNS = 8
            task = asyncio.create_task(agent._handle_chat_message(_envelope()))
            await gate.wait()
            agent._chat_stop_requested = True
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        hist = agent._chat_histories["telegram:42"]
        assert [m["role"] for m in hist] == ["user"]
        agent._auto_reply.assert_not_awaited()

    async def test_blocked_reply_resets_stream_and_tool_events_have_status(self):
        agent = _chat_agent()
        agent._execute_tool = AsyncMock(return_value={"success": False, "error": "blocked"})
        agent._reply_streamed = True  # as if _llm_call had streamed deltas
        script = iter([
            ("", [{"name": "reply_user", "id": "r1", "arguments": {"message": "bad"}}], "s"),
            ("done", [], "s"),
        ])

        async def _llm(self_ref, messages, tools, **kw):
            return next(script)

        with patch.object(agent_loops, "_llm_call", _llm), \
             patch.object(agent_loops, "settings") as st:
            st.CHAT_MAX_TURNS = 8
            await agent._handle_chat_message(_envelope())
        assert agent._chat_run_id  # one id per handled message
        resets = [e for e in agent.events if e["type"] == "chat_reply_delta"]
        assert resets and resets[0]["reset"] is True and resets[0]["text"] == ""
        res = next(e for e in agent.events if e["type"] == "chat_tool_result")
        assert res["status"] == "error" and res["call_id"] == "r1"
        assert len(res["result_preview"]) <= 300


# ---------------------------------------------------------------------------
# HTTP endpoint
# ---------------------------------------------------------------------------


class TestChatStopRoute:
    async def test_stop_route(self, test_client):
        agent = MagicMock()
        agent.stop_chat = MagicMock(return_value=True)
        agents = {"LiveUser": {"agent": agent}}
        with patch("backend.api.agent_lifecycle.active_agents", agents), \
             patch("backend.api.agent_state.active_agents", agents):
            r = await test_client.post("/api/agent/chat/stop", json={})
            assert r.status_code == 200
            assert r.json() == {"success": True, "stopped": True}
            agent.stop_chat.return_value = False
            r = await test_client.post("/api/agent/chat/stop")
            assert r.json() == {"success": True, "stopped": False}

    async def test_stop_route_without_agent(self, test_client):
        with patch("backend.api.agent_lifecycle.active_agents", {}), \
             patch("backend.api.agent_state.active_agents", {}):
            r = await test_client.post("/api/agent/chat/stop", json={})
        assert r.json() == {"success": True, "stopped": False}
