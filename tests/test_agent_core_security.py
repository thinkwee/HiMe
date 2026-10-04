"""Regression tests for the agent-core bug sweep: tool-level security.

Covers the SQL authorizers and role-aware memory access, AST-based page
validation, the chart-path gate, per-flow tool state and a few tool I/O fixes.
All fast, no network.
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.agent.tools import paths as paths_mod
from backend.agent.tools.base import BaseTool, _FlowVar, tool_role
from backend.agent.tools.create_page_tool import CreatePageTool, validate_backend_code
from backend.agent.tools.paths import ChartPathError, resolve_chart_path
from backend.agent.tools.sql_tool import SQLTool

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


# ---------------------------------------------------------------------------
# SQL tool: authorizers + roles
# ---------------------------------------------------------------------------

@pytest.fixture
def sql_tool(data_store, tmp_dirs, memory_db):
    return SQLTool(data_store, tmp_dirs["memory"], "LiveUser")


class TestSqlHealthAuthorizer:
    async def test_recursive_cte_allowed_on_health_data(self, sql_tool):
        """Code 33 is SQLITE_RECURSIVE, not VACUUM: WITH RECURSIVE must work."""
        r = await sql_tool.execute(
            query="WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i+1 FROM n WHERE i<5) "
                  "SELECT i FROM n",
            database="health_data",
        )
        assert r["success"] is True, r
        assert r["row_count"] == 5

    async def test_writes_still_denied(self, sql_tool):
        for q in (
            "WITH x AS (SELECT 1) INSERT INTO samples VALUES ('a','b',1)",
            "SELECT 1; DROP TABLE samples",
        ):
            r = await sql_tool.execute(query=q, database="health_data")
            assert r["success"] is False

    def test_authorizer_is_allow_list(self):
        deny = sqlite3.SQLITE_DENY
        ok = sqlite3.SQLITE_OK
        auth = SQLTool._readonly_authorizer
        assert auth(33, None, None, "main", None) == ok          # RECURSIVE
        assert auth(20, "samples", "value", "main", None) == ok  # READ
        assert auth(21, None, None, None, None) == ok            # SELECT
        for code in (1, 2, 5, 9, 10, 15, 18, 23, 24, 25, 26, 27, 29):
            assert auth(code, "t", None, "main", None) == deny, code
        assert auth(19, "writable_schema", "1", "main", None) == deny  # PRAGMA


class TestMemoryRoles:
    async def test_analysis_role_cannot_write_memory(self, sql_tool, tmp_dirs):
        with tool_role("analysis"):
            for q in (
                "INSERT INTO scheduled_tasks (cron_expr, prompt_goal) VALUES ('* * * * *','x')",
                "DROP TABLE reports",
                "CREATE TABLE evil (a)",
                "PRAGMA writable_schema=1",
                "VACUUM INTO '/tmp/evil_copy.db'",
                "ATTACH DATABASE '/tmp/evil_attach.db' AS e",
                "UPDATE reports SET title='x'",
            ):
                r = await sql_tool.execute(query=q, database="memory")
                assert r["success"] is False, q
                assert "read-only" in r["error"].lower() or "not allowed" in r["error"].lower(), r

    async def test_analysis_role_can_read_memory_and_schema(self, sql_tool):
        with tool_role("analysis"):
            r = await sql_tool.execute(query="SELECT name FROM sqlite_master WHERE type='table'", database="memory")
            assert r["success"] is True and r["row_count"] > 0
            r = await sql_tool.execute(query="PRAGMA table_info(reports)", database="memory")
            assert r["success"] is True and r["row_count"] > 0
            r = await sql_tool.execute(query="SELECT COUNT(*) AS n FROM trigger_rules")
            assert r["success"] is True

    @pytest.mark.parametrize("role", ["manage", "plan"])
    async def test_write_roles_can_write_but_not_escape(self, sql_tool, tmp_dirs, role):
        with tool_role(role):
            r = await sql_tool.execute(
                query="INSERT INTO scheduled_tasks (cron_expr, prompt_goal) VALUES ('0 8 * * *','goal')",
                database="memory",
            )
            assert r["success"] is True, r
            r = await sql_tool.execute(query="CREATE TABLE IF NOT EXISTS my_notes (a TEXT)", database="memory")
            assert r["success"] is True, r
            r = await sql_tool.execute(query="DROP TABLE my_notes", database="memory")
            assert r["success"] is True, r
            # Even a writer: no ATTACH / PRAGMA writes / VACUUM / catalogue writes
            for q in (
                "ATTACH DATABASE '/tmp/evil_attach.db' AS e",
                "PRAGMA writable_schema=1",
                "VACUUM INTO '/tmp/evil_copy.db'",
                "/* c */ VACUUM",
                "INSERT INTO sqlite_master (type,name) VALUES ('table','x')",
                "DROP TABLE reports",
            ):
                r = await sql_tool.execute(query=q, database="memory")
                assert r["success"] is False, q
        # The schema is intact and nothing leaked to disk
        with sqlite3.connect(tmp_dirs["memory"] / "LiveUser.db") as conn:
            assert conn.execute("SELECT COUNT(*) FROM reports").fetchone() is not None
        assert not os.path.exists("/tmp/evil_copy.db")

    async def test_all_memory_tables_autodetect_to_memory(self, sql_tool):
        for tbl in ("trigger_rules", "chat_history", "message_evidence", "onboarding_survey"):
            assert SQLTool._auto_detect_db(f"SELECT * FROM {tbl}") == "memory", tbl

    async def test_code_tool_memory_db_is_read_only(self, data_store, tmp_dirs, memory_db):
        from backend.agent.tools.code_tool import CodeTool
        tool = CodeTool(data_store, tmp_dirs["memory"], "LiveUser")
        r = await tool.execute("memory_db.execute('CREATE TABLE zz (a)')")
        assert r["success"] is False
        assert "readonly" in (r.get("error", "") + r.get("output", "")).lower()
        r = await tool.execute("print(memory_db.execute('SELECT COUNT(*) FROM reports').fetchone())")
        assert r["success"] is True


# ---------------------------------------------------------------------------
# create_page: AST validation
# ---------------------------------------------------------------------------

class TestPageAstValidation:
    GOOD = (
        "import math\n"
        "from collections import defaultdict\n"
        "def route_handler(request):\n"
        "    d = defaultdict(list)\n"
        "    hr = query_health('heart_rate', days=7)\n"
        "    con = sqlite3.connect(HEALTH_DB_PATH)\n"
        "    return {'n': len(hr), 'x': math.sqrt(4), 'when': datetime.now().isoformat()}\n"
    )

    def test_good_page_passes(self):
        assert validate_backend_code(self.GOOD) is None

    @pytest.mark.parametrize("code", [
        "import os\nos.system('id')",
        "import os as o\no.popen('id')",
        "from os import system\nsystem('id')",
        "import  subprocess",
        "import shutil\nshutil.rmtree('/')",
        "from shutil import rmtree",
        "import importlib",
        "import builtins",
        "import backend.config",
        "from backend import config",
        "from . import x",
        "import sys",
        "import socket",
        "import pickle",
        "import threading",
    ])
    def test_dangerous_imports_blocked(self, code):
        err = validate_backend_code(code)
        assert err and "blocked import" in err, (code, err)

    @pytest.mark.parametrize("code", [
        "__import__('os').system('id')",
        "eval('1')",
        "exec('x=1')",
        "compile('1','a','eval')",
        "open('/etc/passwd')",
        "getattr(sqlite3, 'conn' + 'ect')('/tmp/x')",
        "().__class__.__bases__[0].__subclasses__()",
        "x = sqlite3.__builtins__",
        "globals()",
        "json.codecs.open('/etc/passwd')",
        "Path('/etc/passwd').read_text()",
        "Path('/tmp/x').write_text('y')",
        "Path('/tmp').iterdir()",
        "datetime.os.system('id')",
    ])
    def test_reflection_and_fs_blocked(self, code):
        err = validate_backend_code("def route_handler(r):\n    " + code.replace("\n", "\n    "))
        assert err and "blocked identifier" in err, (code, err)

    @pytest.mark.parametrize("arg", [
        'HEALTH_DB_PATH + "_evil.db"', 'str(HEALTH_DB_PATH)', 'f"{MEMORY_DB_PATH}x"',
        '"/etc/passwd"', "other", "",
    ])
    def test_connect_requires_bare_injected_name(self, arg):
        err = validate_backend_code(f"def route_handler(r):\n    s = sqlite3.connect({arg})")
        assert err and "sqlite3.connect" in err

    def test_aliased_connect_is_caught(self):
        err = validate_backend_code("import sqlite3 as s\ndef route_handler(r):\n    s.connect('/tmp/x')")
        assert err and "sqlite3.connect" in err

    def test_syntax_error_deferred_to_compile(self):
        assert validate_backend_code("def broken(:") is None


class TestPageDedupe:
    @pytest.fixture
    def create_tool(self, tmp_dirs, memory_db):
        tool = CreatePageTool(tmp_dirs["memory"], "LiveUser", tmp_dirs["data_stores"])
        tool._pages_dir = tmp_dirs["personalised_pages"]
        return tool

    async def test_changed_payload_is_written_not_deduped(self, create_tool, tmp_dirs):
        kw = dict(page_id="dedupe_page_v1", display_name="P", backend_code="def route_handler(r): return {}")
        a = await create_tool.execute(frontend_html="<p>one</p>", **kw)
        assert a["success"] is True
        b = await create_tool.execute(frontend_html="<p>two</p>", **kw)
        assert b["success"] is True
        html = (tmp_dirs["personalised_pages"] / "dedupe_page_v1" / "index.html").read_text()
        assert html == "<p>two</p>", "a changed payload must not be swallowed by the dedupe window"

    async def test_identical_payload_is_deduped(self, create_tool):
        kw = dict(page_id="dedupe_page_v2", display_name="P", backend_code="def route_handler(r): return {}",
                  frontend_html="<p>same</p>")
        await create_tool.execute(**kw)
        again = await create_tool.execute(**kw)
        assert again["success"] is True and "identical" in again["message"]

    async def test_os_system_bypass_rejected_end_to_end(self, create_tool):
        r = await create_tool.execute(
            page_id="evil_os", display_name="E", frontend_html="<p/>",
            backend_code="import os\ndef route_handler(r):\n    os.system('id')\n    return {}",
        )
        assert r["success"] is False and "blocked import" in r["error"]


# ---------------------------------------------------------------------------
# Chart-path gate
# ---------------------------------------------------------------------------

class TestChartPaths:
    def test_valid_png_in_temp_dir(self, tmp_path):
        p = tmp_path / "chart.png"
        p.write_bytes(PNG)
        assert resolve_chart_path(str(p)) == p.resolve()

    def test_text_file_with_image_extension_rejected(self, tmp_path):
        p = tmp_path / "secret.png"
        p.write_text("not an image: API_KEY=xyz")
        with pytest.raises(ChartPathError):
            resolve_chart_path(str(p))

    def test_non_image_extension_rejected(self, tmp_path):
        p = tmp_path / "data.txt"
        p.write_bytes(PNG)
        with pytest.raises(ChartPathError):
            resolve_chart_path(str(p))

    def test_outside_root_rejected(self, tmp_path):
        p = tmp_path / "chart.png"
        p.write_bytes(PNG)
        other_root = tmp_path / "elsewhere"
        other_root.mkdir()
        with pytest.raises(ChartPathError):
            resolve_chart_path(str(p), roots=[other_root.resolve()])

    def test_symlink_escape_rejected(self, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        target = outside / "real.png"
        target.write_bytes(PNG)
        root = tmp_path / "root"
        root.mkdir()
        link = root / "link.png"
        link.symlink_to(target)
        with pytest.raises(ChartPathError):
            resolve_chart_path(str(link), roots=[root.resolve()])

    def test_missing_and_oversized(self, tmp_path):
        with pytest.raises(ChartPathError):
            resolve_chart_path(str(tmp_path / "nope.png"))
        big = tmp_path / "big.png"
        big.write_bytes(PNG + b"\x00" * 2000)
        with pytest.raises(ChartPathError):
            resolve_chart_path(str(big), max_bytes=1000)

    def test_roots_include_tempdir(self):
        import tempfile
        assert Path(tempfile.gettempdir()).resolve() in paths_mod.allowed_chart_roots()

    async def test_reply_user_never_sends_unsafe_path(self, tmp_path):
        from backend.agent.tools.reply_user_tool import ReplyUserTool
        gw = MagicMock()
        gw.channel.value = "telegram"
        gw.default_chat_id = "1"
        gw.send_photo = AsyncMock(return_value=True)
        gw.send_message = AsyncMock(return_value=True)
        registry = MagicMock()
        registry.for_envelope.return_value = gw
        registry.all.return_value = [gw]
        tool = ReplyUserTool(gateway_registry=registry)

        evil = tmp_path / "evil.png"
        evil.write_text("secret")
        res = await tool.execute(message="hi", chat_id="1", image_path=str(evil))
        gw.send_photo.assert_not_called()
        gw.send_message.assert_awaited_once()
        assert res["success"] is True and "image_warning" in res

        res = await tool.execute(message="hi", chat_id="1", image_path="/etc/passwd")
        gw.send_photo.assert_not_called()

        good = tmp_path / "ok.png"
        good.write_bytes(PNG)
        res = await tool.execute(message="hi", chat_id="1", image_path=str(good))
        gw.send_photo.assert_awaited_once()
        assert gw.send_photo.await_args.kwargs["photo_path"] == str(good.resolve())

    def test_push_report_embeds_only_valid_images(self, tmp_path):
        from backend.agent.tools.push_report_tool import _embed_local_charts
        good = tmp_path / "g.png"
        good.write_bytes(PNG)
        bad = tmp_path / "b.png"
        bad.write_text("secret")
        out = _embed_local_charts(f"![a]({good}) ![b]({bad}) ![c](/etc/passwd)", None)
        assert "data:image/png;base64," in out
        assert str(bad) in out and "/etc/passwd" in out  # left untouched, not inlined
        assert out.count("data:") == 1


# ---------------------------------------------------------------------------
# Per-flow tool state
# ---------------------------------------------------------------------------

class _DummyTool(BaseTool):
    name = "dummy"
    state: list = _FlowVar([])

    def get_definition(self) -> dict:
        return {}

    async def execute(self, **kwargs):
        return {}


async def test_flow_state_is_isolated_between_tasks():
    tool = _DummyTool()
    seen: dict[str, list] = {}

    async def flow(tag: str, delay: float) -> None:
        tool.state = [tag]
        tool._current_tool_results = [{"tool": tag}]
        await asyncio.sleep(delay)
        seen[tag] = [tool.state[0], tool._current_tool_results[0]["tool"]]

    await asyncio.gather(flow("a", 0.05), flow("b", 0.01))
    assert seen == {"a": ["a", "a"], "b": ["b", "b"]}


def test_tool_definitions_are_cached_and_copied():
    from backend.agent.tools.base import load_tool_definitions
    assert load_tool_definitions() is load_tool_definitions()
    t = _DummyTool()
    d1 = t._get_definition_from_json("sql")
    d1["function"]["name"] = "mutated"
    assert t._get_definition_from_json("sql")["function"]["name"] == "sql"


# ---------------------------------------------------------------------------
# update_md: atomic + locked
# ---------------------------------------------------------------------------

async def test_update_md_concurrent_appends_do_not_lose_writes(tmp_path, monkeypatch):
    from backend.agent.tools import update_md_tool
    monkeypatch.setattr(update_md_tool, "_PROMPTS_DIR", tmp_path)
    tool = update_md_tool.UpdateMdTool()
    await asyncio.gather(*[
        tool.execute(file="user.md", op="append", content=f"note-{i}") for i in range(25)
    ])
    text = (tmp_path / "user.md").read_text()
    for i in range(25):
        assert f"note-{i}" in text
    assert not list(tmp_path.glob("*.tmp"))
