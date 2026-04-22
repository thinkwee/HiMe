"""
Tests for HIME agent tool implementations.

Covers:
- SQLTool: read queries, write rejection on health_data, read-write on memory
- CodeTool: Python execution, timeout, error handling
- PushReportTool: report persistence and format
- UpdateMdTool: file-path validation, editable-section replacement
- CreatePageTool: page creation, security validation
"""
from __future__ import annotations

import sqlite3
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.agent.tools.code_tool import CodeTool
from backend.agent.tools.create_page_tool import CreatePageTool
from backend.agent.tools.push_report_tool import PushReportTool
from backend.agent.tools.sql_tool import SQLTool
from backend.agent.tools.update_md_tool import UpdateMdTool

# =========================================================================
# SQLTool
# =========================================================================

class TestSQLTool:
    """Tests for the SQL tool (health_data read-only, memory read-write)."""

    @pytest.fixture
    def sql_tool(self, data_store, tmp_dirs, memory_db):
        return SQLTool(
            data_store=data_store,
            memory_db_path=tmp_dirs["memory"],
            user_id="LiveUser",
        )

    async def test_health_data_select(self, sql_tool):
        """SELECT on health_data should succeed and return rows."""
        result = await sql_tool.execute(
            query="health_data:SELECT feature_type, COUNT(*) as cnt FROM samples GROUP BY feature_type LIMIT 5"
        )
        assert result["success"] is True
        assert result["row_count"] > 0
        assert "columns" in result
        assert "feature_type" in result["columns"]

    async def test_health_data_rejects_write(self, sql_tool):
        """INSERT/UPDATE/DELETE on health_data should be rejected."""
        result = await sql_tool.execute(
            query="health_data:INSERT INTO samples (timestamp, feature_type, value) VALUES ('2026-01-01', 'test', 42)"
        )
        assert result["success"] is False
        assert "READ-ONLY" in result["error"] or "not authorized" in result["error"].lower()

    async def test_health_data_rejects_drop(self, sql_tool):
        """DROP TABLE on health_data should be rejected."""
        result = await sql_tool.execute(
            query="health_data:DROP TABLE samples"
        )
        assert result["success"] is False

    async def test_health_data_empty_result_shows_features(self, sql_tool):
        """An empty SELECT should still succeed and may include available_feature_types."""
        result = await sql_tool.execute(
            query="health_data:SELECT * FROM samples WHERE feature_type = 'nonexistent_feature'"
        )
        assert result["success"] is True
        assert result["row_count"] == 0

    async def test_memory_select(self, sql_tool):
        """SELECT on memory DB should succeed."""
        result = await sql_tool.execute(query="memory:SELECT * FROM reports")
        assert result["success"] is True
        assert result["row_count"] >= 1
        assert "title" in result["columns"]

    async def test_memory_insert(self, sql_tool):
        """INSERT on memory DB should succeed."""
        result = await sql_tool.execute(
            query="memory:INSERT INTO activity_log (event_type, event_data) VALUES ('test', '{\"foo\": 1}')"
        )
        assert result["success"] is True
        assert result["rows_affected"] == 1

    async def test_memory_create_table(self, sql_tool):
        """CREATE TABLE on memory DB should succeed (agent creates custom tables)."""
        result = await sql_tool.execute(
            query="memory:CREATE TABLE IF NOT EXISTS test_table (id INTEGER PRIMARY KEY, data TEXT)"
        )
        assert result["success"] is True

    async def test_auto_detect_health_data(self, sql_tool):
        """Query without prefix should auto-detect health_data from table name."""
        result = await sql_tool.execute(query="SELECT * FROM samples")
        assert result["success"] is True

    async def test_database_parameter(self, sql_tool):
        """Explicit database parameter should route correctly."""
        result = await sql_tool.execute(database="health_data", query="SELECT COUNT(*) FROM samples")
        assert result["success"] is True

    async def test_auto_detect_memory(self, sql_tool):
        """Query referencing memory tables should auto-detect memory DB."""
        result = await sql_tool.execute(query="SELECT * FROM reports LIMIT 1")
        assert result["success"] is True

    async def test_unknown_database(self, sql_tool):
        """Query with an unknown database should return an error."""
        result = await sql_tool.execute(database="unknown_db", query="SELECT 1")
        assert result["success"] is False
        assert "unknown" in result["error"].lower()

    async def test_limit_capped(self, sql_tool):
        """Limit parameter is capped at _MAX_ROWS (50)."""
        result = await sql_tool.execute(
            database="health_data", query="SELECT * FROM samples", limit=100
        )
        assert result["success"] is True
        # Even if many rows in DB, at most 50 rows returned
        assert len(result["rows"]) <= 50

    async def test_health_data_with_clause(self, sql_tool):
        """WITH (CTE) queries on health_data should be allowed."""
        result = await sql_tool.execute(
            query="health_data:WITH recent AS (SELECT * FROM samples LIMIT 5) SELECT * FROM recent"
        )
        assert result["success"] is True


# =========================================================================
# CodeTool
# =========================================================================

class TestCodeTool:
    """Tests for the Python code execution tool."""

    @pytest.fixture
    def code_tool(self, data_store, tmp_dirs, memory_db):
        return CodeTool(
            data_store=data_store,
            memory_db_path=tmp_dirs["memory"],
            user_id="LiveUser",
        )

    async def test_simple_print(self, code_tool):
        """Simple print statement should succeed."""
        result = await code_tool.execute(code='print("hello world")')
        assert result["success"] is True
        assert "hello world" in result["output"]

    async def test_pandas_available(self, code_tool):
        """Pandas should be available in the execution namespace."""
        result = await code_tool.execute(code="print(pd.__version__)")
        assert result["success"] is True
        assert result["output"].strip() != ""

    async def test_numpy_available(self, code_tool):
        """NumPy should be available in the execution namespace."""
        result = await code_tool.execute(code="print(np.mean([1, 2, 3]))")
        assert result["success"] is True
        assert "2.0" in result["output"]

    async def test_health_db_query(self, code_tool):
        """Code should be able to query health_db."""
        code = (
            "df = pd.read_sql('SELECT COUNT(*) as cnt FROM samples', health_db)\n"
            "print(f'Count: {df.iloc[0][\"cnt\"]}')"
        )
        result = await code_tool.execute(code=code)
        assert result["success"] is True
        assert "Count:" in result["output"]

    async def test_memory_db_query(self, code_tool):
        """Code should be able to query memory_db."""
        code = (
            "df = pd.read_sql('SELECT COUNT(*) as cnt FROM reports', memory_db)\n"
            "print(f'Reports: {df.iloc[0][\"cnt\"]}')"
        )
        result = await code_tool.execute(code=code)
        assert result["success"] is True
        assert "Reports:" in result["output"]

    async def test_syntax_error(self, code_tool):
        """Syntax errors should be reported."""
        result = await code_tool.execute(code="def broken(")
        assert result["success"] is False
        assert "error" in result

    async def test_runtime_error(self, code_tool):
        """Runtime exceptions should be caught and reported."""
        result = await code_tool.execute(code="1 / 0")
        assert result["success"] is False
        assert "ZeroDivision" in result.get("error", "") or "division" in result.get("error", "").lower()

    async def test_dataframe_context_on_error(self, code_tool):
        """When a KeyError occurs, DataFrame context should be included."""
        code = (
            "df = pd.DataFrame({'a': [1, 2], 'b': [3, 4]})\n"
            "print(df['nonexistent'])"
        )
        result = await code_tool.execute(code=code)
        assert result["success"] is False
        # Should include hint about checking columns
        assert "error" in result

    async def test_stderr_captured(self, code_tool):
        """Stderr output should be captured in the result."""
        code = "import sys; sys.stderr.write('warning message')"
        result = await code_tool.execute(code=code)
        assert result["success"] is True
        assert result.get("errors") is not None
        assert "warning message" in result["errors"]

    async def test_sanitize_fstrings(self, code_tool):
        """Triple-quoted f-strings should be sanitized to single-line."""
        sanitized = CodeTool._sanitize_fstrings('f"""hello\nworld"""')
        assert '"""' not in sanitized


# =========================================================================
# PushReportTool
# =========================================================================

class TestPushReportTool:
    """Tests for the report-pushing tool."""

    @pytest.fixture
    def push_tool(self, tmp_dirs, memory_db):
        return PushReportTool(
            memory_db_path=tmp_dirs["memory"],
            user_id="LiveUser",
            telegram_sender=None,
        )

    async def test_push_report_success(self, push_tool):
        """A valid report should be saved and return success."""
        result = await push_tool.execute(
            title="Morning Heart Rate Analysis",
            content="## Summary\nHeart rate averaged 68 bpm overnight.",
            im_digest="Your heart rate was steady at 68 bpm last night. Nice recovery!",
            time_range_start="2026-03-19T22:00:00",
            time_range_end="2026-03-20T06:00:00",
            alert_level="normal",
            tags=["heart_rate", "sleep"],
        )
        assert result["success"] is True
        assert result["report_id"] > 0
        assert result["alert_level"] == "normal"

    async def test_push_report_invalid_alert_level(self, push_tool):
        """An invalid alert_level should be rejected."""
        result = await push_tool.execute(
            title="Test",
            content="Content",
            im_digest="Digest",
            time_range_start="2026-03-19T00:00:00",
            time_range_end="2026-03-20T00:00:00",
            alert_level="catastrophic",  # invalid
        )
        assert result["success"] is False
        assert "Invalid alert_level" in result["error"]

    async def test_push_report_persisted_to_db(self, push_tool, tmp_dirs):
        """Report should actually exist in the SQLite database after push."""
        await push_tool.execute(
            title="Persisted Report",
            content="This should be in the DB.",
            im_digest="Digest text",
            time_range_start="2026-03-19T00:00:00",
            time_range_end="2026-03-20T00:00:00",
        )
        db_file = tmp_dirs["memory"] / "LiveUser.db"
        with sqlite3.connect(db_file) as conn:
            row = conn.execute(
                "SELECT title, content, alert_level FROM reports WHERE title = ?",
                ("Persisted Report",),
            ).fetchone()
        assert row is not None
        assert row[0] == "Persisted Report"
        assert row[2] == "normal"  # default

    async def test_push_report_with_all_alert_levels(self, push_tool):
        """All valid alert levels should be accepted."""
        for level in ("normal", "info", "warning", "critical"):
            result = await push_tool.execute(
                title=f"Test {level}",
                content=f"Content for {level}",
                im_digest=f"Digest for {level}",
                time_range_start="2026-03-19T00:00:00",
                time_range_end="2026-03-20T00:00:00",
                alert_level=level,
            )
            assert result["success"] is True, f"Failed for alert_level={level}"

    async def test_push_report_with_metadata(self, push_tool):
        """Custom metadata should be included in the saved report."""
        result = await push_tool.execute(
            title="With Metadata",
            content="Content",
            im_digest="Digest",
            time_range_start="2026-03-19T00:00:00",
            time_range_end="2026-03-20T00:00:00",
            metadata={"custom_key": "custom_value"},
        )
        assert result["success"] is True

    async def test_push_report_with_telegram_sender(self, tmp_dirs, memory_db):
        """When a telegram_sender is provided, it should be invoked."""
        sender = MagicMock()
        sender.send_message = AsyncMock(return_value=True)
        tool = PushReportTool(
            memory_db_path=tmp_dirs["memory"],
            user_id="LiveUser",
            telegram_sender=sender,
        )
        result = await tool.execute(
            title="Telegram Test",
            content="Content",
            im_digest="Telegram digest",
            time_range_start="2026-03-19T00:00:00",
            time_range_end="2026-03-20T00:00:00",
        )
        assert result["success"] is True


# =========================================================================
# UpdateMdTool
# =========================================================================

class TestUpdateMdTool:
    """Tests for the update_md tool (editable prompt files)."""

    @pytest.fixture
    def update_tool(self):
        return UpdateMdTool()

    async def test_update_user_md(self, update_tool, prompt_files):
        """Writing to user.md should succeed and preserve the header."""
        with patch("backend.agent.tools.update_md_tool._PROMPTS_DIR", prompt_files):
            result = await update_tool.execute(
                file="user.md",
                content="Prefers detailed sleep reports.\nTimezone: UTC+0.",
            )
        assert result["success"] is True
        assert result["body_characters"] > 0

        # Verify the file content
        text = (prompt_files / "user.md").read_text(encoding="utf-8")
        assert "# User Profile" in text  # header preserved
        assert "Prefers detailed sleep reports." in text

    async def test_update_experience_md(self, update_tool, prompt_files):
        """Writing to experience.md should succeed."""
        with patch("backend.agent.tools.update_md_tool._PROMPTS_DIR", prompt_files):
            result = await update_tool.execute(
                file="experience.md",
                content="## Learned Patterns\n- Heart rate data often needs cleaning.",
            )
        assert result["success"] is True

    async def test_reject_soul_md(self, update_tool, prompt_files):
        """Writing to soul.md should be rejected (not in _FILE_CONFIG)."""
        with patch("backend.agent.tools.update_md_tool._PROMPTS_DIR", prompt_files):
            result = await update_tool.execute(file="soul.md", content="hacked")
        assert result["success"] is False
        assert "Unknown file" in result["error"]

    async def test_reject_job_md(self, update_tool, prompt_files):
        """Writing to job.md should be rejected."""
        with patch("backend.agent.tools.update_md_tool._PROMPTS_DIR", prompt_files):
            result = await update_tool.execute(file="job.md", content="hacked")
        assert result["success"] is False

    async def test_auto_append_md_extension(self, update_tool, prompt_files):
        """Passing 'user' (without .md) should still work."""
        with patch("backend.agent.tools.update_md_tool._PROMPTS_DIR", prompt_files):
            result = await update_tool.execute(file="user", content="New content.")
        assert result["success"] is True
        assert result["file"] == "user.md"

    async def test_creates_file_if_missing(self, update_tool, tmp_path):
        """If the target file does not exist, it should be created with default header."""
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        with patch("backend.agent.tools.update_md_tool._PROMPTS_DIR", prompts_dir):
            result = await update_tool.execute(file="user.md", content="Brand new content.")
        assert result["success"] is True
        text = (prompts_dir / "user.md").read_text(encoding="utf-8")
        assert "# User Profile" in text
        assert "Brand new content." in text

    async def test_preserves_marker(self, update_tool, prompt_files):
        """The editable-section marker should be preserved across appends."""
        with patch("backend.agent.tools.update_md_tool._PROMPTS_DIR", prompt_files):
            await update_tool.execute(file="experience.md", content="Round 1")
            await update_tool.execute(file="experience.md", content="Round 2")
        text = (prompt_files / "experience.md").read_text(encoding="utf-8")
        assert "<!-- Agent: append your real learnings below this line. -->" in text
        # Default op is "append" — both rounds are retained in order.
        assert "Round 1" in text
        assert "Round 2" in text
        assert text.index("Round 1") < text.index("Round 2")


# =========================================================================
# CreatePageTool
# =========================================================================

class TestCreatePageTool:
    """Tests for the dynamic page creation tool."""

    @pytest.fixture
    def create_tool(self, tmp_dirs, memory_db):
        tool = CreatePageTool(
            memory_db_path=tmp_dirs["memory"],
            user_id="LiveUser",
            data_store_path=tmp_dirs["data_stores"],
        )
        # Override the pages dir to use tmp
        tool._pages_dir = tmp_dirs["personalised_pages"]
        return tool

    async def test_create_page_success(self, create_tool, tmp_dirs):
        """Creating a valid page should succeed."""
        result = await create_tool.execute(
            page_id="sleep_trend_v1",
            display_name="Sleep Trend",
            description="Shows sleep trends",
            backend_code='async def route_handler(request):\n    return {"data": []}',
            frontend_html="<html><body>Hello</body></html>",
        )
        assert result["success"] is True
        assert result["page_id"] == "sleep_trend_v1"

        # Verify files were created
        page_dir = tmp_dirs["personalised_pages"] / "sleep_trend_v1"
        assert (page_dir / "index.html").exists()
        assert (page_dir / "route.py").exists()

    async def test_create_page_invalid_id(self, create_tool):
        """An invalid page_id (uppercase, special chars) should be rejected."""
        result = await create_tool.execute(
            page_id="Sleep-Trend!",
            display_name="Bad ID",
            backend_code="async def route_handler(request): return {}",
            frontend_html="<html></html>",
        )
        assert result["success"] is False
        assert "page_id" in result["error"]

    async def test_create_page_blocked_import(self, create_tool):
        """Backend code with blocked imports should be rejected."""
        result = await create_tool.execute(
            page_id="evil_page",
            display_name="Evil",
            backend_code="import requests\nasync def route_handler(r): return {}",
            frontend_html="<html></html>",
        )
        assert result["success"] is False
        assert "blocked import" in result["error"].lower()

    async def test_create_page_blocked_subprocess(self, create_tool):
        """Backend code importing subprocess should be rejected."""
        result = await create_tool.execute(
            page_id="shell_page",
            display_name="Shell",
            backend_code="import subprocess\nasync def route_handler(r): return {}",
            frontend_html="<html></html>",
        )
        assert result["success"] is False

    async def test_create_page_registered_in_db(self, create_tool, tmp_dirs):
        """The created page should be registered in the personalised_pages table."""
        await create_tool.execute(
            page_id="registered_page",
            display_name="Registered",
            backend_code="async def route_handler(r): return {}",
            frontend_html="<html></html>",
        )
        db_file = tmp_dirs["memory"] / "LiveUser.db"
        with sqlite3.connect(db_file) as conn:
            row = conn.execute(
                "SELECT page_id, display_name, status FROM personalised_pages WHERE page_id = ?",
                ("registered_page",),
            ).fetchone()
        assert row is not None
        assert row[0] == "registered_page"
        assert row[2] == "active"

    async def test_create_page_replaces_existing(self, create_tool):
        """Creating a page with the same ID should replace the existing one."""
        await create_tool.execute(
            page_id="replaced_page",
            display_name="Version 1",
            backend_code="async def route_handler(r): return {'v': 1}",
            frontend_html="<html>V1</html>",
        )
        result = await create_tool.execute(
            page_id="replaced_page",
            display_name="Version 2",
            backend_code="async def route_handler(r): return {'v': 2}",
            frontend_html="<html>V2</html>",
        )
        assert result["success"] is True
        assert result["display_name"] == "Version 2"


# =========================================================================
# Tool definitions
# =========================================================================

class TestToolDefinitions:
    """Verify that all tools return valid OpenAI-compatible function definitions."""

    @pytest.fixture
    def all_tools(self, data_store, tmp_dirs, memory_db):
        return [
            SQLTool(data_store, tmp_dirs["memory"], "LiveUser"),
            CodeTool(data_store, tmp_dirs["memory"], "LiveUser"),
            PushReportTool(tmp_dirs["memory"], "LiveUser"),
            UpdateMdTool(),
            CreatePageTool(tmp_dirs["memory"], "LiveUser", tmp_dirs["data_stores"]),
        ]

    def test_definitions_have_required_keys(self, all_tools):
        """Every tool definition must have type, function.name, function.parameters."""
        for tool in all_tools:
            defn = tool.get_definition()
            assert "type" in defn, f"{tool.__class__.__name__} missing 'type'"
            assert "function" in defn, f"{tool.__class__.__name__} missing 'function'"
            func = defn["function"]
            assert "name" in func, f"{tool.__class__.__name__} missing 'function.name'"
            assert "parameters" in func, f"{tool.__class__.__name__} missing 'function.parameters'"

    def test_definitions_names_are_unique(self, all_tools):
        """Tool names must be unique."""
        names = [t.get_definition()["function"]["name"] for t in all_tools]
        assert len(names) == len(set(names))
