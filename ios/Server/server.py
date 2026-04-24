#!/usr/bin/env python3
"""
WatchExporter — macOS server (single port, aiohttp).

One port handles both transports:
  • WebSocket  ws://host:PORT     — foreground, real-time
  • HTTP POST  http://host:PORT/ingest  — background upload (iOS URLSession.background)
  • HTTP GET   http://host:PORT/ping    — connectivity check

Body for /ingest: JSON array of {ts, f, v} objects, or a single object.
Both transports write to the same SQLite watch.db.
"""

import asyncio
import json
import sqlite3
import argparse
import logging
import os
import socket
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aiohttp import web
import aiohttp
from rich.console import Console
from rich.logging import RichHandler
from rich.markup import escape

console = Console()
_log_level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
_log_level = getattr(logging, _log_level_name, logging.INFO)
logging.basicConfig(level=_log_level, format="%(message)s", datefmt="%H:%M:%S", handlers=[RichHandler(console=console, show_time=False)])
log = logging.getLogger(__name__)

def _ts_fmt(t: float) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _log(tag: str, msg: str, tag_style: str = "") -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    tag_brackets = escape(f"[{tag}]")
    console.print(f"[dim]{ts}[/dim]  [{tag_style}]{tag_brackets}[/]  {msg}")


def _log_connection(msg: str) -> None:
    _log("connection", msg, "bold cyan")


def _log_foreground(msg: str) -> None:
    _log("foreground", msg, "bold green")


def _log_background(msg: str) -> None:
    _log("background", msg, "bold yellow")



SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS health_samples_eav (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL    NOT NULL,
    f           TEXT    NOT NULL,
    v           REAL    NOT NULL,
    updated_at  REAL    NOT NULL DEFAULT (strftime('%s', 'now')),
    UNIQUE(ts, f)
);
CREATE INDEX IF NOT EXISTS idx_ts_f ON health_samples_eav (ts, f);
"""

INSERT = """
    INSERT INTO health_samples_eav (ts, f, v, updated_at)
    VALUES (:ts, :f, :v, strftime('%s', 'now'))
    ON CONFLICT(ts, f) DO UPDATE SET
        v = excluded.v,
        updated_at = strftime('%s', 'now')
"""


def init_db(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path, check_same_thread=False)

    # For existing databases the table already exists without updated_at.
    # CREATE TABLE IF NOT EXISTS is a no-op when the table exists, so
    # the full SCHEMA (which includes updated_at) works for new DBs.
    # For old DBs, we fall back to the migration path below.
    try:
        con.executescript(SCHEMA)
    except sqlite3.OperationalError:
        # Table exists with old schema — just ensure WAL and index
        con.execute("PRAGMA journal_mode=WAL")
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_ts_f "
            "ON health_samples_eav (ts, f)"
        )
        con.commit()

    # Migration: add updated_at column if missing (existing databases)
    try:
        con.execute("SELECT updated_at FROM health_samples_eav LIMIT 1")
    except sqlite3.OperationalError:
        con.execute(
            "ALTER TABLE health_samples_eav "
            "ADD COLUMN updated_at REAL NOT NULL DEFAULT 0"
        )
        con.execute(
            "UPDATE health_samples_eav SET updated_at = ts WHERE updated_at = 0"
        )
        con.commit()

    # Ensure index exists (covers both new and migrated DBs)
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_updated_at "
        "ON health_samples_eav (updated_at)"
    )
    con.commit()
    return con


def insert_payload(con: sqlite3.Connection, payload: dict) -> int:
    """Insert a payload. Returns number of rows actually inserted (0 for duplicates)."""
    now = time.time()
    ts = payload.get("ts")

    # Handle missing or malformed timestamp
    if ts is None:
        log.warning(f"Payload missing 'ts' key. Using server current time.")
        ts = now
    elif not isinstance(ts, (int, float)):
        try: ts = float(ts)
        except: ts = now

    # Detect milliseconds: timestamps > 1e12 are in milliseconds (epoch ms).
    # Current epoch in seconds is ~1.7e9; in ms it's ~1.7e12.
    if ts > 1e12:
        ts = ts / 1000.0

    if ts > now + 3600:
        log.warning(f"Dropping payload with future timestamp {ts} (now={now}, feature={payload.get('f', '?')})")
        return 0

    inserted = 0
    if "f" in payload and "v" in payload:
        # Explicit format: {f: "steps", v: 100}
        try:
            val = float(payload["v"])
            cur = con.execute(INSERT, {"ts": ts, "f": payload["f"], "v": val})
            inserted += cur.rowcount
        except (ValueError, TypeError):
            pass
    else:
        # Flattened format: {ts: 123, steps: 100, heart_rate: 70}
        for k, v in payload.items():
            if k == "ts" or v is None:
                continue
            try:
                val = float(v)
                cur = con.execute(INSERT, {"ts": ts, "f": k, "v": val})
                inserted += cur.rowcount
            except (ValueError, TypeError):
                continue
    return inserted


def insert_batch(con: sqlite3.Connection, payloads: list) -> int:
    """Insert a batch of payloads. Returns number of rows actually inserted (excludes duplicates)."""
    inserted = 0
    for p in payloads:
        try:
            inserted += insert_payload(con, p)
        except Exception as e:
            log.warning(f"Bad payload skipped: {e} | Payload: {p}")
    con.commit()
    return inserted


def local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


# ---------------------------------------------------------------------------
# WebSocket handler
# ---------------------------------------------------------------------------

async def handle_ws(request: web.Request) -> web.WebSocketResponse:
    if not _check_auth(request):
        _log_connection(f"WS auth rejected  {request.remote}")
        return web.Response(status=401, text="Unauthorized")
    con: sqlite3.Connection = request.app["db"]
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    _log_connection(f"iPhone connected (WS)  {request.remote}")
    try:
        async for msg in ws:
            if not _sync_enabled:
                continue
            if msg.type in (aiohttp.WSMsgType.TEXT, aiohttp.WSMsgType.BINARY):
                try:
                    recv_time = _ts_fmt(time.time())
                    # json.loads works for both str and bytes
                    data = json.loads(msg.data)
                    items = data if isinstance(data, list) else [data]
                    lock: asyncio.Lock = request.app["db_lock"]
                    async with lock:
                        n = await asyncio.to_thread(insert_batch, con, items)
                    
                    for item in items:
                        if "f" in item and "v" in item:
                            f, v = item["f"], item["v"]
                        else:
                            keys = [k for k in item if k != "ts" and item.get(k) is not None]
                            if not keys:
                                continue
                            f, v = keys[0], item[keys[0]]
                        
                        if not isinstance(v, (int, float)):
                            continue
                            
                        ts = item.get("ts")
                        if isinstance(ts, (int, float)):
                            ts_sec = ts / 1000.0 if ts > time.time() * 10 else ts
                            data_ts = _ts_fmt(ts_sec)
                            age = time.time() - ts_sec
                            _log_foreground(f"{f:20} = {v:>8.2f}  │  recv {recv_time}  data {data_ts}  age {age:.0f}s")
                        else:
                            _log_foreground(f"{f:20} = {v:>8.2f}  │  recv {recv_time}  from {request.remote}")
                except Exception as e:
                    log.warning(f"WS message error: {e}")
            elif msg.type == aiohttp.WSMsgType.PING:
                await ws.pong()
                _log_connection(f"WS ping from {request.remote}")
            elif msg.type == aiohttp.WSMsgType.ERROR:
                log.error(f"WS error: {ws.exception()}")
                break
            elif msg.type == aiohttp.WSMsgType.CLOSE:
                break
            else:
                log.debug(f"Unhandled WS message type: {msg.type}")
    finally:
        _log_connection(f"WS session ended, sync continues via HTTP  {request.remote}")
    return ws


# ---------------------------------------------------------------------------
# HTTP handlers
# ---------------------------------------------------------------------------

async def handle_ingest(request: web.Request) -> web.Response:
    if not _check_auth(request):
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    if not _sync_enabled:
        return web.json_response({"ok": False, "error": "sync disabled"}, status=403)
    con: sqlite3.Connection = request.app["db"]
    _log_connection(f"Ingest Request (HTTP) from {request.remote}")
    try:
        sync_mode = request.headers.get("X-Sync-Mode", "background").lower()
        log_fn = _log_foreground if sync_mode == "foreground" else _log_background

        recv_time = _ts_fmt(time.time())
        data = await request.json()
        items = data if isinstance(data, list) else [data]
        lock: asyncio.Lock = request.app["db_lock"]
        async with lock:
            n = await asyncio.to_thread(insert_batch, con, items)

        for item in items:
            if "f" in item and "v" in item:
                f, v = item["f"], item["v"]
            else:
                keys = [k for k in item if k != "ts" and item[k] is not None]
                if not keys:
                    continue
                f, v = keys[0], item[keys[0]]
            if not isinstance(v, (int, float)):
                continue
            ts = item.get("ts")
            if isinstance(ts, (int, float)):
                ts_sec = ts / 1000.0 if ts > time.time() * 10 else ts
                data_ts = _ts_fmt(ts_sec)
                age = time.time() - ts_sec
                log_fn(f"{f:20} = {v:>8.2f}  │  recv {recv_time}  data {data_ts}  age {age:.0f}s")
            else:
                log_fn(f"{f:20} = {v:>8.2f}  │  recv {recv_time}  from {request.remote}")

        return web.json_response({"ok": True, "inserted": n})
    except Exception as e:
        log.warning(f"HTTP ingest error: {e}")
        return web.json_response({"ok": False, "error": str(e)}, status=400)


_sync_enabled: bool = True

# ---------------------------------------------------------------------------
# Token auth — mirrors the backend's BearerAuthMiddleware.
# Reads API_AUTH_TOKEN from the environment (same .env the backend uses).
# When empty, auth is disabled (fine for localhost).
# ---------------------------------------------------------------------------

_auth_token: str = os.environ.get("API_AUTH_TOKEN", "").strip()


def _check_auth(request: web.Request) -> bool:
    """Return True if the request is authorized (or auth is disabled)."""
    if not _auth_token:
        return True
    # HTTP header: Authorization: Bearer <token>
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer ") and auth_header[7:].strip() == _auth_token:
        return True
    # Query param: ?token=<token>  (WebSocket clients can't set headers)
    if request.query.get("token", "") == _auth_token:
        return True
    return False


async def handle_sync_control(request: web.Request) -> web.Response:
    """Toggle the global sync state."""
    if not _check_auth(request):
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    global _sync_enabled
    try:
        body = await request.json()
        _sync_enabled = bool(body.get("enabled", True))
        _log_connection(f"Sync Control: {'ENABLED' if _sync_enabled else 'DISABLED'}")
        return web.json_response({"ok": True, "sync_enabled": _sync_enabled})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)


async def handle_ping(request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


# ---------------------------------------------------------------------------
# App factory & entry point
# ---------------------------------------------------------------------------

async def handle_any(request: web.Request) -> web.Response:
    """Catch-all for unknown paths to help debugging."""
    _log_connection(f"Unknown Request: {request.method} {request.path} from {request.remote}")
    return web.json_response({"error": "Path not found", "path": request.path}, status=404)


async def cleanup_old_data(db_path: str, retention_days: int = 30):
    """Periodically delete health samples older than retention_days."""
    _log("cleanup", f"Starting background cleanup (retention: {retention_days} days)", "bold yellow")
    while True:
        try:
            # Run once every 24 hours
            cutoff_ts = (datetime.now(timezone.utc) - timedelta(days=retention_days)).timestamp()
            def _do_delete():
                with sqlite3.connect(db_path) as con:
                    cur = con.cursor()
                    cur.execute("DELETE FROM health_samples_eav WHERE ts < ?", (cutoff_ts,))
                    deleted = cur.rowcount
                    con.commit()
                    return deleted
            
            count = await asyncio.to_thread(_do_delete)
            if count > 0:
                _log("cleanup", f"Removed {count} samples older than {retention_days} days", "yellow")
        except Exception as e:
            _log("cleanup", f"Error: {e}", "bold red")
        
        await asyncio.sleep(86400)


def make_app(db_path: str) -> web.Application:
    con = init_db(db_path)
    app = web.Application()
    app["db"] = con
    app["db_lock"] = asyncio.Lock()
    app.router.add_get("/ws", handle_ws)
    app.router.add_post("/ingest", handle_ingest)
    app.router.add_get("/ping", handle_ping)
    app.router.add_post("/sync-control", handle_sync_control)
    # Catch-all MUST be last
    app.router.add_route("*", "/{tail:.*}", handle_any)

    # Schedule cleanup task to start after the event loop is running
    async def _start_cleanup(application):
        asyncio.create_task(cleanup_old_data(db_path))

    app.on_startup.append(_start_cleanup)

    async def cleanup_db(application: web.Application) -> None:
        if "db" in application and application["db"]:
            application["db"].close()

    app.on_cleanup.append(cleanup_db)

    return app


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--db", default=str(Path(__file__).parent / "watch.db"))
    args = ap.parse_args()

    ip = local_ip()
    console.print()
    console.print("[bold]WatchExporter[/]  [dim]— HealthKit → SQLite[/]")
    console.print()
    console.print(f"  [dim]DB:[/]        [cyan]{Path(args.db).resolve()}[/]")
    console.print(f"  [dim]WebSocket:[/]  [green]ws://{ip}:{args.port}/ws[/]")
    console.print(f"  [dim]HTTP POST:[/]  [yellow]http://{ip}:{args.port}/ingest[/]")
    console.print()

    app = make_app(args.db)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    web.run_app(app, host="0.0.0.0", port=args.port, access_log=None)
