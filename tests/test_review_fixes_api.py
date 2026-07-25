"""
Regression tests for the backend API / service review fixes.

Each test pins a specific bug that was found by code review:

- H1  legacy telegram default chat id read the wrong (lowercase) Settings field
- H2  the live data-stream loop never noticed a WebSocket disconnect
- H3  /chat shared a 5-per-minute rate bucket with /start and /stop
- M4  inspect_memory_table turned its own 400 into a 500
- M7  the rate limiter trusted an unvalidated X-Forwarded-For header
- M11 a blanket string replace corrupted legitimate data in /api/data/inspect
- LOW _build_markdown produced invalid YAML for multi-line descriptions
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from backend.api import agent_state


@pytest.fixture(autouse=True)
def _clear_rate_buckets():
    agent_state._rate_buckets.clear()
    yield
    agent_state._rate_buckets.clear()


# =========================================================================
# H1 — Settings.CHAT_ID is case-sensitive
# =========================================================================

class TestChatIdAttributeCase:
    def test_settings_has_no_lowercase_alias(self):
        """``settings.chat_id`` does not exist — only ``CHAT_ID`` does."""
        from backend.config import settings
        assert hasattr(settings, "CHAT_ID")
        assert not hasattr(settings, "chat_id")

    def test_lifecycle_reads_the_uppercase_field(self):
        """The lifecycle module must not reach for the lowercase spelling."""
        from pathlib import Path
        src = (Path(__file__).parent.parent / "backend" / "api"
               / "agent_lifecycle.py").read_text(encoding="utf-8")
        assert 'getattr(settings, "chat_id"' not in src
        assert "default_chat_id = settings.CHAT_ID" in src


# =========================================================================
# H3 / M7 — rate limiting
# =========================================================================

class TestRateLimiting:
    def test_buckets_are_per_endpoint(self):
        """Exhausting /start must not lock the caller out of /chat."""
        for _ in range(agent_state._RATE_LIMIT_MAX_CALLS):
            agent_state._check_rate_limit("1.2.3.4", "start")
        with pytest.raises(Exception) as exc_info:
            agent_state._check_rate_limit("1.2.3.4", "start")
        assert exc_info.value.status_code == 429

        # A different endpoint keeps its own budget.
        agent_state._check_rate_limit("1.2.3.4", "stop")
        agent_state._check_rate_limit(
            "1.2.3.4", "chat", agent_state._RATE_LIMIT_CHAT_MAX_CALLS
        )

    def test_chat_budget_is_larger_than_lifecycle(self):
        """A short burst of chat messages must not 429."""
        assert (agent_state._RATE_LIMIT_CHAT_MAX_CALLS
                > agent_state._RATE_LIMIT_MAX_CALLS)
        for _ in range(agent_state._RATE_LIMIT_MAX_CALLS + 5):
            agent_state._check_rate_limit(
                "1.2.3.4", "chat", agent_state._RATE_LIMIT_CHAT_MAX_CALLS
            )

    def test_buckets_are_per_ip(self):
        for _ in range(agent_state._RATE_LIMIT_MAX_CALLS):
            agent_state._check_rate_limit("1.2.3.4", "start")
        agent_state._check_rate_limit("5.6.7.8", "start")  # different IP, fine

    def _request(self, forwarded: str | None, peer: str = "10.0.0.1"):
        req = MagicMock()
        req.headers = {"X-Forwarded-For": forwarded} if forwarded else {}
        req.client = MagicMock(host=peer)
        return req

    def test_xff_ignored_by_default(self):
        """Untrusted X-Forwarded-For must not become the limiter key."""
        req = self._request("9.9.9.9")
        assert agent_state._client_ip(req) == "10.0.0.1"

    def test_xff_honoured_when_proxy_trusted(self, monkeypatch):
        monkeypatch.setattr(agent_state.settings, "TRUST_PROXY_HEADERS", True)
        req = self._request("9.9.9.9, 10.0.0.2")
        assert agent_state._client_ip(req) == "9.9.9.9"

    def test_forged_xff_cannot_bypass_the_limit(self):
        """Rotating the header per request must not mint a fresh bucket."""
        for i in range(agent_state._RATE_LIMIT_MAX_CALLS):
            ip = agent_state._client_ip(self._request(f"1.1.1.{i}"))
            agent_state._check_rate_limit(ip, "start")
        ip = agent_state._client_ip(self._request("1.1.1.99"))
        with pytest.raises(Exception) as exc_info:
            agent_state._check_rate_limit(ip, "start")
        assert exc_info.value.status_code == 429

    def test_stale_buckets_are_evicted(self):
        """The bucket dict must not grow without bound."""
        limit = agent_state._RATE_BUCKETS_MAX_KEYS
        for i in range(limit + 100):
            agent_state._rate_buckets[(f"ip-{i}", "start")] = []
        agent_state._evict_stale_buckets(agent_state.time.monotonic())
        assert len(agent_state._rate_buckets) <= limit


# =========================================================================
# H2 — the data-stream loop must exit on WebSocket disconnect
# =========================================================================

class TestStreamDisconnectDetection:
    def _reader(self):
        reader = MagicMock()
        reader.get_feature_types.return_value = ["heart_rate"]
        reader.load_feature_data.return_value = pd.DataFrame()
        reader.load_features_batch.return_value = pd.DataFrame()
        return reader

    async def test_loop_exits_when_client_disconnects_while_idle(self):
        """No data to send + client gone → the poll loop must still stop.

        Previously the loop only noticed a disconnect when ``send_json``
        raised, so a quiet connection polled SQLite forever and leaked a
        worker from the shared executor.
        """
        from backend.services.streaming_service import DataStreamingService

        ws = AsyncMock()
        ws.receive = AsyncMock(return_value={"type": "websocket.disconnect"})
        active = {ws}  # never cleaned up — mirrors the real route handler

        state = {"stream_config": {"granularity": "real-time"},
                 "live_history_window": "1hour"}

        with patch("backend.services.streaming_service.create_reader",
                   return_value=self._reader()), \
             patch("backend.services.streaming_service.settings",
                   MagicMock(DATA_STORE_PATH="/tmp/test")), \
             patch("backend.api.config_routes.get_app_state", return_value=state):
            await asyncio.wait_for(
                DataStreamingService.stream_data(
                    ws, ["LiveUser"], {"granularity": "real-time"}, active
                ),
                timeout=5.0,
            )

        # The websocket is still in active_connections — the loop exited purely
        # because the disconnect frame was observed.
        assert ws in active

    async def test_loop_exits_when_receive_raises(self):
        """A transport-level failure on receive() also ends the loop."""
        from backend.services.streaming_service import DataStreamingService

        ws = AsyncMock()
        ws.receive = AsyncMock(side_effect=RuntimeError("socket is closed"))
        active = {ws}

        state = {"stream_config": {"granularity": "real-time"},
                 "live_history_window": "1hour"}

        with patch("backend.services.streaming_service.create_reader",
                   return_value=self._reader()), \
             patch("backend.services.streaming_service.settings",
                   MagicMock(DATA_STORE_PATH="/tmp/test")), \
             patch("backend.api.config_routes.get_app_state", return_value=state):
            await asyncio.wait_for(
                DataStreamingService.stream_data(
                    ws, ["LiveUser"], {"granularity": "real-time"}, active
                ),
                timeout=5.0,
            )

    def test_client_gone_classifier(self):
        from backend.services.streaming_service import _client_gone

        done = MagicMock()
        done.result.return_value = {"type": "websocket.disconnect"}
        assert _client_gone(done) is True

        chatty = MagicMock()
        chatty.result.return_value = {"type": "websocket.receive", "text": "hi"}
        assert _client_gone(chatty) is False

        broken = MagicMock()
        broken.result.side_effect = RuntimeError("closed")
        assert _client_gone(broken) is True


# =========================================================================
# M4 — inspect_memory_table must not turn its own 400 into a 500
# =========================================================================

class TestInspectMemoryTable:
    async def test_unknown_table_returns_400(self, test_client, memory_db):
        resp = await test_client.get(
            "/api/agent/memory/LiveUser/inspect", params={"table_name": "no_such_table"}
        )
        assert resp.status_code == 400
        assert "not found" in resp.json()["detail"]

    async def test_known_table_returns_rows(self, test_client, memory_db):
        resp = await test_client.get(
            "/api/agent/memory/LiveUser/inspect", params={"table_name": "reports"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["table_name"] == "reports"


# =========================================================================
# M11 — /api/data/inspect must not rewrite legitimate strings
# =========================================================================

class TestInspectDataSerialisation:
    async def test_nan_substring_in_values_is_preserved(self, test_client):
        """A device literally named "NaNoWatch" must survive the round-trip."""
        df = pd.DataFrame([
            {"date": "2026-01-01T00:00:00", "value": 1.0,
             "feature_type": "heart_rate", "device": "NaNoWatch Infinity"},
        ])
        reader = MagicMock()
        reader.load_feature_data.return_value = df

        with patch("backend.api.data_routes._ensure_reader", return_value=reader):
            resp = await test_client.get("/api/data/inspect/LiveUser")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"][0]["device"] == "NaNoWatch Infinity"

    async def test_non_finite_floats_still_serialise(self, test_client):
        """dataframe_to_json_safe already maps NaN/Inf to null."""
        df = pd.DataFrame([
            {"date": "2026-01-01T00:00:00", "value": float("nan"),
             "feature_type": "heart_rate"},
            {"date": "2026-01-01T00:01:00", "value": float("inf"),
             "feature_type": "heart_rate"},
        ])
        reader = MagicMock()
        reader.load_feature_data.return_value = df

        with patch("backend.api.data_routes._ensure_reader", return_value=reader):
            resp = await test_client.get("/api/data/inspect/LiveUser")

        assert resp.status_code == 200
        # Valid JSON with real nulls, not the literal token NaN.
        body = json.loads(resp.text)
        assert body["data"][0]["value"] is None
        assert body["data"][1]["value"] is None


# =========================================================================
# LOW — skill frontmatter must stay valid YAML
# =========================================================================

class TestSkillFrontmatter:
    def test_newlines_in_description_do_not_break_frontmatter(self):
        from backend.agent.skills.loader import parse_frontmatter
        from backend.api.skill_routes import _build_markdown

        md = _build_markdown(
            'first line\ninjected: "evil"\nmore', "# Body\n"
        )
        meta, body = parse_frontmatter(md)
        assert "injected" not in meta
        assert "\n" not in meta["description"]
        assert body.strip() == "# Body"

    def test_quotes_and_backslashes_round_trip(self):
        from backend.agent.skills.loader import parse_frontmatter
        from backend.api.skill_routes import _build_markdown

        desc = r'say "hi" and use C:\path'
        meta, _body = parse_frontmatter(_build_markdown(desc, "body"))
        assert meta["description"] == desc
