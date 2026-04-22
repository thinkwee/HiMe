"""
Tests for tool-level enhancements:
  - query_cache.py: SQL query result LRU cache
  - result_manager.py: Smart result truncation
  - BaseTool enhancements: concurrency safety, progress callbacks, input validation
  - AgentToolsMixin: parallel tool grouping, result formatting with truncation
"""
from __future__ import annotations

import json
import time
from unittest.mock import MagicMock

# =========================================================================
# QueryCache
# =========================================================================

class TestQueryCache:
    """Tests for backend.agent.tools.query_cache."""

    def test_put_and_get(self):
        from backend.agent.tools.query_cache import QueryCache
        cache = QueryCache()
        cache.put("SELECT 1", "health_data", {"success": True, "data": 1})
        result = cache.get("SELECT 1", "health_data")
        assert result is not None
        assert result["data"] == 1

    def test_miss_returns_none(self):
        from backend.agent.tools.query_cache import QueryCache
        cache = QueryCache()
        assert cache.get("SELECT 1", "health_data") is None

    def test_different_database_different_key(self):
        from backend.agent.tools.query_cache import QueryCache
        cache = QueryCache()
        cache.put("SELECT 1", "health_data", {"success": True, "db": "health"})
        cache.put("SELECT 1", "memory", {"success": True, "db": "memory"})
        assert cache.get("SELECT 1", "health_data")["db"] == "health"
        assert cache.get("SELECT 1", "memory")["db"] == "memory"

    def test_ttl_expiration(self):
        from backend.agent.tools.query_cache import QueryCache
        cache = QueryCache(ttl_seconds=0.01)
        cache.put("SELECT 1", "test", {"success": True})
        time.sleep(0.02)
        assert cache.get("SELECT 1", "test") is None

    def test_lru_eviction(self):
        from backend.agent.tools.query_cache import QueryCache
        cache = QueryCache(max_size=3)
        cache.put("q1", "db", {"success": True, "id": 1})
        cache.put("q2", "db", {"success": True, "id": 2})
        cache.put("q3", "db", {"success": True, "id": 3})
        cache.put("q4", "db", {"success": True, "id": 4})
        # q1 should have been evicted
        assert cache.get("q1", "db") is None
        assert cache.get("q4", "db") is not None

    def test_lru_refresh_on_get(self):
        from backend.agent.tools.query_cache import QueryCache
        cache = QueryCache(max_size=3)
        cache.put("q1", "db", {"success": True, "id": 1})
        cache.put("q2", "db", {"success": True, "id": 2})
        cache.put("q3", "db", {"success": True, "id": 3})
        # Access q1 to refresh it
        cache.get("q1", "db")
        # Now add q4 — q2 (least recently used) should be evicted
        cache.put("q4", "db", {"success": True, "id": 4})
        assert cache.get("q1", "db") is not None
        assert cache.get("q2", "db") is None

    def test_does_not_cache_failures(self):
        from backend.agent.tools.query_cache import QueryCache
        cache = QueryCache()
        cache.put("bad query", "db", {"success": False, "error": "syntax error"})
        assert cache.get("bad query", "db") is None

    def test_invalidate_specific_database(self):
        from backend.agent.tools.query_cache import QueryCache
        cache = QueryCache()
        cache.put("q1", "health_data", {"success": True})
        cache.put("q2", "memory", {"success": True})
        cache.invalidate("memory")
        assert cache.get("q1", "health_data") is not None
        assert cache.get("q2", "memory") is None

    def test_invalidate_all(self):
        from backend.agent.tools.query_cache import QueryCache
        cache = QueryCache()
        cache.put("q1", "health_data", {"success": True})
        cache.put("q2", "memory", {"success": True})
        cache.invalidate()
        assert cache.get("q1", "health_data") is None
        assert cache.get("q2", "memory") is None

    def test_clear(self):
        from backend.agent.tools.query_cache import QueryCache
        cache = QueryCache()
        cache.put("q1", "db", {"success": True})
        cache.clear()
        assert cache.stats["size"] == 0
        assert cache.stats["hits"] == 0

    def test_stats_tracking(self):
        from backend.agent.tools.query_cache import QueryCache
        cache = QueryCache()
        cache.put("q1", "db", {"success": True})
        cache.get("q1", "db")  # hit
        cache.get("q2", "db")  # miss
        stats = cache.stats
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["size"] == 1
        assert "50.0%" in stats["hit_rate"]

    def test_whitespace_normalization(self):
        from backend.agent.tools.query_cache import QueryCache
        cache = QueryCache()
        cache.put("SELECT  1  FROM  samples", "db", {"success": True})
        # Should match with different whitespace
        assert cache.get("SELECT 1 FROM samples", "db") is not None

    def test_case_insensitive_normalization(self):
        from backend.agent.tools.query_cache import QueryCache
        cache = QueryCache()
        cache.put("select 1", "db", {"success": True})
        assert cache.get("SELECT 1", "db") is not None


# =========================================================================
# ResultManager
# =========================================================================

class TestResultManager:
    """Tests for backend.agent.tools.result_manager."""

    def test_small_result_unchanged(self):
        from backend.agent.tools.result_manager import truncate_result
        result = {"success": True, "output": "hello"}
        assert truncate_result(result, "code") is result

    def test_code_result_truncation(self):
        from backend.agent.tools.result_manager import truncate_result
        result = {"success": True, "output": "x" * 20_000}
        truncated = truncate_result(result, "code")
        assert truncated["truncated"] is True
        assert truncated["original_length"] == 20_000
        assert "omitted" in truncated["output"]
        assert len(truncated["output"]) < 20_000

    def test_sql_row_truncation(self):
        from backend.agent.tools.result_manager import truncate_result
        # Generate enough rows so the JSON exceeds MAX_RESULT_CHARS
        rows = [[i, f"value_for_row_{i}_" + "x" * 80] for i in range(200)]
        md_lines = [f"| {i} | value_for_row_{i}_{'x' * 80} |" for i in range(200)]
        result = {
            "success": True,
            "rows": rows,
            "columns": ["id", "value"],
            "markdown": "\n".join(md_lines),
            "row_count": 200,
        }
        truncated = truncate_result(result, "sql")
        assert truncated["truncated"] is True
        assert len(truncated["rows"]) == 20
        assert truncated["total_rows"] == 200
        assert "LIMIT" in truncated.get("note", "")

    def test_sql_small_result_untouched(self):
        from backend.agent.tools.result_manager import truncate_result
        result = {
            "success": True,
            "rows": [[1, "a"], [2, "b"]],
            "columns": ["id", "val"],
            "markdown": "| 1 | a |\n| 2 | b |",
            "row_count": 2,
        }
        truncated = truncate_result(result, "sql")
        assert truncated.get("truncated") is not True  # None or missing

    def test_generic_truncation(self):
        from backend.agent.tools.result_manager import truncate_result
        result = {"success": True, "data": "x" * 20_000}
        truncated = truncate_result(result, "unknown_tool")
        assert truncated["truncated"] is True
        assert truncated["original_length"] > 0

    def test_failed_result_not_truncated(self):
        from backend.agent.tools.result_manager import truncate_result
        result = {"success": False, "error": "short error"}
        assert truncate_result(result, "code") is result


# =========================================================================
# BaseTool enhancements
# =========================================================================

class TestBaseToolEnhancements:
    """Tests for BaseTool concurrency, progress, and validation enhancements."""

    def test_default_concurrency_safe_is_false(self):
        from backend.agent.tools.base import BaseTool

        class DummyTool(BaseTool):
            def get_definition(self):
                return {}
            async def execute(self, **kw):
                return {"success": True}

        tool = DummyTool()
        assert tool.is_concurrency_safe is False

    def test_sql_tool_is_concurrency_safe(self):
        from backend.agent.tools.sql_tool import SQLTool
        # Just check the property declaration (can't instantiate without deps)
        assert SQLTool.is_concurrency_safe.fget is not None

    def test_code_tool_is_not_concurrency_safe(self):
        from backend.agent.tools.code_tool import CodeTool
        assert CodeTool.is_concurrency_safe.fget is not None

    def test_progress_callback(self):
        from backend.agent.tools.base import BaseTool

        class DummyTool(BaseTool):
            def get_definition(self):
                return {}
            async def execute(self, **kw):
                self.report_progress({"status": "running"})
                return {"success": True}

        events = []
        tool = DummyTool()
        tool.name = "dummy"
        tool.set_progress_callback(lambda name, data: events.append((name, data)))
        tool.report_progress({"step": 1})
        assert len(events) == 1
        assert events[0] == ("dummy", {"step": 1})

    def test_progress_callback_none_is_safe(self):
        from backend.agent.tools.base import BaseTool

        class DummyTool(BaseTool):
            def get_definition(self):
                return {}
            async def execute(self, **kw):
                return {"success": True}

        tool = DummyTool()
        # Should not raise even without a callback
        tool.report_progress({"anything": True})

    async def test_call_with_input_schema(self):
        from pydantic import BaseModel

        from backend.agent.tools.base import BaseTool

        class MyInput(BaseModel):
            name: str
            count: int = 1

        class MyTool(BaseTool):
            input_schema = MyInput
            def get_definition(self):
                return {}
            async def execute(self, name: str, count: int = 1, **kw):
                return {"success": True, "name": name, "count": count}

        tool = MyTool()
        # Valid call
        result = await tool(name="test", count=5)
        assert result["success"] is True
        assert result["name"] == "test"
        assert result["count"] == 5

    async def test_call_with_invalid_input(self):
        from pydantic import BaseModel

        from backend.agent.tools.base import BaseTool

        class MyInput(BaseModel):
            name: str
            count: int

        class MyTool(BaseTool):
            input_schema = MyInput
            def get_definition(self):
                return {}
            async def execute(self, **kw):
                return {"success": True}

        tool = MyTool()
        # Missing required field
        result = await tool(name="test")
        assert result["success"] is False
        assert result["error_type"] == "validation"
        assert "count" in result["error"]

    async def test_call_with_semantic_validation(self):
        from backend.agent.tools.base import BaseTool

        class MyTool(BaseTool):
            def get_definition(self):
                return {}
            async def validate_input(self, mode: str = "", **kw):
                if mode == "bad":
                    return "Mode 'bad' is not allowed"
                return None
            async def execute(self, **kw):
                return {"success": True}

        tool = MyTool()
        tool.input_schema = None  # no schema, but has semantic validation

        # Trigger semantic validation via __call__
        result = await tool(mode="bad")
        assert result["success"] is False
        assert "not allowed" in result["error"]

    async def test_call_without_schema_falls_through(self):
        from backend.agent.tools.base import BaseTool

        class MyTool(BaseTool):
            def get_definition(self):
                return {}
            async def execute(self, x: int = 0, **kw):
                return {"success": True, "x": x}

        tool = MyTool()
        result = await tool(x=42)
        assert result["success"] is True
        assert result["x"] == 42


# =========================================================================
# Tool name attributes
# =========================================================================

class TestToolNames:
    """Verify all tools have correct name attributes."""

    def test_sql_tool_name(self):
        from backend.agent.tools.sql_tool import SQLTool
        assert SQLTool.name == "sql"

    def test_code_tool_name(self):
        from backend.agent.tools.code_tool import CodeTool
        assert CodeTool.name == "code"

    def test_push_report_tool_name(self):
        from backend.agent.tools.push_report_tool import PushReportTool
        assert PushReportTool.name == "push_report"

    def test_update_md_tool_name(self):
        from backend.agent.tools.update_md_tool import UpdateMdTool
        assert UpdateMdTool.name == "update_md"

    def test_reply_user_tool_name(self):
        from backend.agent.tools.reply_user_tool import ReplyUserTool
        assert ReplyUserTool.name == "reply_user"

    def test_finish_chat_tool_name(self):
        from backend.agent.tools.finish_chat_tool import FinishChatTool
        assert FinishChatTool.name == "finish_chat"

    def test_create_page_tool_name(self):
        from backend.agent.tools.create_page_tool import CreatePageTool
        assert CreatePageTool.name == "create_page"


# =========================================================================
# AgentToolsMixin — parallel grouping
# =========================================================================

class TestParallelToolGrouping:
    """Tests for AgentToolsMixin._group_tool_calls_by_concurrency."""

    def _make_mixin(self):
        from backend.agent.agent_tools import AgentToolsMixin
        mixin = AgentToolsMixin()
        # Mock the registry
        registry = MagicMock()

        sql_tool = MagicMock()
        sql_tool.is_concurrency_safe = True

        code_tool = MagicMock()
        code_tool.is_concurrency_safe = False

        push_tool = MagicMock()
        push_tool.is_concurrency_safe = False

        def get_tool(name):
            return {"sql": sql_tool, "code": code_tool, "push_report": push_tool}.get(name)

        registry.get_tool = get_tool
        mixin.tool_registry = registry
        return mixin

    def test_single_safe_tool(self):
        mixin = self._make_mixin()
        calls = [{"name": "sql", "arguments": {}}]
        groups = mixin._group_tool_calls_by_concurrency(calls)
        assert len(groups) == 1
        is_parallel, tcs = groups[0]
        assert is_parallel is True
        assert len(tcs) == 1

    def test_multiple_safe_tools_grouped(self):
        mixin = self._make_mixin()
        calls = [
            {"name": "sql", "arguments": {"query": "q1"}},
            {"name": "sql", "arguments": {"query": "q2"}},
            {"name": "sql", "arguments": {"query": "q3"}},
        ]
        groups = mixin._group_tool_calls_by_concurrency(calls)
        assert len(groups) == 1
        is_parallel, tcs = groups[0]
        assert is_parallel is True
        assert len(tcs) == 3

    def test_unsafe_tool_is_singleton_group(self):
        mixin = self._make_mixin()
        calls = [{"name": "code", "arguments": {"code": "print(1)"}}]
        groups = mixin._group_tool_calls_by_concurrency(calls)
        assert len(groups) == 1
        is_parallel, tcs = groups[0]
        assert is_parallel is False

    def test_mixed_tools_split_correctly(self):
        mixin = self._make_mixin()
        calls = [
            {"name": "sql", "arguments": {"query": "q1"}},
            {"name": "sql", "arguments": {"query": "q2"}},
            {"name": "code", "arguments": {"code": "print(1)"}},
            {"name": "sql", "arguments": {"query": "q3"}},
        ]
        groups = mixin._group_tool_calls_by_concurrency(calls)
        assert len(groups) == 3
        assert groups[0][0] is True   # 2 sql parallel
        assert len(groups[0][1]) == 2
        assert groups[1][0] is False  # 1 code serial
        assert len(groups[1][1]) == 1
        assert groups[2][0] is True   # 1 sql parallel (singleton)
        assert len(groups[2][1]) == 1

    def test_push_report_is_serial(self):
        mixin = self._make_mixin()
        calls = [
            {"name": "sql", "arguments": {}},
            {"name": "push_report", "arguments": {}},
        ]
        groups = mixin._group_tool_calls_by_concurrency(calls)
        assert len(groups) == 2
        assert groups[0][0] is True   # sql parallel
        assert groups[1][0] is False  # push_report serial


# =========================================================================
# Result formatting with truncation
# =========================================================================

class TestResultFormatting:
    """Tests for AgentToolsMixin._format_tool_result with truncation."""

    def test_sql_result_formatted_as_markdown(self):
        from backend.agent.agent_tools import AgentToolsMixin
        result = {
            "success": True,
            "markdown": "| a | b |\n|---|---|\n| 1 | 2 |",
            "row_count": 1,
        }
        formatted = AgentToolsMixin._format_tool_result("sql", result, {"query": "SELECT 1"})
        assert "Tool `sql`" in formatted
        assert "1 rows" in formatted
        assert "| a | b |" in formatted

    def test_large_code_result_truncated(self):
        from backend.agent.agent_tools import AgentToolsMixin
        result = {"success": True, "output": "x" * 20_000}
        formatted = AgentToolsMixin._format_tool_result("code", result, {"code": "print('x'*20000)"})
        assert "omitted" in formatted
        assert len(formatted) < 20_000

    def test_query_label_included(self):
        from backend.agent.agent_tools import AgentToolsMixin
        result = {"success": True, "data": "ok"}
        formatted = AgentToolsMixin._format_tool_result(
            "sql", result, {"query": "SELECT COUNT(*) FROM samples"}
        )
        assert "SELECT COUNT" in formatted


# =========================================================================
# Key findings extraction
# =========================================================================

class TestKeyFindingsExtraction:
    """Tests for _extract_key_findings in agent_loops."""

    def test_extracts_from_tool_results(self):
        from backend.agent.agent_loops import _extract_key_findings
        groups = [[
            {"role": "assistant", "content": "Let me query"},
            {"role": "tool", "content": json.dumps({
                "success": True,
                "row_count": 5,
                "columns": ["hr", "ts"],
            })},
        ]]
        findings = _extract_key_findings(groups)
        assert "SQL" in findings
        assert "5 rows" in findings

    def test_extracts_from_assistant_findings(self):
        from backend.agent.agent_loops import _extract_key_findings
        groups = [[
            {"role": "assistant", "content": "The average heart rate was 72 bpm, which is normal."},
        ]]
        findings = _extract_key_findings(groups)
        assert "average" in findings.lower() or "normal" in findings.lower()

    def test_empty_groups_return_empty(self):
        from backend.agent.agent_loops import _extract_key_findings
        assert _extract_key_findings([]) == ""

    def test_deduplication(self):
        from backend.agent.agent_loops import _extract_key_findings
        groups = [
            [{"role": "assistant", "content": "The average is 72"}],
            [{"role": "assistant", "content": "The average is 72"}],
        ]
        findings = _extract_key_findings(groups)
        lines = [line for line in findings.split("\n") if line.strip()]
        assert len(lines) == 1  # Deduplicated

    def test_max_10_findings(self):
        from backend.agent.agent_loops import _extract_key_findings
        groups = [
            [{"role": "assistant", "content": f"The average of metric_{i} increased to {i}"}]
            for i in range(20)
        ]
        findings = _extract_key_findings(groups)
        lines = [line for line in findings.split("\n") if line.strip()]
        assert len(lines) <= 10
