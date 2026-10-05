"""Freshness gate for scheduled analysis runs."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from backend.agent.agent_loops import AgentLoopsMixin
from backend.agent.autonomous_agent import AutonomousHealthAgent
from backend.agent.scheduled_freshness import ScheduledGoal, build_run_context
from backend.config import settings


def _iso(minutes_ago: float | None):
    if minutes_ago is None:
        return None
    dt = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


class _Clock:
    """Mutable 'age of newest sample' for the fake data store."""

    def __init__(self, minutes_ago):
        self.minutes_ago = minutes_ago

    def query(self, sql):
        return pd.DataFrame({"ts": [_iso(self.minutes_ago)]})


def _agent(age):
    a = AutonomousHealthAgent.__new__(AutonomousHealthAgent)
    a.user_id = "u"
    a._recent_scheduled_goals = {}
    a._scheduled_dedupe_window_s = 120.0
    a._analysis_queue = asyncio.Queue(maxsize=50)
    a._deferred_scheduled = []
    a.clock = _Clock(age)
    a.data_store = a.clock
    a._emit = AsyncMock()
    return a


@pytest.fixture(autouse=True)
def _cfg(monkeypatch):
    monkeypatch.setattr(settings, "SCHEDULED_FRESHNESS_MAX_AGE_MIN", 45)
    monkeypatch.setattr(settings, "SCHEDULED_FRESHNESS_RECHECK_MIN", 10)
    monkeypatch.setattr(settings, "SCHEDULED_FRESHNESS_MAX_WAIT_MIN", 120)


def _types(a):
    return [c.args[0]["type"] for c in a._emit.await_args_list]


class TestGate:
    async def test_fresh_runs_immediately(self):
        a = _agent(5)
        await a.run_scheduled_analysis("g", task_id=1)
        assert a._analysis_queue.qsize() == 1
        assert not a._deferred_scheduled
        assert isinstance(a._analysis_queue.get_nowait(), ScheduledGoal)

    async def test_stale_defers_then_ingest_releases(self):
        a = _agent(600)
        await a.run_scheduled_analysis("g", task_id=1)
        await a.run_scheduled_analysis("h", task_id=2)
        assert a._analysis_queue.qsize() == 0
        assert len(a._deferred_scheduled) == 2
        assert _types(a) == ["analysis_deferred", "analysis_deferred"]
        # Ingest hook while still stale: nothing released
        a.request_deferred_recheck()
        assert await a.poll_deferred_scheduled() == 0
        # Fresh batch lands
        a.clock.minutes_ago = 1
        a.request_deferred_recheck()
        assert await a.poll_deferred_scheduled() == 2
        assert a._analysis_queue.qsize() == 2
        assert not a._deferred_scheduled
        released = [
            c.args[0] for c in a._emit.await_args_list
            if c.args[0]["type"] == "analysis_released"
        ]
        assert {r["reason"] for r in released} == {"fresh_data"}
        assert not a._analysis_queue.get_nowait().deadline_passed

    async def test_duplicate_cron_fire_does_not_duplicate(self):
        a = _agent(600)
        await a.run_scheduled_analysis("g", task_id=1)
        a._recent_scheduled_goals.clear()  # past the dedupe window
        await a.run_scheduled_analysis("g", task_id=1)
        assert len(a._deferred_scheduled) == 1

    async def test_stale_runs_at_deadline_with_flag(self):
        a = _agent(600)
        await a.run_scheduled_analysis("g", task_id=1)
        a._deferred_scheduled[0]["deadline"] = time.monotonic() - 1
        assert await a.poll_deferred_scheduled() == 1
        item = a._analysis_queue.get_nowait()
        assert item.deadline_passed
        assert _types(a)[-1] == "analysis_released"
        assert a._emit.await_args_list[-1].args[0]["reason"] == "deadline"

    async def test_periodic_recheck_without_ingest_hook(self):
        a = _agent(600)
        await a.run_scheduled_analysis("g", task_id=1)
        a.clock.minutes_ago = 2
        assert await a.poll_deferred_scheduled() == 0  # not due yet
        a._deferred_scheduled[0]["next_check"] = time.monotonic() - 1
        assert await a.poll_deferred_scheduled() == 1

    async def test_disabled_keeps_old_behaviour(self, monkeypatch):
        monkeypatch.setattr(settings, "SCHEDULED_FRESHNESS_MAX_AGE_MIN", 0)
        a = _agent(600)
        await a.run_scheduled_analysis("g", task_id=1)
        assert a._analysis_queue.qsize() == 1 and not a._deferred_scheduled

    async def test_no_data_does_not_defer(self):
        a = _agent(None)
        await a.run_scheduled_analysis("g", task_id=1)
        assert a._analysis_queue.qsize() == 1 and not a._deferred_scheduled

    async def test_manual_trigger_bypasses_gate(self):
        a = _agent(600)
        await a.run_scheduled_analysis("g", gate=False)
        assert a._analysis_queue.qsize() == 1

    async def test_stop_cancels_deferred(self):
        a = _agent(600)
        await a.run_scheduled_analysis("g", task_id=1)
        a.cancel_deferred_scheduled()
        assert not a._deferred_scheduled
        assert await a.poll_deferred_scheduled() == 0


def _wrapper(age_min):
    agent = MagicMock(spec=AgentLoopsMixin)
    agent.cycle_count = 0
    agent.max_turns = 5
    agent.data_store = MagicMock()
    agent.data_store.query.return_value = pd.DataFrame({"ts": [_iso(age_min)]})
    agent._set_state = MagicMock()
    agent._save_state = MagicMock()
    agent._emit = AsyncMock()
    agent._execute_tool = AsyncMock(return_value={"success": True, "report_id": 1})
    agent._chat_histories = {}
    agent.tool_registry = MagicMock()
    agent.tool_registry.get_tool = MagicMock(return_value=None)
    agent.cycle_messages = []
    agent._run_sub_analysis = AsyncMock(return_value={
        "success": True, "findings": "ok", "charts": [], "evidence": [],
    })
    return agent


class TestRunContext:
    async def test_fresh_scheduled_run_gets_context_line(self):
        agent = _wrapper(5)
        await AgentLoopsMixin._run_one_shot_analysis(agent, ScheduledGoal("daily"))
        sent = agent._run_sub_analysis.await_args.args[0]
        assert sent.startswith("[Run context] Now: ")
        assert "Latest synced data:" in sent and sent.endswith("daily")
        assert "may not have synced" not in sent

    async def test_deadline_with_stale_data_adds_note(self):
        agent = _wrapper(600)
        g = ScheduledGoal("daily")
        g.deadline_passed, g.waited_min = True, 120
        await AgentLoopsMixin._run_one_shot_analysis(agent, g)
        sent = agent._run_sub_analysis.await_args.args[0]
        assert "may not have synced" in sent and "10.0 h ago" in sent

    async def test_trigger_goals_are_untouched(self):
        agent = _wrapper(5)
        await AgentLoopsMixin._run_one_shot_analysis(agent, "event goal")
        assert agent._run_sub_analysis.await_args.args[0] == "event goal"

    def test_context_without_data(self):
        assert "No synced data" in build_run_context(None)
