"""
Regression tests for 7 real issues found in user behavior logs.

Each test class corresponds to a specific bug and can run standalone
without starting any services.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ===========================================================================
# Test 1: df rowid incremental refresh must not accumulate
# ===========================================================================

class TestDfIncrementalRefresh:
    """
    Reproduction: df grew from 21K rows to 63K rows today (each refresh appended ~979 rows).
    Root cause: incremental refresh did not dedupe properly, so df row count kept accumulating.
    """

    @pytest.fixture
    def step_count_db(self, tmp_dirs) -> Path:
        """Create a health DB containing only step_count data, for the no-double-count test."""
        db_file = tmp_dirs["data_stores"] / "LiveUser_data.db"
        now = datetime.now(timezone.utc)
        with sqlite3.connect(db_file) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS samples (
                    timestamp TEXT NOT NULL,
                    feature_type TEXT NOT NULL,
                    value REAL,
                    metadata TEXT,
                    ingested_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
                    PRIMARY KEY (timestamp, feature_type)
                )
            """)
            # Insert 10 step_count rows, 700 steps each, totalling 7000
            for i in range(10):
                ts = (now - timedelta(minutes=i * 10)).strftime('%Y-%m-%dT%H:%M:%S')
                conn.execute(
                    "INSERT OR REPLACE INTO samples (timestamp, feature_type, value) VALUES (?, ?, ?)",
                    (ts, "step_count", 700.0),
                )
            conn.commit()
        return db_file

    @pytest.fixture
    def code_tool_from_db(self, tmp_dirs, step_count_db, memory_db):
        """Create a CodeTool instance backed by the step-count DB."""
        from backend.agent.data_store import DataStore
        from backend.agent.tools.code_tool import CodeTool

        ds = DataStore(db_path=tmp_dirs["data_stores"], user_id="LiveUser")
        return CodeTool(
            data_store=ds,
            memory_db_path=tmp_dirs["memory"],
            user_id="LiveUser",
        )

    @pytest.fixture
    def known_count_db(self, tmp_dirs) -> Path:
        """Create a health DB with an exact row count: 100 rows, within 14 days, fixed feature."""
        db_file = tmp_dirs["data_stores"] / "LiveUser_data.db"
        now = datetime.now(timezone.utc)
        with sqlite3.connect(db_file) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS samples (
                    timestamp TEXT NOT NULL,
                    feature_type TEXT NOT NULL,
                    value REAL,
                    metadata TEXT,
                    ingested_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
                    PRIMARY KEY (timestamp, feature_type)
                )
            """)
            for i in range(100):
                ts = (now - timedelta(minutes=i * 5)).strftime('%Y-%m-%dT%H:%M:%S')
                conn.execute(
                    "INSERT OR REPLACE INTO samples (timestamp, feature_type, value) VALUES (?, ?, ?)",
                    (ts, "heart_rate", 70.0 + i),
                )
            conn.commit()
        return db_file

    @pytest.fixture
    def code_tool_100rows(self, tmp_dirs, known_count_db, memory_db):
        """Create a CodeTool backed by the 100-row DB."""
        from backend.agent.data_store import DataStore
        from backend.agent.tools.code_tool import CodeTool

        ds = DataStore(db_path=tmp_dirs["data_stores"], user_id="LiveUser")
        return CodeTool(
            data_store=ds,
            memory_db_path=tmp_dirs["memory"],
            user_id="LiveUser",
        )

    def test_incremental_does_not_accumulate(self, code_tool_100rows):
        """
        After 10 refresh_df calls, df row count should equal the unique
        (timestamp, feature_type) rows in the DB within the 14-day window,
        not 10x the original row count.
        Reproduction: when refresh has no new data, df row count should stay constant.
        """
        tool = code_tool_100rows

        # First full load
        tool.refresh_df(days=14)
        first_shape = tool._shell.user_ns["df"].shape[0]
        assert first_shape == 100, (
            f"Expected df to have 100 rows (the exact number inserted into DB), got {first_shape}"
        )

        # Call 9 more times with no new data
        for _i in range(9):
            tool.refresh_df(days=14)

        final_shape = tool._shell.user_ns["df"].shape[0]
        assert final_shape == first_shape, (
            f"df row count accumulated after 10 refresh_df calls: expected {first_shape}, got {final_shape}. "
            f"The rowid-based incremental logic is duplicating rows."
        )

    def test_incremental_captures_new_rows(self, tmp_dirs, memory_db):
        """
        Insert 50 rows first, run full refresh, then insert 20 more new records;
        after incremental refresh df should have 70 rows (not 50+50+20=120).
        """
        db_file = tmp_dirs["data_stores"] / "LiveUser_data.db"
        now = datetime.now(timezone.utc)

        with sqlite3.connect(db_file) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS samples (
                    timestamp TEXT NOT NULL,
                    feature_type TEXT NOT NULL,
                    value REAL,
                    metadata TEXT,
                    ingested_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
                    PRIMARY KEY (timestamp, feature_type)
                )
            """)
            # Insert initial 50 rows (earlier in time)
            for i in range(50):
                ts = (now - timedelta(hours=12, minutes=i)).strftime('%Y-%m-%dT%H:%M:%S')
                conn.execute(
                    "INSERT OR REPLACE INTO samples (timestamp, feature_type, value) VALUES (?, ?, ?)",
                    (ts, "heart_rate", 65.0 + i),
                )
            conn.commit()

        from backend.agent.data_store import DataStore
        from backend.agent.tools.code_tool import CodeTool

        ds = DataStore(db_path=tmp_dirs["data_stores"], user_id="LiveUser")
        tool = CodeTool(
            data_store=ds,
            memory_db_path=tmp_dirs["memory"],
            user_id="LiveUser",
        )

        # Full refresh
        tool.refresh_df(days=14)
        shape_after_full = tool._shell.user_ns["df"].shape[0]
        assert shape_after_full == 50, f"After full refresh should have 50 rows, got {shape_after_full}"

        # Insert 20 more new records (more recent time)
        with sqlite3.connect(db_file) as conn:
            for i in range(20):
                ts = (now - timedelta(minutes=i)).strftime('%Y-%m-%dT%H:%M:%S')
                conn.execute(
                    "INSERT OR REPLACE INTO samples (timestamp, feature_type, value) VALUES (?, ?, ?)",
                    (ts, "heart_rate", 80.0 + i),
                )
            conn.commit()

        # Incremental refresh should append only the 20 new rows
        tool.refresh_df(days=14)
        shape_after_incr = tool._shell.user_ns["df"].shape[0]
        assert shape_after_incr == 70, (
            f"After incremental refresh should have 70 rows (50+20), got {shape_after_incr}. "
            f"Possible duplicate append (expected 70; 120 would indicate the bug)."
        )

    def test_step_count_not_doubled(self, code_tool_from_db):
        """
        Reproduction: "today's step report says 150K steps".
        DB has 10 step_count rows, each with value 700, so sum should be 7000.
        After 10 refresh_df calls, sum should still be ~7000, not 70000.
        """
        tool = code_tool_from_db

        # Full refresh
        tool.refresh_df(days=14)

        # Run incremental refresh 9 more times (no new data)
        for _ in range(9):
            tool.refresh_df(days=14)

        import pandas as pd
        df: pd.DataFrame = tool._shell.user_ns["df"]
        step_df = df[df["feature_type"] == "step_count"]
        total_steps = step_df["value"].sum()

        assert abs(total_steps - 7000.0) < 0.01, (
            f"Expected total steps ~7000 (10x700), got {total_steps}. "
            f"If it's 70000, every refresh is re-appending the same data."
        )


# ===========================================================================
# Test 2: WebSocket disconnect must not produce ERROR logs
# ===========================================================================

class TestWebSocketDisconnect:
    """
    Reproduction: after client disconnects, backend's send_json raises
    WebSocketDisconnect and an ERROR-level log appears, polluting monitoring alerts.
    """

    async def test_disconnect_does_not_log_error(self, caplog):
        """
        After client disconnect, stream_data should exit silently (break the loop)
        without emitting ERROR-level streaming logs.
        """
        pytest.importorskip("starlette")

        ws = AsyncMock()
        active = {ws}

        # First send_json succeeds (sends stream_start)
        # Second send_json (history batch) raises WebSocketDisconnect
        call_count = 0

        async def _send_json_side_effect(msg):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                active.discard(ws)  # Remove from active set to trigger loop exit

        ws.send_json = _send_json_side_effect

        mock_reader = MagicMock()
        mock_reader.get_feature_types.return_value = ["heart_rate"]
        import pandas as pd
        mock_reader.load_features_batch.return_value = pd.DataFrame()
        mock_reader.load_feature_data.return_value = pd.DataFrame()

        mock_state = {"live_history_window": "1hour"}

        with caplog.at_level(logging.ERROR, logger="backend.services.streaming_service"), \
             patch("backend.services.streaming_service.create_reader", return_value=mock_reader), \
             patch("backend.services.streaming_service.settings", MagicMock(DATA_STORE_PATH="/tmp/test")), \
             patch("backend.api.config_routes.get_app_state", return_value=mock_state):

            from backend.services.streaming_service import DataStreamingService
            try:
                await asyncio.wait_for(
                    DataStreamingService.stream_data(ws, ["LiveUser"], {}, active),
                    timeout=5.0,
                )
            except asyncio.TimeoutError:
                pass

        # Should not have any ERROR-level logs
        error_records = [
            r for r in caplog.records
            if r.levelno >= logging.ERROR and "streaming" in r.name.lower()
        ]
        assert len(error_records) == 0, (
            "ERROR log emitted after client disconnect (should exit silently):\n"
            + "\n".join(r.getMessage() for r in error_records)
        )

    async def test_websocket_disconnect_exception_silenced(self, caplog):
        """
        When send_json raises WebSocketDisconnect(1001) internally,
        streaming_service should catch and break rather than re-raise or log ERROR.
        """
        try:
            from starlette.websockets import WebSocketDisconnect
        except ImportError:
            pytest.skip("starlette not available")

        ws = AsyncMock()
        active = {ws}
        first_call = True

        async def _send_json_raises(msg):
            nonlocal first_call
            if first_call:
                first_call = False
                return  # stream_start sent successfully
            raise WebSocketDisconnect(1001)

        ws.send_json = _send_json_raises

        mock_reader = MagicMock()
        mock_reader.get_feature_types.return_value = ["heart_rate"]
        import pandas as pd
        mock_reader.load_features_batch.return_value = pd.DataFrame()
        mock_reader.load_feature_data.return_value = pd.DataFrame()

        mock_state = {"live_history_window": "1hour"}

        with caplog.at_level(logging.ERROR, logger="backend.services.streaming_service"), \
             patch("backend.services.streaming_service.create_reader", return_value=mock_reader), \
             patch("backend.services.streaming_service.settings", MagicMock(DATA_STORE_PATH="/tmp/test")), \
             patch("backend.api.config_routes.get_app_state", return_value=mock_state):

            from backend.services.streaming_service import DataStreamingService
            try:
                await asyncio.wait_for(
                    DataStreamingService.stream_data(ws, ["LiveUser"], {}, active),
                    timeout=5.0,
                )
            except asyncio.TimeoutError:
                pass

        streaming_errors = [
            r for r in caplog.records
            if r.levelno >= logging.ERROR
        ]
        assert len(streaming_errors) == 0, (
            "WebSocketDisconnect should be handled silently, but ERROR log was emitted:\n"
            + "\n".join(r.getMessage() for r in streaming_errors)
        )


# ===========================================================================
# Test 3: [TELEGRAM MESSAGE] format marker must not enter chat history
# ===========================================================================

class TestChatHistoryFormat:
    """
    Reproduction: chat history returned by the agent had user messages
    prefixed with '[TELEGRAM MESSAGE from 7965441365 at 2026-04-01 17:31]:',
    so the LLM saw metadata in later turns that should never have been stored.
    """

    def _make_envelope(self, content: str, chat_id: int = 123456, sender: int = 7965441365):
        """Build an envelope object resembling a real Telegram message."""
        from backend.messaging.base import MessageChannel
        env = MagicMock()
        env.content = content
        env.chat_id = chat_id
        env.sender_id = sender
        env.timestamp = datetime.now(timezone.utc)
        env.channel = MessageChannel.TELEGRAM
        return env

    def _make_minimal_agent(self):
        """
        Build a minimal agent mock bound with AgentLoopsMixin methods.
        Covers every attribute and method required inside _handle_chat_message.
        """
        from backend.agent.agent_loops import AgentLoopsMixin

        agent = MagicMock(spec=AgentLoopsMixin)

        # Required attributes
        agent._chat_histories = {}
        agent._max_chat_history = 20
        agent.user_messages_received = 0
        agent.max_turns = 5

        # Commit 2 (GatewayRegistry routing) added a tool_registry lookup
        # inside _handle_chat_message to inject the current envelope into
        # the reply tool. The minimal mock only needs get_tool to return
        # something falsy so the code path falls through cleanly.
        tool_registry_mock = MagicMock()
        tool_registry_mock.get_tool = MagicMock(return_value=None)
        agent.tool_registry = tool_registry_mock

        # Method mocks
        agent._set_state = MagicMock()
        agent._emit = AsyncMock()
        agent._save_state = MagicMock()
        agent._auto_reply = AsyncMock()
        agent._format_message_timestamp = MagicMock(return_value="2026-04-01 17:31")
        agent._get_system_prompt = MagicMock(return_value="System prompt")
        agent._get_chat_tool_definitions = MagicMock(return_value=[])

        # Bind real methods
        agent._handle_chat_message = AgentLoopsMixin._handle_chat_message.__get__(agent)

        return agent

    async def test_format_tag_not_stored_in_history(self):
        """
        User messages stored in chat history must not contain the '[TELEGRAM MESSAGE ...]' prefix.
        Only the per-turn message sent to the LLM carries the metadata marker;
        history stores the clean text only.
        """
        agent = self._make_minimal_agent()
        user_text = "How many steps today?"
        envelope = self._make_envelope(user_text)

        # Mock LLM: return text directly without calling any tool (triggers the auto-reply branch)
        reply_text = "You walked 6985 steps today."

        async def _mock_llm_call(self_ref, messages, tools, **kwargs):
            return (reply_text, [], "sig123")

        with patch("backend.agent.agent_loops._llm_call", _mock_llm_call), \
             patch("backend.agent.agent_loops.settings") as mock_settings:
            mock_settings.CHAT_MAX_TURNS = 5
            await agent._handle_chat_message(envelope)

        # Inspect user-role messages in history (keyed by channel:chat_id)
        history_key = f"{envelope.channel.value}:{envelope.chat_id}"
        history = agent._chat_histories.get(history_key, [])
        user_msgs = [m for m in history if m["role"] == "user"]

        assert len(user_msgs) >= 1, "History should contain at least one user message"
        for msg in user_msgs:
            assert "[TELEGRAM MESSAGE" not in msg["content"], (
                f"User message stored in history contains the format marker (should not be stored):\n{msg['content']}"
            )
            assert msg["content"] == user_text, (
                f"User message stored in history should be the raw text '{user_text}', "
                f"got: '{msg['content']}'"
            )

    async def test_llm_receives_format_tag_in_current_turn(self):
        """
        The per-turn message sent to the LLM should include the '[TELEGRAM MESSAGE ...]'
        metadata so the LLM knows the source and timestamp, but that message
        must not be stored in history.
        """
        agent = self._make_minimal_agent()
        user_text = "Analyze today's heart rate for me"
        envelope = self._make_envelope(user_text)

        captured_messages: list[dict] = []

        async def _capture_llm_call(self_ref, messages, tools, **kwargs):
            captured_messages.extend(messages)
            return ("OK, heart rate looks normal.", [], "sig456")

        with patch("backend.agent.agent_loops._llm_call", _capture_llm_call), \
             patch("backend.agent.agent_loops.settings") as mock_settings:
            mock_settings.CHAT_MAX_TURNS = 5
            await agent._handle_chat_message(envelope)

        # The user message passed to the LLM should carry the format marker
        user_msgs_to_llm = [
            m for m in captured_messages
            if m.get("role") == "user" and "[TELEGRAM MESSAGE" in m.get("content", "")
        ]
        assert len(user_msgs_to_llm) >= 1, (
            "Messages sent to the LLM should include a user message prefixed with "
            "'[TELEGRAM MESSAGE ...]', but none was found. The LLM needs the source and timestamp."
        )


# ===========================================================================
# Test 4: manage tool behavior verification
# ===========================================================================

class TestManageSubAgent:
    """
    Reproduction: user says "start monitoring heart rate", chat agent calls
    the manage tool to create a trigger rule, but nothing is actually written
    to the DB and the agent only acknowledges verbally.
    """

    def _build_registry(self, tmp_dirs):
        """Build a ToolRegistry that contains the sql and create_page tools."""
        from backend.agent.data_store import DataStore
        from backend.agent.tools.code_tool import CodeTool
        from backend.agent.tools.create_page_tool import CreatePageTool
        from backend.agent.tools.push_report_tool import PushReportTool
        from backend.agent.tools.registry import ToolRegistry
        from backend.agent.tools.sql_tool import SQLTool
        from backend.agent.tools.update_md_tool import UpdateMdTool

        ds = DataStore(db_path=tmp_dirs["data_stores"], user_id="LiveUser")
        registry = ToolRegistry()
        registry.register_tool(SQLTool(ds, tmp_dirs["memory"], "LiveUser"))
        registry.register_tool(CodeTool(ds, tmp_dirs["memory"], "LiveUser"))
        registry.register_tool(PushReportTool(tmp_dirs["memory"], "LiveUser"))
        registry.register_tool(UpdateMdTool())
        create_tool = CreatePageTool(tmp_dirs["memory"], "LiveUser", tmp_dirs["data_stores"])
        create_tool._pages_dir = tmp_dirs["personalised_pages"]
        registry.register_tool(create_tool)

        return registry, ds

    @pytest.fixture
    def manage_agent(self, tmp_dirs, memory_db):
        """Create a minimal agent mock capable of invoking _run_chat_manage."""
        from backend.agent.agent_loops import AgentLoopsMixin

        registry, ds = self._build_registry(tmp_dirs)

        agent = MagicMock(spec=AgentLoopsMixin)
        agent._set_state = MagicMock()
        agent._emit = AsyncMock()
        agent.cycle_count = 1
        agent.tool_registry = registry

        # Bind the real _run_chat_manage, _execute_tool, and prompt builder.
        # The prompt builder lives on AgentPromptsMixin and internally calls
        # _append_layers (also on AgentPromptsMixin), so rebind both.
        from backend.agent.agent_prompts import AgentPromptsMixin
        from backend.agent.agent_tools import AgentToolsMixin
        agent._execute_tool = AgentToolsMixin._execute_tool.__get__(agent)
        agent._get_sub_manage_tool_definitions = AgentToolsMixin._get_sub_manage_tool_definitions.__get__(agent)
        agent._run_chat_manage = AgentLoopsMixin._run_chat_manage.__get__(agent)
        agent.build_sub_manage_prompt = AgentPromptsMixin.build_sub_manage_prompt.__get__(agent)
        agent._append_layers = AgentPromptsMixin._append_layers.__get__(agent)

        return agent, tmp_dirs

    async def test_manage_creates_trigger_rule_in_db(self, manage_agent):
        """
        _run_chat_manage(goal='create trigger rule for high heart rate')
        Should actually create a row in the memory DB's trigger_rules table.
        The mock LLM drives the sub-agent to run a real sql INSERT (including all
        NOT NULL columns), then we verify the row really landed in the DB.
        """
        agent, tmp_dirs = manage_agent

        db_file = tmp_dirs["memory"] / "LiveUser.db"
        # trigger_rules has required columns: name, feature_type, condition, threshold, prompt_goal
        insert_sql = (
            "memory:INSERT INTO trigger_rules "
            "(name, feature_type, condition, threshold, cooldown_minutes, prompt_goal, status) "
            "VALUES ('high_hr_test', 'heart_rate', 'gt', 100.0, 30, "
            "'Investigate elevated heart rate', 'active')"
        )
        tool_call_sent = False

        async def _mock_llm_call(self_ref, messages, tools, **kwargs):
            nonlocal tool_call_sent
            if not tool_call_sent:
                tool_call_sent = True
                return (
                    "Creating heart-rate trigger rule...",
                    [{"name": "sql", "id": "tc_1", "arguments": {"query": insert_sql}}],
                    "sig_manage_1",
                )
            # Second round: no tool calls, return final text
            return ("Heart-rate trigger rule created successfully.", [], "sig_manage_2")

        with patch("backend.agent.agent_loops._llm_call", _mock_llm_call):
            await agent._run_chat_manage("Create a trigger rule for high heart rate", "test_chat_123")

        # Verify the DB actually has a new row
        with sqlite3.connect(db_file) as conn:
            row = conn.execute(
                "SELECT name, feature_type, condition, threshold FROM trigger_rules "
                "WHERE name = 'high_hr_test'"
            ).fetchone()

        assert row is not None, (
            "After the manage sub-agent called the sql tool, trigger_rules should have a new row but none was found.\n"
            f"Executed INSERT: {insert_sql}\n"
            "_execute_tool binding may have failed, or the SQL write may have been blocked."
        )
        assert row[1] == "heart_rate", f"feature_type should be 'heart_rate', got {row[1]}"
        assert row[2] == "gt", f"condition should be 'gt', got {row[2]}"
        assert abs(row[3] - 100.0) < 0.01, f"threshold should be 100.0, got {row[3]}"

    def test_manage_is_in_chat_tools(self, tmp_dirs, memory_db):
        """
        The manage tool should appear in the chat-mode tool definitions.
        This ensures the chat agent knows it can delegate framework CRUD to the manage sub-agent.
        """
        from backend.agent.agent_tools import AgentToolsMixin

        registry, _ = self._build_registry(tmp_dirs)

        # Instantiate a real AgentToolsMixin subclass (only override tool_registry)
        # to avoid MagicMock intercepting the @staticmethod _load_tool_json
        class _MinimalAgent(AgentToolsMixin):
            pass

        agent = _MinimalAgent.__new__(_MinimalAgent)
        agent.tool_registry = registry

        chat_defs = agent._get_chat_tool_definitions()
        tool_names = [d["function"]["name"] for d in chat_defs]

        assert "manage" in tool_names, (
            f"manage tool should be in chat-mode tool definitions, got: {tool_names}"
        )

    def test_manage_not_in_sub_analysis_tools(self, tmp_dirs, memory_db):
        """
        The ``manage`` tool must not appear in sub_analysis tool definitions.

        After the unified architecture, every analysis path (chat delegation /
        cron autonomous / iOS quick) runs through the same read-only sub_analysis
        engine, which only has sql / code / read_skill. All writes must go
        through the chat -> manage -> sub_manage path. Exposing ``manage``
        to sub_analysis would break the read/write split.
        """
        from backend.agent.agent_tools import AgentToolsMixin

        registry, _ = self._build_registry(tmp_dirs)

        class _MinimalAgent(AgentToolsMixin):
            pass

        agent = _MinimalAgent.__new__(_MinimalAgent)
        agent.tool_registry = registry

        sub_defs = agent._get_sub_analysis_tool_definitions()
        tool_names = [d["function"]["name"] for d in sub_defs]

        assert "manage" not in tool_names, (
            f"manage tool should NOT be in sub_analysis tool definitions, got: {tool_names}"
        )
        # And sub_analysis should be strictly sql + code + read_skill —
        # no write tools at all.
        assert set(tool_names) <= {"sql", "code", "read_skill"}, (
            f"sub_analysis should expose only read-only tools, got: {tool_names}"
        )


# ===========================================================================
# Test 5: create_page patch mode preserves existing functionality
# ===========================================================================

class TestCreatePagePatch:
    """
    Reproduction: user says "add a new feature to the page", agent calls
    create_page(patch=True), but it overwrites the original HTML and the
    existing feature disappears.
    """

    @pytest.fixture
    def create_tool(self, tmp_dirs, memory_db):
        """Create a CreatePageTool backed by a tmp directory."""
        from backend.agent.tools.create_page_tool import CreatePageTool
        tool = CreatePageTool(
            memory_db_path=tmp_dirs["memory"],
            user_id="LiveUser",
            data_store_path=tmp_dirs["data_stores"],
        )
        tool._pages_dir = tmp_dirs["personalised_pages"]
        return tool

    async def test_patch_preserves_existing_html(self, create_tool, tmp_dirs):
        """
        With patch=True, passing an empty frontend_html should preserve the
        original HTML and not overwrite it with an empty string.
        """
        # 1. Create the original page
        original_html = "<p>original dashboard content</p>"
        result1 = await create_tool.execute(
            page_id="patch_test_v1",
            display_name="Patch Test V1",
            backend_code="async def route_handler(request):\n    return {'data': []}",
            frontend_html=original_html,
        )
        assert result1["success"] is True, f"Initial create failed: {result1}"

        # 2. patch=True: only update backend code, do not pass frontend_html
        result2 = await create_tool.execute(
            page_id="patch_test_v1",
            display_name="Patch Test V1",
            backend_code="async def route_handler(request):\n    return {'data': 'new'}",
            frontend_html="",  # empty string = do not update HTML
            patch=True,
        )
        assert result2["success"] is True, f"Patch operation failed: {result2}"

        # 3. Read index.html from disk; it should retain the original content
        html_path = tmp_dirs["personalised_pages"] / "patch_test_v1" / "index.html"
        actual_html = html_path.read_text(encoding="utf-8")
        assert actual_html == original_html, (
            f"When patch=True and frontend_html='', original HTML should be preserved.\n"
            f"Expected: '{original_html}'\n"
            f"Actual:   '{actual_html}'"
        )

    async def test_patch_updates_backend_code(self, create_tool, tmp_dirs):
        """
        With patch=True, passing a new backend_code should overwrite the
        existing backend code to update the page functionality.
        """
        # Create the initial page
        await create_tool.execute(
            page_id="patch_backend_v1",
            display_name="Backend Patch Test",
            backend_code="async def route_handler(request):\n    return {'version': 1}",
            frontend_html="<p>UI</p>",
        )

        # Patch-update backend_code
        result = await create_tool.execute(
            page_id="patch_backend_v1",
            display_name="Backend Patch Test",
            backend_code="async def route_handler(request):\n    return {'version': 2}",
            frontend_html="",
            patch=True,
        )
        assert result["success"] is True, f"Patch backend failed: {result}"

        route_path = tmp_dirs["personalised_pages"] / "patch_backend_v1" / "route.py"
        route_content = route_path.read_text(encoding="utf-8")
        assert "'version': 2" in route_content, (
            f"After patch, route.py should contain the new code \"'version': 2\",\n"
            f"actual content (first 200 chars): {route_content[:200]}"
        )
        # Confirm old code has been replaced
        assert "'version': 1" not in route_content, (
            "After patch, old code \"'version': 1\" should no longer appear in route.py"
        )

    async def test_full_create_still_overwrites(self, create_tool, tmp_dirs):
        """
        patch=False (default) should fully overwrite the existing page;
        this verifies the default behavior is unchanged.
        Note: create_page has a 60s dedup window, so we create v1, clear the
        dedup cache, and then recreate v2 with the same ID from scratch.
        """
        import backend.agent.tools.create_page_tool as _cat_module

        page_id = "overwrite_fulltest_v1"

        # Create v1 first
        result1 = await create_tool.execute(
            page_id=page_id,
            display_name="Overwrite V1",
            backend_code="async def route_handler(request):\n    return {}",
            frontend_html="<p>Version 1</p>",
        )
        assert result1["success"] is True, f"Initial create failed: {result1}"

        html_path = tmp_dirs["personalised_pages"] / page_id / "index.html"
        assert html_path.read_text(encoding="utf-8") == "<p>Version 1</p>"

        # Clear the dedup cache to simulate creation more than 60s later
        _cat_module._recent_creations.pop(page_id, None)

        # Do not pass patch=True (default patch=False): should fully overwrite
        result2 = await create_tool.execute(
            page_id=page_id,
            display_name="Overwrite V2",
            backend_code="async def route_handler(request):\n    return {}",
            frontend_html="<p>Version 2</p>",
        )
        assert result2["success"] is True, f"Second full create failed: {result2}"

        actual_html = html_path.read_text(encoding="utf-8")
        assert actual_html == "<p>Version 2</p>", (
            f"patch=False (default) should fully overwrite HTML.\n"
            f"Expected: '<p>Version 2</p>'\n"
            f"Actual:   '{actual_html}'"
        )

    async def test_patch_nonexistent_page_creates_new(self, create_tool):
        """
        If the page does not exist, patch=True should gracefully fall back to normal creation (not error).
        """
        result = await create_tool.execute(
            page_id="brand_new_page_v1",
            display_name="Brand New",
            backend_code="async def route_handler(request):\n    return {}",
            frontend_html="<p>New</p>",
            patch=True,
        )
        assert result["success"] is True, (
            f"When patch=True and the page does not exist, a new page should be created, but it failed: {result}"
        )


# ===========================================================================
# Test 6: croniter imports correctly
# ===========================================================================

def test_croniter_importable():
    """
    Reproduction: the scheduled-task runner raised ImportError because croniter
    was not installed, so the daily 08:00 sleep analysis task could not run.
    Verify croniter imports correctly and can parse cron expressions.
    """
    try:
        from croniter import croniter
    except ImportError as e:
        pytest.fail(
            f"Failed to import croniter: {e}. "
            "Run 'pip install croniter' to install the dependency; "
            "otherwise the scheduled-task runner cannot work."
        )

    # Verify it parses the actual cron expression in use (daily 08:00)
    c = croniter("0 8 * * *")
    next_run = c.get_next()
    assert next_run > 0, f"croniter.get_next() should return a positive timestamp, got {next_run}"

    # Verify the hourly cron also parses
    c2 = croniter("0 * * * *")
    next_hourly = c2.get_next()
    assert next_hourly > 0, f"Hourly cron parse failed, get_next() returned {next_hourly}"


# ===========================================================================
# Extra: streaming service WebSocket clean disconnect (connection already removed from active)
# ===========================================================================

class TestWebSocketCleanExit:
    """
    Extra test: stream_data should exit cleanly after the websocket is
    removed from active_connections. This simulates the normal lifecycle
    after ConnectionManager.disconnect() is called.
    """

    async def test_exits_when_removed_from_active(self):
        """
        When the websocket is removed from the active_connections set,
        the stream_data while loop should exit on the next iteration without any ERROR log.
        """
        ws = AsyncMock()
        active = {ws}

        # Remove ws after the first successful send to simulate a clean disconnect
        send_count = 0

        async def _controlled_send(msg):
            nonlocal send_count
            send_count += 1
            if send_count >= 1:
                active.discard(ws)

        ws.send_json = _controlled_send

        mock_reader = MagicMock()
        mock_reader.get_feature_types.return_value = []
        import pandas as pd
        mock_reader.load_features_batch.return_value = pd.DataFrame()

        mock_state = {"live_history_window": "1hour"}

        with patch("backend.services.streaming_service.create_reader", return_value=mock_reader), \
             patch("backend.services.streaming_service.settings", MagicMock(DATA_STORE_PATH="/tmp/test")), \
             patch("backend.api.config_routes.get_app_state", return_value=mock_state):

            from backend.services.streaming_service import DataStreamingService
            # Should exit cleanly within the timeout
            await asyncio.wait_for(
                DataStreamingService.stream_data(ws, ["LiveUser"], {}, active),
                timeout=5.0,
            )

        # stream_start was sent at least once
        assert send_count >= 1, "stream_start message should have been sent"
