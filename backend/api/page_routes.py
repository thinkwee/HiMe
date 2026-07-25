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


# --- Inlining the shared assets -------------------------------------------
#
# The HiMe dashboard embeds these pages in a `sandbox="allow-scripts"` iframe
# (no `allow-same-origin`), which puts the document in an OPAQUE origin. Browser
# engines disagree about what CSP `'self'` means there, and the disagreement is
# fatal rather than cosmetic:
#
#   * Chromium 131 / Firefox 132 resolve `'self'` against the *response URL's*
#     origin, so `<script src="/api/personalised-pages/_shared/hime-ui.js">`
#     loads normally.
#   * WebKit 18.2 (Safari 18.x, i.e. macOS Safari and iOS) resolves `'self'`
#     against the *document's* opaque origin, which matches nothing. It refuses
#     both shared assets outright ("Refused to load … because it does not appear
#     in the style-src directive"), leaving a completely blank page.
#
# Verified empirically with Playwright across all three engines. The options
# that make WebKit work by loosening CSP are all worse: naming the origin
# explicitly means trusting the Host header, and `script-src http: https:` would
# let an agent-generated page pull code from any host — exactly the exfiltration
# path the policy exists to close.
#
# So we remove the subresource instead: the shared CSS/JS are inlined into the
# page HTML at serve time. Inline content is covered by the `'unsafe-inline'`
# already in the policy (the agent ships inline JS regardless), needs no new
# host source, is origin-agnostic, and saves two round trips. The `_shared/`
# endpoint below is kept for any page HTML whose tags don't match the patterns,
# and for top-level (non-opaque) loads such as the iOS WKWebView.

_SHARED_CSS_LINK_RE = re.compile(
    r'<link\b[^>]*href\s*=\s*["\'][^"\']*_shared/hime-ui\.css[^"\']*["\'][^>]*>',
    re.IGNORECASE,
)
_SHARED_JS_SCRIPT_RE = re.compile(
    r'<script\b[^>]*src\s*=\s*["\'][^"\']*_shared/hime-ui\.js[^"\']*["\'][^>]*>\s*</script\s*>',
    re.IGNORECASE,
)

_shared_assets_cache: tuple[str, str] = ("", "")
_shared_assets_mtime: float = -1.0


def _read_shared_assets() -> tuple[str, str]:
    """Return (css_text, js_text), re-read only when either file changes."""
    global _shared_assets_cache, _shared_assets_mtime
    css = _SHARED_DIR / "hime-ui.css"
    js = _SHARED_DIR / "hime-ui.js"
    try:
        mtime = max(css.stat().st_mtime if css.exists() else 0,
                    js.stat().st_mtime if js.exists() else 0)
        if mtime != _shared_assets_mtime:
            _shared_assets_cache = (
                css.read_text(encoding="utf-8") if css.exists() else "",
                js.read_text(encoding="utf-8") if js.exists() else "",
            )
            _shared_assets_mtime = mtime
    except Exception:
        logger.warning("Could not read shared page assets for inlining", exc_info=True)
        return ("", "")
    return _shared_assets_cache


def _inline_shared_assets(html: str) -> str:
    """Replace the shared <link>/<script src> tags with their inline contents."""
    css_text, js_text = _read_shared_assets()
    if css_text:
        # A literal '</style' inside the CSS would terminate the block early.
        safe_css = css_text.replace("</style", "<\\/style")
        html = _SHARED_CSS_LINK_RE.sub(
            lambda _m: f"<style>\n{safe_css}\n</style>", html, count=1
        )
    if js_text:
        # Likewise for '</script' inside the JS.
        safe_js = js_text.replace("</script", "<\\/script")
        html = _SHARED_JS_SCRIPT_RE.sub(
            lambda _m: f"<script>\n{safe_js}\n</script>", html, count=1
        )
    return html


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
    # Don't rely on the route pattern alone to keep traversal out — whitelist
    # the filename shape and the extensions this endpoint is meant to serve.
    content_types = {".css": "text/css", ".js": "application/javascript"}
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', filename) or not filename.endswith(tuple(content_types)):
        raise HTTPException(status_code=400, detail="Invalid asset name")
    path = _SHARED_DIR / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail=f"Shared asset '{filename}' not found")
    ct = content_types[path.suffix]
    return Response(
        content=await asyncio.to_thread(path.read_text, encoding="utf-8"),
        media_type=ct,
        headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"},
    )


@router.get("/list")
async def list_personalised_pages():
    """List all active personalised pages across all users."""
    def _scan() -> list[dict]:
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
        return pages

    # Walks every memory DB — keep it off the event loop.
    return {"success": True, "pages": await asyncio.to_thread(_scan)}


@router.delete("/{page_id}")
async def delete_personalised_page(page_id: str):
    """Soft-delete a personalised page: set status='deleted' in all DBs and remove files."""
    _validate_page_id(page_id)

    def _mark_deleted() -> bool:
        marked = False
        for db_file in _all_memory_dbs():
            try:
                with sqlite3.connect(str(db_file), timeout=10) as conn:
                    cur = conn.execute(
                        "UPDATE personalised_pages SET status='deleted' WHERE page_id=? AND status='active'",
                        (page_id,),
                    )
                    if cur.rowcount > 0:
                        marked = True
            except Exception:
                continue
        return marked

    updated = await asyncio.to_thread(_mark_deleted)

    # Remove files on disk (protect _shared assets directory)
    if page_id == "_shared":
        raise HTTPException(status_code=400, detail="Cannot delete shared assets")
    page_dir = _PAGES_DIR / page_id
    if page_dir.exists():
        await asyncio.to_thread(shutil.rmtree, page_dir, ignore_errors=True)
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
#   - form-action 'self'    : a form cannot POST harvested data to a third-party
#                             host. Without this, connect-src is trivially
#                             sidestepped by submitting a form instead of fetch().
_PAGE_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors 'self'; "
    "form-action 'self'; "
    "base-uri 'none'"
)
_PAGE_SECURITY_HEADERS = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Content-Security-Policy": _PAGE_CSP,
    "X-Frame-Options": "SAMEORIGIN",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}

# The same policy, carried INSIDE the document as a <meta> tag.
#
# Why both: the HiMe dashboard no longer points the iframe at this URL. The
# endpoint is behind the API bearer token, and putting that token in an
# `<iframe src>` would hand a full-privilege credential to the very code the
# sandbox exists to contain (the page can read its own location.search). So the
# dashboard fetches this HTML with an Authorization header and injects it via
# `srcdoc` instead — no URL, no token, nothing to steal.
#
# A `srcdoc` document is not an HTTP response, so it never sees the headers
# above. Its policy has to travel in the markup, which is what this is. The
# response header is kept for the loads that DO have a URL (top-level "open in
# a new tab" via the SPA, the iOS WKWebView, curl), so both paths end up under
# the same rules.
#
# Two directives are deliberately dropped: `frame-ancestors` and `sandbox` are
# ignored in a meta policy by every engine (and log a console warning if
# present). Neither is needed here — `frame-ancestors` protects a URL from being
# framed by a third-party site, and a srcdoc document has no URL for anyone else
# to frame; the sandbox is applied by the embedder's `sandbox` attribute.
_PAGE_CSP_META = "; ".join(
    d for d in _PAGE_CSP.split("; ") if not d.startswith("frame-ancestors")
)
_PAGE_META_TAGS = (
    f'<meta http-equiv="Content-Security-Policy" content="{_PAGE_CSP_META}">'
    '<meta name="referrer" content="no-referrer">'
)


def _inject_head(html: str, snippet: str) -> str:
    """Insert `snippet` as early inside <head> as possible.

    A meta CSP only governs what comes *after* it, so this has to land before
    the page's own tags — hence the insert right after the opening <head>, with
    fallbacks for agent HTML that omits <head> or <html> entirely.
    """
    lowered = html.lower()
    idx = lowered.find("<head>")
    if idx != -1:
        cut = idx + len("<head>")
        return html[:cut] + "\n    " + snippet + html[cut:]
    idx = lowered.find("<html")
    if idx != -1:
        end = html.find(">", idx)
        if end != -1:
            return html[: end + 1] + "\n" + snippet + html[end + 1:]
    return snippet + html


@router.get("/{page_id}/", response_class=HTMLResponse)
async def serve_page_frontend(page_id: str):
    """Serve the frontend HTML for a personalised page, with cache-busting for shared assets."""
    _validate_page_id(page_id)
    html_path = _PAGES_DIR / page_id / "index.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail=f"Page '{page_id}' not found")
    html = await asyncio.to_thread(html_path.read_text, encoding="utf-8")
    # Inline the shared CSS/JS. This is what makes the page work inside the
    # dashboard's opaque-origin sandbox — see the block comment above
    # _SHARED_CSS_LINK_RE for why a subresource cannot be used there.
    html = await asyncio.to_thread(_inline_shared_assets, html)
    # Any shared reference the patterns above didn't match still gets the
    # cache-busting version, so browsers/WKWebView don't serve a stale 404
    # from when _shared was temporarily deleted.
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
        html = _inject_head(
            html,
            '<meta name="viewport" '
            'content="width=device-width, initial-scale=1, '
            'viewport-fit=cover">',
        )
    # Carry the CSP in the markup as well as the header, so the policy still
    # applies when the dashboard renders this HTML through `srcdoc` (which has
    # no HTTP response, hence no headers). Injected last so it ends up FIRST in
    # <head> — a meta policy does not govern what precedes it.
    html = _inject_head(html, _PAGE_META_TAGS)
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
    source = await asyncio.to_thread(route_path.read_text, encoding="utf-8")
    try:
        await asyncio.to_thread(compile, source, str(route_path), "exec")
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
        def _load_module():
            spec = importlib.util.spec_from_file_location(
                f"personalised_page_{page_id}", route_path
            )
            mod = importlib.util.module_from_spec(spec)
            # Agent-generated module top level can do arbitrary slow work
            # (heavy imports, opening a DB) — never run it on the event loop.
            spec.loader.exec_module(mod)
            return mod

        module = await asyncio.to_thread(_load_module)

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
