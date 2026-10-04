"""
CreatePageTool — Agent tool to dynamically create and register new personalised pages.

The agent provides backend Python code (a FastAPI route function) and frontend
HTML. The tool:
1. Validates the code (no dangerous imports).
2. Syntax-checks the backend Python via compile().
3. Writes the frontend asset to disk.
4. Registers the backend route dynamically.
5. Records the page in the personalised_pages memory table.
6. Returns a success status so the agent can confirm to the user.
"""
import ast
import asyncio
import hashlib
import logging
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from .base import BaseTool

logger = logging.getLogger(__name__)

# Top-level modules agent-generated backend code may never import.  The
# framework already injects everything a page legitimately needs (sqlite3,
# json, statistics, datetime/timedelta, Path and the page_helpers functions),
# so this is a deny-list of the dangerous families: network, process/shell,
# filesystem mutation, reflection / dynamic loading, interpreter internals and
# the HIME backend itself (which would expose settings and API keys).
_BLOCKED_MODULES = frozenset([
    # Network / external API
    "anthropic", "openai", "requests", "httpx", "aiohttp", "urllib", "urllib3",
    "http", "socket", "ssl", "smtplib", "ftplib", "telnetlib", "imaplib",
    "poplib", "xmlrpc", "paramiko", "websocket", "websockets", "webbrowser",
    # Process / shell / OS
    "os", "sys", "subprocess", "pty", "pexpect", "signal", "posix", "nt",
    "multiprocessing", "threading", "_thread", "concurrent", "resource", "fcntl",
    # Filesystem destruction / file access helpers
    "shutil", "tempfile", "glob", "fileinput",
    # Reflection / dynamic code loading / interpreter internals
    "importlib", "ctypes", "cffi", "marshal", "dill", "pickle", "shelve",
    "builtins", "__builtin__", "inspect", "gc", "code", "codeop", "runpy",
    "pkgutil", "zipimport", "types", "imp",
    # The host application and its secrets
    "backend", "dotenv", "pydantic_settings",
])

# Bare names that mean "evaluate / reflect / reach the filesystem".
_BLOCKED_NAMES = frozenset([
    "eval", "exec", "compile", "__import__", "getattr", "setattr", "delattr",
    "globals", "locals", "vars", "open", "breakpoint", "input", "exit", "quit",
])

# Attribute names that are dangerous on *any* object: process execution,
# module-table access (``json.codecs``, ``x.modules``), and every
# ``pathlib.Path`` method that reads, writes, lists or deletes files (the
# preamble injects ``Path``, so ``Path(x).write_text(...)`` is otherwise open).
_BLOCKED_ATTRS = frozenset([
    "system", "popen", "fork", "forkpty", "execv", "execve", "execvp", "execl",
    "spawnv", "spawnl", "startfile", "rmtree",
    "codecs", "modules", "builtins", "os", "sys", "subprocess", "importlib",
    "socket", "load_extension", "enable_load_extension",
    "read_text", "read_bytes", "write_text", "write_bytes", "open", "unlink",
    "rmdir", "mkdir", "chmod", "touch", "symlink_to", "hardlink_to", "rename",
    "iterdir", "glob", "rglob",
])

# The only ``sqlite3.connect`` targets pages may use: the two framework-injected
# DB path variables, passed as a *bare name*.  An expression is not enough --
# ``HEALTH_DB_PATH + "_evil.db"`` mentions an allowed name but points elsewhere.
# Pages are told to use query_health / query_memory / write_memory instead.
_ALLOWED_DB_ARGS = frozenset(["HEALTH_DB_PATH", "MEMORY_DB_PATH"])

_CONNECT_MSG = (
    "sqlite3.connect() may only be called with the bare name "
    "HEALTH_DB_PATH or MEMORY_DB_PATH (the framework-injected "
    "database paths). Wrapping or concatenating them — "
    'str(HEALTH_DB_PATH), HEALTH_DB_PATH + "...", f-strings — '
    "is rejected. Prefer the query_health / query_memory / "
    "write_memory helpers over opening a connection yourself."
)


def validate_backend_code(code: str) -> str | None:
    """AST-check agent-generated page backend code.

    Returns an error message (suitable for the LLM) or ``None`` when the code
    passes.  Syntax errors return ``None`` here -- the compile step that
    follows reports them with line numbers.  Works on the parsed tree, so it
    cannot be fooled by ``import os`` + ``os.system(...)`` split across lines,
    aliases, string tricks or whitespace the way the old regex scan was.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None

    def _identifier_error(name: str) -> str:
        return (
            f"Backend code contains a blocked identifier: '{name}'. Use the "
            f"provided helpers (query_health, query_memory, write_memory, "
            f"ensure_table) instead of dynamic code evaluation, reflection "
            f"or file access."
        )

    def _import_error(name: str) -> str:
        return (
            f"Backend code contains blocked import: '{name}'. Agent-created pages "
            f"may not call external APIs, spawn processes, touch the filesystem "
            f"or reach into the host application."
        )

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in _BLOCKED_MODULES:
                    return _import_error(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import: reaches into the page's own package
                return _import_error("." * node.level + (node.module or ""))
            if (node.module or "").split(".")[0] in _BLOCKED_MODULES:
                return _import_error(node.module or "")
            for alias in node.names:
                if alias.name in _BLOCKED_NAMES or alias.name in _BLOCKED_MODULES:
                    return _import_error(f"{node.module}.{alias.name}")
        elif isinstance(node, ast.Name):
            if node.id in _BLOCKED_NAMES:
                return _identifier_error(node.id)
            if node.id.startswith("__") and node.id.endswith("__") and node.id != "__name__":
                return _identifier_error(node.id)
        elif isinstance(node, ast.Attribute):
            attr = node.attr
            if attr in _BLOCKED_ATTRS or attr in _BLOCKED_NAMES:
                return _identifier_error(attr)
            if attr.startswith("__") and attr.endswith("__"):
                return _identifier_error(attr)
        elif isinstance(node, ast.Call):
            fn = node.func
            callee = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
            if callee == "connect":
                first = node.args[0] if node.args else None
                if not (isinstance(first, ast.Name) and first.id in _ALLOWED_DB_ARGS):
                    return _CONNECT_MSG
    return None


# Deduplication: {page_id: (timestamp, content_hash)}.  Only an *identical*
# re-submission inside the window is treated as the LLM repeating itself; a
# changed payload is a real update and must be written.
_recent_creations: dict[str, tuple[float, str]] = {}
_DEDUP_WINDOW_SECS = 60.0


def _content_hash(display_name: str, description: str, backend_code: str, frontend_html: str) -> str:
    h = hashlib.sha256()
    for part in (display_name, description, backend_code, frontend_html):
        h.update(part.encode("utf-8", "replace"))
        h.update(b"\x00")
    return h.hexdigest()


_SCHEMA_DESCRIPTION = """
Create a new personalised page that will appear as a screen in the iOS app.
Provide:
- page_id: unique slug, e.g. "sleep_trend_v1"
- display_name: human-readable title shown on the screen
- description: what this page does
- backend_code: Python code defining `route_handler(request)` that returns a dict.
  The framework auto-injects `HEALTH_DB_PATH` and `MEMORY_DB_PATH` as global str variables.
  Available imports: sqlite3, json, datetime, pathlib.
  NO AI/LLM calls, NO external HTTP requests.
- frontend_html: complete single-page HTML that fetches data from
  /api/personalised-pages/{page_id}/data and renders it. Use vanilla JS only.
"""


class CreatePageTool(BaseTool):
    """Dynamically create and register a new personalised page."""

    name = "create_page"

    def __init__(self, memory_db_path: Path, user_id: str, data_store_path: Path = None):
        self.memory_db_path = Path(memory_db_path)
        self.user_id = user_id
        self.data_store_path = data_store_path
        _project_root = Path(__file__).resolve().parent.parent.parent.parent
        self._pages_dir = _project_root / "data" / "personalised_pages"
        self._pages_dir.mkdir(parents=True, exist_ok=True)

    # Relative path from project root to page_helpers.py (portable across moves)
    _HELPERS_REL = "backend/agent/tools/page_helpers.py"

    def get_definition(self) -> dict[str, Any]:
        return self._get_definition_from_json("create_page")

    def _build_route_source(self, page_id: str, db_path: Path, backend_code: str) -> str:
        """Build the full route.py source with preamble. Uses __file__-relative path for helpers."""
        _project_root = Path(__file__).resolve().parent.parent.parent.parent
        helpers_abs = _project_root / self._HELPERS_REL
        return (
            f"# Auto-generated by CreatePageTool for page_id={page_id!r}\n"
            f"import sqlite3, json, statistics\nfrom datetime import datetime, timedelta\nfrom pathlib import Path\n"
            f"HEALTH_DB_PATH = {str(db_path / (self.user_id + '_data.db'))!r}\n"
            f"MEMORY_DB_PATH = {str(self.memory_db_path / (self.user_id + '.db'))!r}\n\n"
            f"# --- Page Helpers (query_health, query_memory, write_memory, ensure_table, etc.) ---\n"
            f"# Resolve helpers relative to this file's project root for portability\n"
            f"_helpers_path = Path(__file__).resolve().parent.parent.parent.parent / {self._HELPERS_REL!r}\n"
            f"if not _helpers_path.exists():\n"
            f"    _helpers_path = Path({str(helpers_abs)!r})  # fallback to absolute\n"
            f"exec(open(str(_helpers_path)).read())\n\n"
            + backend_code
        )

    async def execute(self, page_id: str, display_name: str, backend_code: str,
                      frontend_html: str, description: str = "",
                      patch: bool = False) -> dict[str, Any]:
        # 0. Deduplication — prevent the LLM from creating the same page multiple times in one turn.
        #    Only an identical payload counts as a duplicate (skipped for patch=True — patching
        #    is an intentional update).
        now = time.time()
        digest = _content_hash(display_name, description, backend_code, frontend_html)
        if not patch:
            recent = _recent_creations.get(page_id)
            if recent is not None and (now - recent[0]) < _DEDUP_WINDOW_SECS and recent[1] == digest:
                logger.info("create_page: skipping duplicate creation of '%s' (created %.0fs ago)", page_id, now - recent[0])
                return {
                    "success": True,
                    "page_id": page_id,
                    "display_name": display_name,
                    "frontend_url": f"/api/personalised-pages/{page_id}/",
                    "data_url": f"/api/personalised-pages/{page_id}/data",
                    "message": f"Page '{display_name}' already exists with identical content (created moments ago). No duplicate created.",
                }

        # 1. Validate page_id format. This MUST run before the patch branch —
        #    otherwise ``patch=True`` with a traversing page_id ("../../x")
        #    writes index.html / route.py outside the pages directory.
        if not re.match(r'^[a-z0-9_]{1,64}$', page_id):
            return {"success": False, "error": "page_id must be lowercase alphanumeric/underscore, max 64 chars"}

        # 1a. Defence in depth: the resolved directory must stay inside the
        #     pages root even if the regex above is ever loosened.
        page_dir = self._pages_dir / page_id
        try:
            page_dir.resolve().relative_to(self._pages_dir.resolve())
        except ValueError:
            return {"success": False, "error": "page_id resolves outside the personalised pages directory"}

        # 2. Security: AST-check the backend code (imports, reflection,
        #    filesystem access, sqlite3.connect targets).
        if backend_code:
            sec_error = validate_backend_code(backend_code)
            if sec_error:
                return {"success": False, "error": sec_error}

        # 2c. Patch mode: update existing page preserving unmodified parts.
        #     Runs *after* validation so a patch cannot bypass the page_id and
        #     sandbox checks above.
        if patch:
            if await asyncio.to_thread(page_dir.exists):
                return await self._patch_page(
                    page_id, display_name, description, backend_code, frontend_html
                )
            # Page doesn't exist yet — fall through to normal creation

        # 2b. Syntax-check the backend Python code before writing to disk
        #     Build the full source that will actually be saved (with preamble).
        db_path = self.data_store_path or Path("data/data_stores")
        route_source = self._build_route_source(page_id, db_path, backend_code)
        try:
            compile(route_source, f"<personalised_page:{page_id}/route.py>", "exec")
        except SyntaxError as se:
            logger.warning("create_page: syntax error in backend_code for '%s': %s (line %s)", page_id, se.msg, se.lineno)
            return {
                "success": False,
                "error": (
                    f"The backend Python code has a syntax error: {se.msg} at line {se.lineno}. "
                    "Please fix the code and try again."
                ),
            }

        # 3-5. Write frontend + backend files and register the page. Blocking
        #      file + SQLite I/O runs in a worker thread, off the event loop.
        try:
            await asyncio.to_thread(
                self._write_page_sync,
                page_id, display_name, description, route_source, frontend_html,
            )
        except Exception as e:
            logger.error("create_page: write/DB error: %s", e)
            return {"success": False, "error": f"DB registration failed: {e}"}

        # 6. Record creation time for deduplication
        _recent_creations[page_id] = (time.time(), digest)

        logger.info("create_page: registered '%s' (%s)", page_id, display_name)
        return {
            "success": True,
            "page_id": page_id,
            "display_name": display_name,
            "frontend_url": f"/api/personalised-pages/{page_id}/",
            "data_url": f"/api/personalised-pages/{page_id}/data",
            "message": f"Page '{display_name}' created. It will appear as a new page in the iOS app."
        }

    def _write_page_sync(
        self, page_id: str, display_name: str, description: str,
        route_source: str, frontend_html: str,
    ) -> None:
        """Write index.html + route.py and upsert the personalised_pages row."""
        page_dir = self._pages_dir / page_id
        page_dir.mkdir(parents=True, exist_ok=True)
        html_path = page_dir / "index.html"
        html_path.write_text(frontend_html, encoding="utf-8")
        (page_dir / "route.py").write_text(route_source, encoding="utf-8")

        db_file = self.memory_db_path / f"{self.user_id}.db"
        with sqlite3.connect(str(db_file), timeout=10) as conn:
            conn.execute(
                """INSERT INTO personalised_pages
                   (page_id, display_name, description, backend_route, frontend_asset, status)
                   VALUES (?, ?, ?, ?, ?, 'active')
                   ON CONFLICT(page_id) DO UPDATE SET
                       display_name = excluded.display_name,
                       description = excluded.description,
                       backend_route = excluded.backend_route,
                       frontend_asset = excluded.frontend_asset,
                       status = 'active'""",
                (
                    page_id,
                    display_name,
                    description,
                    f"/api/personalised-pages/{page_id}/data",
                    str(html_path),
                ),
            )
            conn.commit()

    async def _patch_page(
        self,
        page_id: str,
        display_name: str,
        description: str,
        backend_code: str,
        frontend_html: str,
    ) -> dict[str, Any]:
        """Update an existing page preserving parts that are not provided."""
        page_dir = self._pages_dir / page_id
        html_path = page_dir / "index.html"
        route_path = page_dir / "route.py"

        # Read existing files so we can preserve unchanged parts
        def _read_existing() -> tuple[str, str]:
            return (
                html_path.read_text(encoding="utf-8") if html_path.exists() else "",
                route_path.read_text(encoding="utf-8") if route_path.exists() else "",
            )

        existing_html, existing_route = await asyncio.to_thread(_read_existing)

        new_html = frontend_html if frontend_html else existing_html
        new_backend = backend_code if backend_code else ""

        # If neither existing nor new content is available, report an error
        if not new_html and not existing_html:
            return {"success": False, "error": f"Cannot patch page '{page_id}': no existing frontend HTML and none provided."}
        if not new_backend and not existing_route:
            return {"success": False, "error": f"Cannot patch page '{page_id}': no existing backend route and none provided."}

        route_source: str | None = None
        if new_backend:
            # Rebuild full route source and syntax-check it
            db_path = self.data_store_path or Path("data/data_stores")
            route_source = self._build_route_source(page_id, db_path, new_backend)
            try:
                compile(route_source, f"<personalised_page:{page_id}/route.py>", "exec")
            except SyntaxError as se:
                logger.warning("create_page patch: syntax error in backend_code for '%s': %s (line %s)", page_id, se.msg, se.lineno)
                return {
                    "success": False,
                    "error": f"Patched backend code has a syntax error: {se.msg} at line {se.lineno}. Fix and retry.",
                }

        def _apply() -> None:
            if route_source is not None:
                route_path.write_text(route_source, encoding="utf-8")
            html_path.write_text(new_html, encoding="utf-8")
            # DB UPDATE — only overwrite non-empty fields
            db_file = self.memory_db_path / f"{self.user_id}.db"
            with sqlite3.connect(str(db_file), timeout=10) as conn:
                updates: list = []
                params: list = []
                if display_name:
                    updates.append("display_name = ?")
                    params.append(display_name)
                if description:
                    updates.append("description = ?")
                    params.append(description)
                if updates:
                    params.append(page_id)
                    conn.execute(
                        f"UPDATE personalised_pages SET {', '.join(updates)} WHERE page_id = ?",
                        params,
                    )
                    conn.commit()

        try:
            await asyncio.to_thread(_apply)
        except Exception as e:
            logger.error("create_page patch: write/DB error: %s", e)
            return {"success": False, "error": f"DB update failed: {e}"}

        _recent_creations[page_id] = (
            time.time(), _content_hash(display_name, description, backend_code, frontend_html),
        )
        effective_name = display_name or page_id
        logger.info("create_page: patched '%s' (%s)", page_id, effective_name)
        return {
            "success": True,
            "page_id": page_id,
            "display_name": effective_name,
            "frontend_url": f"/api/personalised-pages/{page_id}/",
            "data_url": f"/api/personalised-pages/{page_id}/data",
            "message": f"Page '{effective_name}' patched successfully.",
        }
