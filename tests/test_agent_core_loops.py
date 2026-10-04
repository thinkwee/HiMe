"""Regression tests for the agent-core bug sweep: loops, fallback, providers,
trigger evaluator, state persistence.  Fast, no network.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from backend.agent import agent_loops
from backend.agent.agent_loops import (
    AgentLoopsMixin,
    _build_tool_result_msg,
    _normalize_history,
    _run_with_provider_fallback,
    _trim_history,
    _yield_missing_tool_results,
)
from backend.agent.autonomous_agent import AutonomousHealthAgent
from backend.agent.cancellation import CancellationToken
from backend.agent.cancellation import CancelledError as AgentCancelled
from backend.agent.errors import (
    ErrorCategory,
    FallbackTriggered,
    classify_error,
    is_context_overflow_message,
)
from backend.agent.tools.base import current_tool_role


def run_sub_result(**kw):
    return {"success": True, "findings": "", "charts": [], "evidence": [], **kw}


# ---------------------------------------------------------------------------
# Error classification / retry predicates
# ---------------------------------------------------------------------------

class TestErrorClassification:
    def test_token_count_is_not_a_status_code(self):
        from backend.agent.llm import _is_capacity_error, _is_retryable
        exc = Exception("This model's maximum context length is 128000 tokens. You requested 129529 tokens")
        assert not _is_capacity_error(exc)
        assert not _is_retryable(exc)
        assert classify_error(exc).category == ErrorCategory.CONTEXT_OVERFLOW

    def test_real_status_codes_still_detected(self):
        from backend.agent.llm import _is_capacity_error, _is_retryable

        class E(Exception):
            status_code = 529

        assert _is_capacity_error(E("Overloaded"))
        assert _is_retryable(E("x"))
        assert _is_retryable(Exception("503 UNAVAILABLE"))
        assert _is_capacity_error(Exception("503 UNAVAILABLE high demand"))
        assert not _is_retryable(Exception("invalid api key"))

    @pytest.mark.parametrize("msg", [
        "prompt is too long: 250000 tokens > 200000 maximum",
        "The input token count (1200000) exceeds the maximum number of tokens allowed (1048576)",
        "This model's maximum context length is 8192 tokens",
        "Error code: 400 - context_length_exceeded",
    ])
    def test_context_overflow_patterns(self, msg):
        assert is_context_overflow_message(msg)
        assert classify_error(Exception(msg)).category == ErrorCategory.CONTEXT_OVERFLOW

    def test_out_of_credit_triggers_fallback_with_reason(self):
        from backend.agent.llm import retry_async

        async def go(text):
            calls = 0

            async def _call():
                nonlocal calls
                calls += 1
                raise Exception(text)

            with pytest.raises(FallbackTriggered) as ei:
                await retry_async(_call)
            return calls, ei.value

        calls, exc = asyncio.run(go("Error code: 429 - {'error': {'code': 'insufficient_quota'}}"))
        assert calls == 1 and "insufficient_quota" in exc.reason
        assert "insufficient_quota" in str(exc)
        calls, exc = asyncio.run(go("Your credit balance is too low to access the Anthropic API"))
        assert calls == 1


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------

async def _drain(agen):
    return [c async for c in agen]


class TestProviderFallbackPropagation:
    async def test_gemini_reraises_fallback_and_maps_timeout(self):
        from backend.agent.llm import gemini
        p = gemini.GeminiProvider.__new__(gemini.GeminiProvider)
        p.model = "gemini-2.5-flash"
        p.thinking_budget = 0
        p._client = MagicMock()

        async def _fb(coro, **kw):
            raise FallbackTriggered("", "m", reason="capacity")

        async def _timeout(coro, **kw):
            raise asyncio.TimeoutError()

        msgs = [{"role": "user", "content": "hi"}]
        with patch.object(gemini, "retry_async", _fb), pytest.raises(FallbackTriggered):
            await _drain(p.complete(msgs))
        with patch.object(gemini, "retry_async", _timeout), pytest.raises(FallbackTriggered) as ei:
            await _drain(p.complete(msgs))
        assert "timed out" in ei.value.reason

    async def test_anthropic_reraises_fallback(self):
        from backend.agent.llm import anthropic_provider as ap
        p = ap.AnthropicProvider.__new__(ap.AnthropicProvider)
        p.model = "claude-x"
        p.thinking_budget = None
        p._client = MagicMock()

        async def _fb(coro, **kw):
            raise FallbackTriggered("", "m")

        with patch.object(ap, "retry_async", _fb), pytest.raises(FallbackTriggered):
            await _drain(p.complete([{"role": "user", "content": "hi"}]))

    async def test_zhipuai_reraises_fallback(self):
        from backend.agent.llm import zhipuai_provider as zp
        p = zp.ZhipuAIProvider.__new__(zp.ZhipuAIProvider)
        p.model = "glm"
        p._client = MagicMock()
        p._client.chat.completions.create.side_effect = FallbackTriggered("", "m")
        with pytest.raises(FallbackTriggered):
            await _drain(p.complete([{"role": "user", "content": "hi"}]))

    async def test_bedrock_reraises_fallback_and_sends_system_prompt(self):
        from backend.agent.llm import bedrock
        p = bedrock.AmazonBedrockProvider.__new__(bedrock.AmazonBedrockProvider)
        p.model = "amazon.nova"
        p._client = MagicMock()
        seen: dict = {}

        async def _fb(coro, **kw):
            await coro()  # run the converse_stream lambda
            raise FallbackTriggered("", "m")

        def _converse_stream(**kwargs):
            seen.update(kwargs)
            return {}

        p._client.converse_stream = _converse_stream
        msgs = [
            {"role": "system", "content": "You are HIME."},
            {"role": "user", "content": "hi"},
        ]
        with patch.object(bedrock, "retry_async", _fb), pytest.raises(FallbackTriggered):
            await _drain(p.complete(msgs))
        assert seen["system"] == [{"text": "You are HIME."}]
        assert all(m["role"] != "system" for m in seen["messages"])

    def test_openai_wire_messages_strip_internal_keys(self):
        from backend.agent.llm.openai_provider import _wire_messages
        msgs = [
            {"role": "assistant", "content": None, "signature": "s", "tool_calls": []},
            {"role": "tool", "tool_call_id": "1", "content": "{}", "_tool_name": "sql"},
            {"role": "user", "content": "x"},
        ]
        out = _wire_messages(msgs)
        assert "signature" not in out[0] and "_tool_name" not in out[1]
        assert out[2] is msgs[2]
        assert msgs[0]["signature"] == "s"  # input untouched

    async def test_stall_guard_gives_first_chunk_a_longer_budget(self):
        from backend.agent.llm.openai_provider import _stall_guarded

        class Stream:
            def __init__(self, delays):
                self.delays = list(delays)

            def __aiter__(self):
                return self

            async def __anext__(self):
                if not self.delays:
                    raise StopAsyncIteration
                await asyncio.sleep(self.delays.pop(0))
                return "chunk"

            async def close(self):
                pass

        # slow first chunk (0.15s) with a 0.05s stall timeout: still fine
        got = [c async for c in _stall_guarded(Stream([0.15, 0.0]), 0.05, "m")]
        assert got == ["chunk", "chunk"]
        # a stall *between* chunks still aborts
        with pytest.raises(FallbackTriggered):
            _ = [c async for c in _stall_guarded(Stream([0.0, 0.3]), 0.05, "m")]


# ---------------------------------------------------------------------------
# Provider fallback state
# ---------------------------------------------------------------------------

class _Prov:
    def __init__(self, name, behaviour):
        self.name = name
        self.model = "m"
        self.behaviour = behaviour
        self.calls = 0


@pytest.fixture
def fb_settings():
    s = SimpleNamespace(
        FALLBACK_LLM_PROVIDER="fb", FALLBACK_LLM_MODEL="fb-model",
        LLM_FALLBACK_COOLDOWN_SECONDS=600.0,
    )
    agent_loops._primary_down_until.clear()
    agent_loops._fallback_providers.clear()
    with patch.object(agent_loops, "settings", s):
        yield s
    agent_loops._primary_down_until.clear()
    agent_loops._fallback_providers.clear()


class TestFallbackState:
    @staticmethod
    def _attempt(log):
        async def attempt(llm):
            llm.calls += 1
            log.append(llm.name)
            if llm.behaviour == "fallback":
                raise FallbackTriggered("", "m", reason="stalled")
            if llm.behaviour == "boom":
                raise RuntimeError("fallback exploded")
        return attempt

    async def test_switches_to_cached_fallback_and_skips_primary_during_cooldown(self, fb_settings):
        primary = _Prov("primary", "fallback")
        fb = _Prov("fb", "ok")
        created = []

        def _create(provider, model):
            created.append((provider, model))
            return fb

        log: list[str] = []
        with patch("backend.agent.llm_providers.create_provider", _create):
            await _run_with_provider_fallback(primary, self._attempt(log), lambda: None)
            await _run_with_provider_fallback(primary, self._attempt(log), lambda: None)
        assert log == ["primary", "fb", "fb"]       # 2nd call skipped the downed primary
        assert created == [("fb", "fb-model")]      # fallback client built once, then cached

    async def test_fallback_failure_during_cooldown_tries_primary_once(self, fb_settings):
        primary = _Prov("primary", "ok")
        fb = _Prov("fb", "boom")
        agent_loops._primary_down_until[agent_loops._provider_key(primary)] = 10 ** 12
        agent_loops._fallback_providers[("fb", "fb-model")] = fb
        log: list[str] = []
        resets: list[int] = []
        await _run_with_provider_fallback(primary, self._attempt(log), lambda: resets.append(1))
        assert log == ["fb", "primary"] and resets
        assert agent_loops._provider_key(primary) not in agent_loops._primary_down_until

    async def test_cooldown_is_keyed_by_provider_and_model(self, fb_settings):
        a, b = _Prov("a", "fallback"), _Prov("b", "ok")
        b.__class__ = type("OtherProv", (_Prov,), {})
        assert agent_loops._provider_key(a) != agent_loops._provider_key(b)

    async def test_no_fallback_configured_surfaces_the_real_error(self):
        s = SimpleNamespace(FALLBACK_LLM_PROVIDER=None, FALLBACK_LLM_MODEL=None,
                            LLM_FALLBACK_COOLDOWN_SECONDS=1.0)

        async def attempt(llm):
            raise FallbackTriggered("", "m", reason="HTTP 402: out of credit")

        with patch.object(agent_loops, "settings", s), pytest.raises(FallbackTriggered) as ei:
            await _run_with_provider_fallback(_Prov("p", "x"), attempt, lambda: None)
        assert "out of credit" in str(ei.value)


# ---------------------------------------------------------------------------
# Message helpers
# ---------------------------------------------------------------------------

class TestMessageHelpers:
    def test_synthetic_results_carry_tool_name(self):
        msgs = [
            {"role": "user", "content": "x"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "sql", "arguments": "{}"}},
            ]},
        ]
        out = _yield_missing_tool_results(msgs)
        assert out[-1]["tool_call_id"] == "c1" and out[-1]["_tool_name"] == "sql"

    def test_large_results_are_truncated(self):
        big = {"success": True, "output": "x" * 50_000}
        msg = _build_tool_result_msg("1", big, "code")
        assert len(msg["content"]) < 12_000
        assert "omitted" in msg["content"]

    def test_orchestrator_results_drop_raw_evidence(self):
        res = {"success": True, "findings": "HR avg 61", "evidence": [{"x": "y" * 5000}], "report_args": {}}
        msg = _build_tool_result_msg("1", res, "analyze")
        body = json.loads(msg["content"])
        assert body == {"success": True, "findings": "HR avg 61"}
        assert "evidence" in res  # caller's copy (evidence store) untouched

    def test_history_window_never_starts_with_assistant(self):
        hist = [
            {"role": "assistant", "content": "report 1"},
            {"role": "assistant", "content": "report 2"},
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "a"},
        ]
        assert _normalize_history(hist)[0]["role"] == "user"
        trimmed = _trim_history([*hist, {"role": "user", "content": "q2"}, {"role": "assistant", "content": "a2"}], 4)
        assert trimmed[0]["role"] == "user"
        merged = _normalize_history([
            {"role": "user", "content": "a"}, {"role": "assistant", "content": "r1"},
            {"role": "assistant", "content": "r2"}, {"role": "user", "content": "b"},
        ])
        assert [m["role"] for m in merged] == ["user", "assistant", "user"]
        assert "r1" in merged[1]["content"] and "r2" in merged[1]["content"]


# ---------------------------------------------------------------------------
# Chat loop
# ---------------------------------------------------------------------------

def _envelope(text="how did I sleep?"):
    from backend.messaging.base import MessageChannel
    env = MagicMock()
    env.content = text
    env.chat_id = 42
    env.sender_id = 7
    env.timestamp = datetime.now(timezone.utc)
    env.channel = MessageChannel.TELEGRAM
    env.attachments = None
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
    agent._emit = AsyncMock()
    agent._save_state = MagicMock()
    agent._auto_reply = AsyncMock(return_value={"sent": True, "error": ""})
    agent._format_message_timestamp = MagicMock(return_value="2026-04-01 17:31")
    agent._get_system_prompt = MagicMock(return_value="System prompt")
    agent._get_chat_tool_definitions = MagicMock(return_value=[])
    agent._execute_tool = AsyncMock(return_value={"success": True, "message": "sent"})
    agent._run_sub_analysis = AsyncMock(return_value=run_sub_result(findings="avg 7h", evidence=[]))
    agent._handle_chat_message = AgentLoopsMixin._handle_chat_message.__get__(agent)
    return agent


def _scripted_llm(script, seen=None):
    it = iter(script)

    async def _call(self_ref, messages, tools, **kw):
        if seen is not None:
            seen.append(list(messages))
        nxt = next(it)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    return _call


def _tc(name, id_, **args):
    return {"name": name, "id": id_, "arguments": args}


async def _run_chat(agent, script, seen=None, env=None):
    with patch.object(agent_loops, "_llm_call", _scripted_llm(script, seen)), \
         patch.object(agent_loops, "settings") as st:
        st.CHAT_MAX_TURNS = 8
        await agent._handle_chat_message(env or _envelope())


class TestChatLoop:
    async def test_final_text_after_ack_and_analyze_is_not_dropped(self):
        agent = _chat_agent()
        script = [
            ("", [_tc("reply_user", "r1", message="On it..."), _tc("analyze", "a1", goal="sleep")], "s"),
            ("You slept 7h on average.", [], "s"),
        ]
        await _run_chat(agent, script)
        agent._auto_reply.assert_awaited_once()
        assert agent._auto_reply.await_args.args[0] == "You slept 7h on average."

    async def test_no_double_send_when_final_reply_already_delivered(self):
        agent = _chat_agent()
        script = [
            ("", [_tc("reply_user", "r1", message="On it..."), _tc("analyze", "a1", goal="sleep")], "s"),
            ("", [_tc("reply_user", "r2", message="7h avg.")], "s"),
            ("done", [], "s"),
        ]
        await _run_chat(agent, script)
        agent._auto_reply.assert_not_awaited()

    async def test_blocked_replies_do_not_consume_the_send_budget(self):
        agent = _chat_agent()
        outcomes = iter([
            {"success": False, "error": "blocked by fact verifier"},
            {"success": False, "error": "blocked by fact verifier"},
            {"success": True, "message": "sent"},
        ])
        agent._execute_tool = AsyncMock(side_effect=lambda *a, **k: next(outcomes))
        script = [
            ("", [_tc("reply_user", "r1", message="one")], "s"),
            ("", [_tc("reply_user", "r2", message="two")], "s"),
            ("", [_tc("reply_user", "r3", message="three")], "s"),
            ("ok", [], "s"),
        ]
        await _run_chat(agent, script)
        assert agent._execute_tool.await_count == 3, "third attempt must still be executed"

    async def test_merged_replies_keep_image_and_reply_to(self):
        agent = _chat_agent()
        script = [
            ("", [
                _tc("reply_user", "r1", message="part one"),
                _tc("reply_user", "r2", message="part two", image_path="/tmp/c.png", reply_to_message_id=9),
            ], "s"),
            ("done", [], "s"),
        ]
        await _run_chat(agent, script)
        args = agent._execute_tool.await_args_list[0].args[1]
        assert args["image_path"] == "/tmp/c.png" and args["reply_to_message_id"] == 9
        assert "part one" in args["message"] and "part two" in args["message"]

    async def test_chat_cannot_execute_data_or_write_tools(self):
        agent = _chat_agent()
        seen: list = []
        script = [
            ("", [_tc("sql", "s1", query="DROP TABLE reports"), _tc("update_md", "u1", file="user.md")], "s"),
            ("", [_tc("reply_user", "r1", message="sorry")], "s"),
            ("ok", [], "s"),
        ]
        await _run_chat(agent, script, seen)
        executed = [c.args[0] for c in agent._execute_tool.await_args_list]
        assert executed == ["reply_user"]
        tool_msgs = [m for m in seen[1] if m.get("role") == "tool"]
        assert len(tool_msgs) == 2 and all("not available in this role" in m["content"] for m in tool_msgs)

    async def test_analysis_failure_is_reported_as_failure_to_the_orchestrator(self):
        agent = _chat_agent()
        agent._run_sub_analysis = AsyncMock(return_value={
            "success": False, "error": "provider down", "findings": "", "charts": [], "evidence": [],
        })
        seen: list = []
        script = [
            ("", [_tc("analyze", "a1", goal="x")], "s"),
            ("", [_tc("reply_user", "r1", message="I could not analyse that right now.")], "s"),
            ("ok", [], "s"),
        ]
        await _run_chat(agent, script, seen)
        tool_msg = next(m for m in seen[1] if m.get("role") == "tool")
        body = json.loads(tool_msg["content"])
        assert body["success"] is False and "Analysis failed" in body["error"]
        assert "tell the user" in body["error"]

    async def test_context_overflow_recovers_with_truncate_and_retry(self):
        agent = _chat_agent()
        agent._chat_histories["telegram:42"] = [
            {"role": "user", "content": f"[2026-04-01 10:0{i}] q{i}"} if i % 2 == 0
            else {"role": "assistant", "content": f"a{i}"}
            for i in range(8)
        ]
        seen: list = []
        script = [
            Exception("prompt is too long: 250000 tokens > 200000 maximum"),
            ("All good.", [], "s"),
        ]
        await _run_chat(agent, script, seen)
        assert len(seen) == 2, "chat must retry once after an overflow instead of apologising"
        assert len(seen[1]) < len(seen[0]), "the retry must send a smaller context"
        assert seen[1][1]["role"] == "user"
        agent._auto_reply.assert_awaited_once()
        assert agent._auto_reply.await_args.args[0] == "All good."

    async def test_leading_assistant_history_is_cleaned_before_the_llm(self):
        agent = _chat_agent()
        agent._chat_histories["telegram:42"] = [
            {"role": "assistant", "content": "proactive report"},
            {"role": "user", "content": "[2026-04-01 10:00] earlier"},
            {"role": "assistant", "content": "earlier answer"},
        ]
        seen: list = []
        await _run_chat(agent, [("fine", [], "s")], seen)
        roles = [m["role"] for m in seen[0]]
        assert roles[0] == "system" and roles[1] == "user"
        stored = agent._chat_histories["telegram:42"]
        assert stored[0]["role"] == "user"


# ---------------------------------------------------------------------------
# Sub-analysis engine + wrappers
# ---------------------------------------------------------------------------

def _sub_agent(tools=None):
    agent = MagicMock(spec=AgentLoopsMixin)
    agent.tool_registry = MagicMock()
    agent.tool_registry.get_tool = MagicMock(return_value=None)
    agent.context_window_size = 20
    agent.llm = MagicMock()
    agent._emit = AsyncMock()
    agent.build_sub_analysis_prompt = MagicMock(return_value="sys")
    agent._get_sub_analysis_tool_definitions = MagicMock(return_value=[])
    agent._chat_memory = MagicMock()
    agent._chat_memory.return_value.recent_user_text = MagicMock(return_value="")
    agent._run_sub_analysis = AgentLoopsMixin._run_sub_analysis.__get__(agent)
    return agent


async def _run_sub(agent, script, **kw):
    with patch.object(agent_loops, "_llm_call", _scripted_llm(script)):
        return await agent._run_sub_analysis("goal", **kw)


class TestSubAnalysis:
    async def test_llm_error_is_a_failure_not_findings(self):
        agent = _sub_agent()
        agent._execute_tool = AsyncMock()
        res = await _run_sub(agent, [RuntimeError("provider down")], source="analysis")
        assert res["success"] is False
        assert "provider down" in res["error"]
        assert res["findings"] == ""

    async def test_context_overflow_second_failure_is_still_a_failure(self):
        agent = _sub_agent()
        res = await _run_sub(agent, [Exception("prompt is too long"), Exception("prompt is too long")],
                             source="analysis")
        assert res["success"] is False

    async def test_allowed_tools_enforced_at_execution(self):
        agent = _sub_agent()
        agent._execute_tool = AsyncMock(return_value={"success": True, "markdown": "| a |"})
        script = [
            ("", [_tc("code", "c1", code="import os"), _tc("sql", "s1", query="SELECT 1")], "s"),
            ("findings", [], "s"),
        ]
        res = await _run_sub(agent, script, source="quick", allowed_tools={"sql"})
        executed = [c.args[0] for c in agent._execute_tool.await_args_list]
        assert executed == ["sql"]
        assert res["success"] is True and res["findings"] == "findings"

    async def test_write_tools_never_run_in_analysis(self):
        agent = _sub_agent()
        agent._execute_tool = AsyncMock(return_value={"success": True})
        script = [("", [_tc("update_md", "u1", file="user.md", content="x"),
                        _tc("create_page", "p1", page_id="x")], "s"), ("done", [], "s")]
        await _run_sub(agent, script, source="analysis")
        agent._execute_tool.assert_not_awaited()

    async def test_push_report_gets_same_turn_evidence_and_role_is_set(self):
        agent = _sub_agent()
        roles: list = []
        captured: dict = {}

        async def _exec(name, args, **kw):
            roles.append(current_tool_role())
            if name == "push_report":
                captured["evidence"] = kw.get("evidence_trail")
                return {"success": True, "report_id": 5}
            return {"success": True, "markdown": "| hr |\n| 60 |", "row_count": 1}

        agent._execute_tool = _exec
        script = [("", [_tc("sql", "s1", query="SELECT 1"),
                        _tc("push_report", "p1", title="t", content="c")], "s")]
        res = await _run_sub(agent, script, source="analysis", extra_tools={"push_report"})
        assert res["report_pushed"] is True
        assert [e["tool"] for e in captured["evidence"]] == ["sql"]
        assert roles == ["analysis", "analysis"]
        assert current_tool_role() is None, "role must be restored after the run"

    async def test_plan_source_gets_write_role(self):
        agent = _sub_agent()
        roles: list = []

        async def _exec(name, args, **kw):
            roles.append(current_tool_role())
            return {"success": True}

        agent._execute_tool = _exec
        await _run_sub(agent, [("", [_tc("sql", "s1", query="x")], "s"), ("ok", [], "s")], source="plan")
        assert roles == ["plan"]

    async def test_stop_cancels_the_loop_at_turn_boundary(self):
        agent = _sub_agent()
        token = CancellationToken()
        token.cancel("stop")
        agent._cancellation = token
        with pytest.raises(AgentCancelled):
            await _run_sub(agent, [("never", [], "s")], source="analysis")


def _wrapper_agent():
    agent = MagicMock(spec=AgentLoopsMixin)
    agent.cycle_count = 0
    agent.max_turns = 5
    agent.data_store = MagicMock()
    agent.data_store.query.return_value = pd.DataFrame({"ts": [None]})
    agent._set_state = MagicMock()
    agent._save_state = MagicMock()
    agent._emit = AsyncMock()
    agent._execute_tool = AsyncMock(return_value={"success": True, "report_id": 1})
    agent._chat_histories = {}
    agent._max_chat_history = 20
    agent.tool_registry = MagicMock()
    agent.tool_registry.get_tool = MagicMock(return_value=None)
    agent.cycle_messages = []
    return agent


class TestWrappers:
    async def test_failed_cron_analysis_is_not_published(self):
        agent = _wrapper_agent()
        agent._run_sub_analysis = AsyncMock(return_value={
            "success": False, "error": "provider down", "findings": "", "charts": [], "evidence": [],
        })
        await AgentLoopsMixin._run_one_shot_analysis(agent, "daily sleep")
        agent._execute_tool.assert_not_awaited()
        events = [c.args[0] for c in agent._emit.await_args_list]
        assert any(e.get("type") == "error" and "provider down" in e["error"] for e in events)
        assert not any(e.get("type") == "report_pushed" for e in events)
        assert any(e.get("type") == "cycle_end" for e in events)

    async def test_cron_exception_is_not_published_either(self):
        agent = _wrapper_agent()
        agent._run_sub_analysis = AsyncMock(side_effect=RuntimeError("boom"))
        await AgentLoopsMixin._run_one_shot_analysis(agent, "g")
        agent._execute_tool.assert_not_awaited()

    async def test_successful_cron_without_push_falls_back_to_programmatic_push(self):
        agent = _wrapper_agent()
        agent._run_sub_analysis = AsyncMock(return_value=run_sub_result(findings="Sleep was fine."))
        await AgentLoopsMixin._run_one_shot_analysis(agent, "g")
        assert agent._execute_tool.await_args.args[0] == "push_report"
        assert agent._execute_tool.await_args.args[1]["content"] == "Sleep was fine."

    async def test_cron_runs_are_independent_of_each_other(self):
        """No shared 'already pushed' flag: back-to-back runs both publish."""
        agent = _wrapper_agent()
        agent._run_sub_analysis = AsyncMock(return_value=run_sub_result(findings="ok"))
        await AgentLoopsMixin._run_one_shot_analysis(agent, "g1")
        await AgentLoopsMixin._run_one_shot_analysis(agent, "g2")
        assert agent._execute_tool.await_count == 2
        assert not hasattr(AutonomousHealthAgent, "pushed_report_in_cycle")

    async def test_cron_analysis_has_an_overall_timeout(self):
        agent = _wrapper_agent()

        async def _hang(*a, **k):
            await asyncio.sleep(30)

        agent._run_sub_analysis = _hang
        with patch.object(agent_loops, "ONE_SHOT_ANALYSIS_TIMEOUT_S", 0.05):
            await AgentLoopsMixin._run_one_shot_analysis(agent, "g")
        agent._execute_tool.assert_not_awaited()
        events = [c.args[0] for c in agent._emit.await_args_list]
        assert any(e.get("type") == "error" and "timed out" in e["error"] for e in events)

    async def test_plan_designer_failure_keeps_survey_pending(self):
        agent = _wrapper_agent()
        mem = MagicMock()
        agent._chat_memory = MagicMock(return_value=mem)
        agent.user_id = "LiveUser"
        agent.build_plan_designer_prompt = MagicMock(return_value="p")
        agent._run_sub_analysis = AsyncMock(return_value={
            "success": False, "error": "x", "findings": "", "charts": [], "evidence": [],
        })
        await AgentLoopsMixin._run_plan_designer(agent, {"id": 3, "goals": ["sleep"], "answers": {}})
        mem.mark_survey_planned.assert_not_called()

        agent._run_sub_analysis = AsyncMock(return_value=run_sub_result(report_pushed=True))
        await AgentLoopsMixin._run_plan_designer(agent, {"id": 3, "goals": ["sleep"], "answers": {}})
        mem.mark_survey_planned.assert_called_once_with(3)

    async def test_quick_failure_returns_neutral_friendly_state_and_no_push(self):
        agent = _wrapper_agent()
        agent._run_sub_analysis = AsyncMock(return_value={
            "success": False, "error": "RuntimeError: secret stack", "findings": "", "charts": [], "evidence": [],
        })
        agent._run_quick_analysis_inner = AgentLoopsMixin._run_quick_analysis_inner.__get__(agent)
        with patch("backend.agent.prompt_loader.load_prompt", return_value="goal"):
            out = await AgentLoopsMixin.run_quick_analysis(agent)
        assert out["state"] == "neutral"
        assert "secret" not in out["message"] and "Analysis error" not in out["message"]
        agent._execute_tool.assert_not_awaited()

    async def test_quick_runs_are_serialised(self):
        agent = _wrapper_agent()
        active = 0
        peak = 0

        async def _inner():
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.02)
            active -= 1
            return {"state": "relaxed", "message": "ok"}

        agent._run_quick_analysis_inner = _inner
        await asyncio.gather(*[AgentLoopsMixin.run_quick_analysis(agent) for _ in range(3)])
        assert peak == 1


# ---------------------------------------------------------------------------
# AutonomousHealthAgent: state I/O, dedupe, envelope isolation
# ---------------------------------------------------------------------------

def _bare_agent():
    a = AutonomousHealthAgent.__new__(AutonomousHealthAgent)
    a.user_id = "u"
    a.cycle_count = 0
    a.last_sleep_time = 0
    a.last_data_check_time = None
    a.cycle_messages = []
    a.current_simulation_timestamp = None
    a.state = "idle"
    a.state_start_time = 0
    a.state_metadata = {}
    a._chat_histories = {}
    a.cumulative_tokens = {}
    a._state_dirty = False
    a._state_flush_task = None
    a._STATE_FLUSH_DELAY_S = 0.01
    a._recent_scheduled_goals = {}
    a._scheduled_dedupe_window_s = 120.0
    a._analysis_queue = asyncio.Queue(maxsize=50)
    return a


class TestAgentState:
    async def test_state_writes_are_coalesced_and_off_loop(self):
        a = _bare_agent()
        writes: list[str] = []
        a.state_repo = SimpleNamespace(save_state_json=lambda uid, txt: writes.append(txt))
        for i in range(30):
            a.cycle_count = i
            a._save_state()
        await asyncio.sleep(0.15)
        assert 1 <= len(writes) <= 3
        assert json.loads(writes[-1])["cycle_count"] == 29

    def test_sync_context_writes_immediately(self):
        a = _bare_agent()
        writes: list[str] = []
        a.state_repo = SimpleNamespace(save_state_json=lambda uid, txt: writes.append(txt))
        a._save_state()
        assert len(writes) == 1
        assert "pushed_report_in_cycle" not in json.loads(writes[0])

    async def test_scheduled_dedupe_by_task_id(self):
        a = _bare_agent()
        await a.run_scheduled_analysis("same goal", task_id=1)
        await a.run_scheduled_analysis("same goal", task_id=2)   # different task, same text
        await a.run_scheduled_analysis("same goal", task_id=1)   # true duplicate
        assert a._analysis_queue.qsize() == 2
        b = _bare_agent()
        await b.run_scheduled_analysis("g")
        await b.run_scheduled_analysis("g")                      # no id: text dedupe as before
        assert b._analysis_queue.qsize() == 1


# ---------------------------------------------------------------------------
# Trigger evaluator
# ---------------------------------------------------------------------------

@pytest.fixture
def trig(tmp_path):
    from backend.agent.memory_manager import _ensure_schema
    from backend.agent.trigger_evaluator import TriggerEvaluator

    mem_dir = tmp_path / "mem"
    mem_dir.mkdir()
    health = tmp_path / "health.db"
    with sqlite3.connect(health) as c:
        c.execute("CREATE TABLE samples (timestamp TEXT, feature_type TEXT, value REAL)")
    with sqlite3.connect(mem_dir / "u.db") as c:
        _ensure_schema(c)
    ev = TriggerEvaluator(mem_dir, "u", health)
    ev._min_eval_interval = 0.0

    def add_rule(name, feature, cond, thr, window=60, cooldown=30):
        with sqlite3.connect(mem_dir / "u.db") as c:
            c.execute(
                "INSERT INTO trigger_rules (name, feature_type, condition, threshold, window_minutes, "
                "cooldown_minutes, prompt_goal) VALUES (?,?,?,?,?,?,?)",
                (name, feature, cond, thr, window, cooldown, f"goal-{name}"),
            )

    def add_sample(feature, value, minutes_ago=0):
        ts = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%S")
        with sqlite3.connect(health) as c:
            c.execute("INSERT INTO samples VALUES (?,?,?)", (ts, feature, value))

    return SimpleNamespace(ev=ev, add_rule=add_rule, add_sample=add_sample)


class TestTriggerEvaluator:
    async def test_absent_rule_fires_from_periodic_sweep_only(self, trig):
        trig.add_rule("no-hr", "heart_rate", "absent", 0, window=30)
        trig.add_sample("heart_rate", 60, minutes_ago=120)   # old data only
        q: asyncio.Queue = asyncio.Queue()
        assert await trig.ev.evaluate_after_ingest(q, {"heart_rate"}) == []
        fired = await trig.ev.evaluate_periodic(q)
        assert [r["name"] for r in fired] == ["no-hr"]
        assert q.get_nowait() == "[Triggered: no-hr] goal-no-hr"

    async def test_absent_rule_quiet_while_data_flows(self, trig):
        trig.add_rule("no-hr", "heart_rate", "absent", 0, window=30)
        trig.add_sample("heart_rate", 60, minutes_ago=1)
        assert await trig.ev.evaluate_periodic(asyncio.Queue()) == []

    async def test_throttled_batch_is_not_lost(self, trig):
        trig.add_rule("hi-hr", "heart_rate", "gt", 100)
        trig.add_rule("lo-spo2", "blood_oxygen", "lt", 0.9)
        trig.add_sample("heart_rate", 150)
        trig.add_sample("blood_oxygen", 0.8)
        trig.ev._min_eval_interval = 3600.0
        trig.ev._last_eval_time = __import__("time").monotonic()   # inside the throttle window
        assert await trig.ev.evaluate_after_ingest(None, {"heart_rate"}) == []
        # next batch only mentions SpO2 — but the skipped HR batch must be evaluated too
        trig.ev._last_eval_time = 0.0
        fired = await trig.ev.evaluate_after_ingest(None, {"blood_oxygen"})
        assert {r["name"] for r in fired} == {"hi-hr", "lo-spo2"}

    async def test_periodic_sweep_flushes_pending_features(self, trig):
        trig.add_rule("hi-hr", "heart_rate", "gt", 100)
        trig.add_sample("heart_rate", 150)
        trig.ev._last_eval_time = __import__("time").monotonic()
        trig.ev._min_eval_interval = 3600.0
        await trig.ev.evaluate_after_ingest(None, {"heart_rate"})
        fired = await trig.ev.evaluate_periodic(None)
        assert [r["name"] for r in fired] == ["hi-hr"]

    async def test_stale_sample_does_not_fire_simple_rule(self, trig):
        trig.add_rule("hi-hr", "heart_rate", "gt", 100, window=60)
        trig.add_sample("heart_rate", 150, minutes_ago=600)
        assert await trig.ev.evaluate_after_ingest(None, {"heart_rate"}) == []
        trig.add_sample("heart_rate", 150, minutes_ago=1)
        assert len(await trig.ev.evaluate_after_ingest(None, {"heart_rate"})) == 1


# ---------------------------------------------------------------------------
# Cancellation helper
# ---------------------------------------------------------------------------

def test_check_cancelled_is_tolerant_of_agents_without_a_token():
    agent_loops._check_cancelled(object())
    tok = CancellationToken()
    agent_loops._check_cancelled(SimpleNamespace(_cancellation=tok))
    tok.cancel("x")
    with pytest.raises(AgentCancelled):
        agent_loops._check_cancelled(SimpleNamespace(_cancellation=tok))
