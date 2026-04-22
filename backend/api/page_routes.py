"""
Personalised-page routes — serve dynamically created page frontends and data endpoints.

Pages are registered in the personalised_pages memory table.
Each page has:
- GET  /api/personalised-pages/{page_id}/       → serves the frontend HTML
- GET  /api/personalised-pages/{page_id}/data   → runs the backend route_handler (GET)
- POST /api/personalised-pages/{page_id}/data   → runs the backend route_handler (POST)
- GET  /api/personalised-pages/list            → lists all active pages
- DELETE /api/personalised-pages/{page_id}      → soft-delete a page
"""
import asyncio
import importlib.util
import logging
import re
import shutil
import sqlite3
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ..config import settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/personalised-pages", tags=["personalised-pages"])

_PAGES_DIR = Path("data/personalised_pages")
_SHARED_DIR = _PAGES_DIR / "_shared"


def _validate_page_id(page_id: str) -> None:
    """Reject page_id values that could cause path traversal.

    Mirrors the regex used by ``CreatePageTool`` so a page_id that survives
    creation also survives serving (and vice-versa). The resolved-path check
    is belt-and-suspenders against symlink shenanigans.
    """
    if not re.fullmatch(r'[a-z0-9_]{1,64}', page_id):
        raise HTTPException(status_code=400, detail="Invalid page_id")
    resolved = (_PAGES_DIR / page_id).resolve()
    if not str(resolved).startswith(str(_PAGES_DIR.resolve())):
        raise HTTPException(status_code=400, detail="Invalid page_id")


_shared_version_cache: str = ""
_shared_version_mtime: float = 0.0

def _shared_version() -> str:
    """Content-based version hash for cache-busting shared assets."""
    global _shared_version_cache, _shared_version_mtime
    import hashlib
    css = _SHARED_DIR / "hime-ui.css"
    js = _SHARED_DIR / "hime-ui.js"
    try:
        mtime = max(css.stat().st_mtime if css.exists() else 0,
                    js.stat().st_mtime if js.exists() else 0)
        if mtime != _shared_version_mtime:
            content = (css.read_bytes() if css.exists() else b"") + (js.read_bytes() if js.exists() else b"")
            _shared_version_cache = hashlib.md5(content).hexdigest()[:8]
            _shared_version_mtime = mtime
    except Exception:
        _shared_version_cache = "1"
    return _shared_version_cache


def _get_db_file(user_id: str) -> Path:
    return settings.MEMORY_DB_PATH / f"{user_id}.db"


def _get_active_pid() -> str | None:
    """Return the user ID of the currently running agent, if any."""
    try:
        from .agent_state import active_agents
        if active_agents:
            return next(iter(active_agents))
    except Exception:
        pass
    return None


def _all_memory_dbs() -> list[Path]:
    """Return all user memory DB files."""
    db_dir = settings.MEMORY_DB_PATH
    if not db_dir.exists():
        return []
    return list(db_dir.glob("*.db"))


@router.get("/_shared/{filename}")
async def serve_shared_asset(filename: str):
    """Serve shared UI component assets (CSS/JS) with no-cache headers."""
    from fastapi.responses import Response
    path = _SHARED_DIR / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail=f"Shared asset '{filename}' not found")
    content_types = {".css": "text/css", ".js": "application/javascript"}
    ct = content_types.get(path.suffix, "application/octet-stream")
    return Response(
        content=path.read_text(encoding="utf-8"),
        media_type=ct,
        headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"},
    )


@router.get("/list")
async def list_personalised_pages():
    """List all active personalised pages across all users."""
    pages: list[dict] = []
    seen_ids: set[str] = set()

    for db_file in _all_memory_dbs():
        try:
            with sqlite3.connect(str(db_file), timeout=10) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT page_id, display_name, description, backend_route, "
                    "frontend_asset, created_at "
                    "FROM personalised_pages WHERE status='active' ORDER BY created_at ASC"
                ).fetchall()
            for row in rows:
                d = dict(row)
                if d["page_id"] not in seen_ids:
                    seen_ids.add(d["page_id"])
                    pages.append(d)
        except Exception:
            continue

    return {"success": True, "pages": pages}


@router.delete("/{page_id}")
async def delete_personalised_page(page_id: str):
    """Soft-delete a personalised page: set status='deleted' in all DBs and remove files."""
    _validate_page_id(page_id)
    updated = False
    for db_file in _all_memory_dbs():
        try:
            with sqlite3.connect(str(db_file), timeout=10) as conn:
                cur = conn.execute(
                    "UPDATE personalised_pages SET status='deleted' WHERE page_id=? AND status='active'",
                    (page_id,),
                )
                if cur.rowcount > 0:
                    updated = True
        except Exception:
            continue

    # Remove files on disk (protect _shared assets directory)
    if page_id == "_shared":
        raise HTTPException(status_code=400, detail="Cannot delete shared assets")
    page_dir = _PAGES_DIR / page_id
    if page_dir.exists():
        shutil.rmtree(page_dir, ignore_errors=True)
        updated = True

    if not updated:
        raise HTTPException(status_code=404, detail=f"Page '{page_id}' not found")

    logger.info("Deleted personalised page: %s", page_id)
    return {"success": True, "page_id": page_id}


# Content-Security-Policy applied to every personalised page response.
# Pages are inherently agent-generated, but we still lock them down so a
# compromised page cannot exfiltrate data to a third-party host or be
# embedded by an attacker site:
#   - default-src 'self'    : only same-origin resource loads
#   - script-src 'self' 'unsafe-inline' : the agent ships inline JS
#   - connect-src 'self'    : XHR/fetch/WebSocket only to this origin
#   - frame-ancestors 'self': may be iframed by HIME frontend (same origin)
#                             but not by any third-party site (clickjacking)
#   - base-uri 'none'       : no <base> override tricks
_PAGE_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors 'self'; "
    "base-uri 'none'"
)
_PAGE_SECURITY_HEADERS = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Content-Security-Policy": _PAGE_CSP,
    "X-Frame-Options": "SAMEORIGIN",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


@router.get("/{page_id}/", response_class=HTMLResponse)
async def serve_page_frontend(page_id: str):
    """Serve the frontend HTML for a personalised page, with cache-busting for shared assets."""
    _validate_page_id(page_id)
    html_path = _PAGES_DIR / page_id / "index.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail=f"Page '{page_id}' not found")
    html = html_path.read_text(encoding="utf-8")
    # Inject cache-busting version into _shared asset URLs so browsers/WKWebView
    # don't serve stale 404 responses from when _shared was temporarily deleted.
    html = html.replace(
        '/api/personalised-pages/_shared/hime-ui.css',
        f'/api/personalised-pages/_shared/hime-ui.css?v={_shared_version()}',
    ).replace(
        '/api/personalised-pages/_shared/hime-ui.js',
        f'/api/personalised-pages/_shared/hime-ui.js?v={_shared_version()}',
    )
    # Guarantee a mobile-friendly viewport even when the agent-authored HTML
    # omits it. Without this, WKWebView renders at 980px CSS width and scales
    # down, making pages feel tiny and horizontally pannable.
    if 'name="viewport"' not in html and "name='viewport'" not in html:
        meta = (
            '<meta name="viewport" '
            'content="width=device-width, initial-scale=1, '
            'viewport-fit=cover">'
        )
        if "<head>" in html:
            html = html.replace("<head>", "<head>\n    " + meta, 1)
        elif "<html" in html:
            html = html.replace("<html", meta + "<html", 1)
        else:
            html = meta + html
    return HTMLResponse(content=html, headers=_PAGE_SECURITY_HEADERS)


async def _exec_route_handler(page_id: str, request: Request | None):
    """Load and execute a page's route_handler."""
    _validate_page_id(page_id)
    route_path = _PAGES_DIR / page_id / "route.py"
    if not route_path.exists():
        return JSONResponse(
            status_code=404,
            content={"success": False, "error": f"Page '{page_id}' backend not found"},
        )

    # Pre-validate syntax before attempting to import
    source = route_path.read_text(encoding="utf-8")
    try:
        compile(source, str(route_path), "exec")
    except SyntaxError as se:
        logger.error(
            "serve_page_data syntax error for %s: %s (line %s)",
            page_id, se.msg, se.lineno,
        )
        return JSONResponse(
            status_code=422,
            content={
                "success": False,
                "error": f"Page '{page_id}' has a syntax error in its backend code: "
                         f"{se.msg} (line {se.lineno}). "
                         "Ask the agent to recreate this page.",
            },
        )

    try:
        spec = importlib.util.spec_from_file_location(f"personalised_page_{page_id}", route_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        if not hasattr(module, "route_handler"):
            return JSONResponse(
                status_code=500,
                content={
                    "success": False,
                    "error": f"Page '{page_id}' backend has no route_handler function.",
                },
            )

        if asyncio.iscoroutinefunction(module.route_handler):
            result = await module.route_handler(request)
        else:
            # Pre-read body so sync handlers can access it via request._body
            body = await request.body()
            request._body = body
            result = await asyncio.to_thread(module.route_handler, request)

        return JSONResponse(content=result)
    except Exception as e:
        logger.error("serve_page_data error for %s: %s", page_id, e, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": f"Page '{page_id}' backend encountered an internal error.",
            },
        )


@router.get("/{page_id}/data")
async def serve_page_data_get(page_id: str, request: Request):
    """Execute the page's backend route_handler (GET)."""
    return await _exec_route_handler(page_id, request)


@router.post("/{page_id}/data")
async def serve_page_data_post(page_id: str, request: Request):
    """Execute the page's backend route_handler (POST)."""
    return await _exec_route_handler(page_id, request)
