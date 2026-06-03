"""
Push-report tool — saves an analysis report to the memory DB and notifies
every configured messaging gateway (Telegram, Feishu, …).

Reports have two bodies:

* ``content`` — the full analysis, stored in the memory DB and surfaced in
  the HIME dashboard + iOS app. This is where depth goes.
* ``im_digest`` — a short, conversational one-liner / paragraph delivered
  through every IM channel. Keep it brief and human — the user opens the
  dashboard or iOS app if they want the full report.

Thread safety
-------------
SQLite writes are performed inside ``asyncio.to_thread`` so the event loop is
never blocked.  Gateway sends happen through the async ``GatewayRegistry``.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import mimetypes
import re
import time
from pathlib import Path
from typing import Any

import httpx

from ...agent.memory_manager import _ensure_schema
from ...config import settings
from ...messaging.registry import GatewayRegistry
from ...utils import ts_now
from .base import BaseTool

try:
    import sqlite3
except ImportError:
    sqlite3 = None  # type: ignore

logger = logging.getLogger(__name__)

# Inlining charts into the report ----------------------------------------------
# The analysis agent saves charts to local files (e.g. /tmp/sleep.png) and refs
# them inline with standard Markdown image syntax. We replace each local image
# src with a self-contained base64 ``data:`` URI so the chart is embedded in the
# report content itself — it survives a restart, needs no extra endpoint, and is
# stored at rest with the rest of the report. Remote (http) and already-inline
# (data:) srcs are left untouched.
_IMG_RE = re.compile(r"!\[([^\]]*)\]\(\s*([^)\s]+)\s*\)")
_IMG_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_MAX_IMG_BYTES = 2_000_000      # per chart
_MAX_EMBEDDED = 8               # charts per report


def _file_to_data_uri(src: str) -> str | None:
    """Return a ``data:`` URI for a local image file, or None if it's not a
    safe, in-bounds image. Guards on extension + size so a stray non-image path
    can never be slurped into a report."""
    try:
        path = Path(src).expanduser()
        if path.suffix.lower() not in _IMG_EXTS or not path.is_file():
            return None
        if path.stat().st_size > _MAX_IMG_BYTES:
            logger.warning("push_report: chart %s exceeds %d bytes — skipping", src, _MAX_IMG_BYTES)
            return None
        mime = mimetypes.guess_type(str(path))[0] or "image/png"
        b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{b64}"
    except Exception as exc:
        logger.warning("push_report: failed to embed chart %s: %s", src, exc)
        return None


def _embed_local_charts(content: str, extra_paths: list[str] | None) -> str:
    """Inline any local-file chart references in *content* as data URIs, then
    append any *extra_paths* not already referenced. Caps total embeds."""
    embedded = 0

    def _repl(m: re.Match) -> str:
        nonlocal embedded
        alt, src = m.group(1), m.group(2)
        if src.startswith(("data:", "http://", "https://")) or embedded >= _MAX_EMBEDDED:
            return m.group(0)
        uri = _file_to_data_uri(src)
        if uri is None:
            return m.group(0)
        embedded += 1
        return f"![{alt}]({uri})"

    out = _IMG_RE.sub(_repl, content or "")

    for p in (extra_paths or []):
        if embedded >= _MAX_EMBEDDED:
            break
        # Skip ones already inlined above (same basename referenced in content).
        if Path(p).name in out:
            continue
        uri = _file_to_data_uri(p)
        if uri is None:
            continue
        embedded += 1
        out += f"\n\n![chart]({uri})"
    return out


class PushReportTool(BaseTool):
    """Save an analysis report to the memory DB and push it to the frontend + every enabled messaging gateway."""

    name = "push_report"

    _VALID_ALERT_LEVELS = ("normal", "info", "warning", "critical")
    _VALID_SOURCES = ("scheduled_analysis", "quick_analysis")

    def __init__(
        self,
        memory_db_path: Path,
        user_id: str,
        report_callback=None,
        telegram_sender=None,
        fact_verifier=None,
        gateway_registry: GatewayRegistry | None = None,
    ) -> None:
        self.memory_db_file  = memory_db_path / f"{user_id}.db"
        self.user_id  = user_id
        self.report_callback = report_callback  # optional: push to frontend live
        # Legacy: single Telegram sender (still used by older tests/benchmarks)
        self._telegram_sender = telegram_sender
        # Preferred: multi-channel gateway registry (Telegram + Feishu + ...)
        self._gateway_registry = gateway_registry
        self._fact_verifier = fact_verifier       # FactVerifier for evidence tracking
        self._llm_provider = None                 # Set by agent for LLM semantic verification
        # Accumulates tool results from the current analysis loop
        self._current_tool_results: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Tool definition
    # ------------------------------------------------------------------

    def get_definition(self) -> dict:
        return self._get_definition_from_json("push_report")

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def execute(
        self,
        title: str = "",
        content: str = "",
        im_digest: str = "",
        time_range_start: str = "",
        time_range_end: str = "",
        alert_level: str = "normal",
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        source: str = "scheduled_analysis",
        image_paths: list[str] | None = None,
    ) -> dict:
        """
        Persist report and notify subscribers.

        Args:
            source: Origin of the report. One of "scheduled_analysis" or
                    "quick_analysis". Stored in the report's ``source`` column
                    and ``metadata.source`` so the frontend can display it.
            image_paths: Optional local chart files to embed. Charts are usually
                    referenced inline in ``content`` (``![caption](/tmp/x.png)``)
                    and inlined automatically; anything listed here that isn't
                    already referenced is appended to the end of the report.
        """
        # Require at least title and content — the rest can be defaulted
        if not title or not content:
            return {
                "success": False,
                "error": "push_report requires at least 'title' and 'content'. "
                         "Also provide: im_digest, time_range_start, time_range_end.",
            }

        # Inline chart files as self-contained data URIs so the report renders
        # 图文并茂 in the dashboard / iOS app. Only touches ``content`` — the
        # short ``im_digest`` sent to IM channels stays text-only.
        content = _embed_local_charts(content, image_paths)
        # Default missing time range to now
        if not time_range_start:
            time_range_start = ts_now()
        if not time_range_end:
            time_range_end = ts_now()
        if not im_digest:
            im_digest = title

        if alert_level not in self._VALID_ALERT_LEVELS:
            return {
                "success": False,
                "error": f"Invalid alert_level. Must be one of: {self._VALID_ALERT_LEVELS}",
            }

        # Normalise source — default to scheduled_analysis if unknown
        if source not in self._VALID_SOURCES:
            source = "scheduled_analysis"

        # Build final metadata dict (validate type — LLM may pass a non-dict)
        report_meta: dict[str, Any] = dict(metadata) if isinstance(metadata, dict) else {}
        if tags:
            report_meta["tags"] = tags
        report_meta["user_id"] = self.user_id
        report_meta["im_digest"] = im_digest
        report_meta["source"] = source

        # Build the short IM message. This is what gets sent to every
        # messaging gateway (Telegram, Feishu, …) — intentionally short and
        # conversational. The full ``content`` stays in the DB and is
        # surfaced through the dashboard + iOS app. Do NOT truncate here:
        # if a digest is too long for a platform's limits the sender-layer
        # will handle it (splitting across elements, falling back, etc.);
        # silent mid-sentence cuts with a "...truncated" marker feel broken
        # in a chat UI.
        _EMOJI = {"normal": "\u2705", "info": "\u2139\ufe0f", "warning": "\u26a0\ufe0f", "critical": "\U0001f6a8"}
        im_body = im_digest or content
        emoji = _EMOJI.get(alert_level, "\U0001f4dd")
        im_text = f"{emoji} **{title}**\n\n{im_body}"

        # Fact verification — block fabricated / unverified reports before persisting
        verification = await self._verify_and_build_markup(im_text)
        if verification["status"] in ("fabricated", "unverified"):
            logger.warning(
                "push_report blocked (%s): %s", verification["status"], verification["detail"],
            )
            return {
                "success": False,
                "error": (
                    f"Report blocked by fact verification ({verification['status']}): "
                    f"{verification['detail']}. "
                    "Use sql/code tools to query real data before pushing a report."
                ),
            }

        # Persist asynchronously
        t0 = time.perf_counter()
        report_id = await asyncio.to_thread(
            self._save_to_db,
            title, content, time_range_start, time_range_end, alert_level, report_meta, source,
        )
        elapsed = time.perf_counter() - t0
        logger.info("push_report: _save_to_db took %.2fs", elapsed)

        if report_id < 0:
            logger.error("Database write failed for report '%s'.", title)
            return {
                "success": False,
                "error": "DB insert failed — report was not saved. Check logs for details.",
            }

        if report_id == 0:
            logger.error("Database insert returned no rowid for report '%s'.", title)
            return {
                "success": False,
                "error": "DB insert returned no rowid — report may not have been saved.",
            }

        report: dict[str, Any] = {
            "id":               report_id,
            "created_at":       ts_now(),
            "time_range_start": time_range_start,
            "time_range_end":   time_range_end,
            "title":            title,
            "content":          content,
            "alert_level":      alert_level,
            "metadata":         report_meta,
            "user_id":   self.user_id,
            "im_digest":        im_digest,
            "source":           source,
        }

        # Push to frontend (fast, in-process callback — OK on event loop)
        if self.report_callback:
            try:
                self.report_callback(report)
            except Exception as exc:
                logger.warning("report_callback failed: %s", exc)

        # Pass pre-built IM text and evidence markup to avoid re-building
        report["_im_text"] = im_text
        report["_reply_markup"] = verification["reply_markup"]

        # User notification (all channels) — fully async, errors are non-fatal
        task = asyncio.create_task(self._notify_user(report))
        task.add_done_callback(lambda t: logger.error("User notify failed: %s", t.exception()) if not t.cancelled() and t.exception() else None)

        logger.info("Report saved: '%s' (id=%d, level=%s, source=%s)", title, report_id, alert_level, source)
        return {
            "success":    True,
            "report_id":  report_id,
            "title":      title,
            "alert_level": alert_level,
            "source":     source,
            "message":    "Report saved and pushed successfully.",
        }

    # ------------------------------------------------------------------
    # SQLite persistence (runs in thread pool)
    # ------------------------------------------------------------------

    def _save_to_db(
        self,
        title: str,
        content: str,
        time_range_start: str,
        time_range_end: str,
        alert_level: str,
        metadata: dict,
        source: str = "scheduled_analysis",
    ) -> int:
        """Insert report row and return the new row id (-1 on failure)."""
        try:
            import sqlite3 as _sqlite3  # noqa: PLC0415
            with _sqlite3.connect(self.memory_db_file, timeout=30) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                _ensure_schema(conn)
                cur = conn.execute(
                    """INSERT INTO reports
                       (time_range_start, time_range_end, title, content, alert_level, metadata, source)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        time_range_start, time_range_end,
                        title, content, alert_level,
                        json.dumps(metadata),
                        source,
                    ),
                )
                conn.commit()
                return cur.lastrowid or -1
        except Exception as exc:
            logger.error("Failed to save report to DB: %s", exc, exc_info=True)
            return -1

    # ------------------------------------------------------------------
    # User notification (fully async, never blocks)
    # ------------------------------------------------------------------

    async def _persist_ios_history(
        self, message: str, reply_markup: dict | None, report_id: int | None = None
    ) -> None:
        """Store a proactive report as an assistant turn in the iOS chat history
        (history_key ``ios:<uid>``) so it shows in the in-app conversation, with
        the evidence hash so "Show evidence" works just like a chat reply, and
        the ``report_id`` so the bubble's "view full report" deep-link survives
        a history reload."""
        try:
            from ...ios_gateway.gateway import extract_message_hash
            from ..memory_manager import MemoryManager
            msg_hash = extract_message_hash(reply_markup)
            mem = MemoryManager(self.memory_db_file.parent, self.user_id)
            await mem.persist_chat_turn(
                f"ios:{self.user_id}", "assistant", message,
                message_hash=msg_hash, report_id=report_id,
            )
        except Exception as exc:
            logger.debug("iOS chat-history persist for proactive report skipped: %s", exc)

    async def _notify_user(self, report: dict[str, Any]) -> None:
        """Push the report to every configured messaging gateway.

        When a ``GatewayRegistry`` is wired in, iterate over all registered
        gateways and send to each that has a ``default_chat_id``. Otherwise
        fall back to the legacy single-sender Telegram path so older tests
        and benchmark harnesses keep working unchanged.
        """
        message = report.get("_im_text", "")
        if not message:
            _EMOJI = {"normal": "\u2705", "info": "\u2139\ufe0f", "warning": "\u26a0\ufe0f", "critical": "\U0001f6a8"}
            emoji = _EMOJI.get(report.get("alert_level", ""), "\U0001f4dd")
            body = report.get("im_digest") or report.get("content", "")
            message = f"{emoji} **{report['title']}**\n\n{body}"

        reply_markup = report.get("_reply_markup")
        report_id = report.get("id")

        # Preferred path: dispatch via GatewayRegistry (all enabled channels)
        if self._gateway_registry is not None and len(self._gateway_registry) > 0:
            has_ios = False
            for gw in self._gateway_registry.all():
                is_ios = getattr(getattr(gw, "channel", None), "value", None) == "ios"
                if is_ios:
                    has_ios = True
                target = getattr(gw, "default_chat_id", None)
                if not target:
                    continue
                try:
                    if gw.is_muted():
                        logger.info(
                            "%s gateway muted — skipping notification for '%s'",
                            gw.channel.value, report.get("title", "?"),
                        )
                        continue
                    # Only the iOS gateway carries report_id (for the in-app
                    # "view full report" deep-link); IM gateways don't accept it.
                    send_kwargs = {"text": message, "chat_id": target, "reply_markup": reply_markup}
                    if is_ios:
                        send_kwargs["report_id"] = report_id
                    ok = await gw.send_message(**send_kwargs)
                    if ok:
                        logger.info(
                            "Notification sent via %s for report '%s'",
                            gw.channel.value, report["title"],
                        )
                    else:
                        logger.warning(
                            "Notification failed via %s for report '%s'",
                            gw.channel.value, report["title"],
                        )
                except Exception as exc:
                    logger.warning("%s notification error: %s", gw.channel.value, exc)
            # Proactive reports go out over the iOS WS as a live chat_reply, but
            # (unlike chat-loop turns) nothing persists them — so they'd vanish
            # from the in-app conversation on reload, surviving only in the
            # Reports tab. Persist the digest as an assistant chat turn so every
            # scheduled / triggered report is a durable in-app message too.
            if has_ios:
                await self._persist_ios_history(message, reply_markup, report_id=report_id)
            return

        # Legacy path: shared TelegramSender directly (backward compat for tests)
        if self._telegram_sender is not None:
            if getattr(self._telegram_sender, "is_muted", lambda: False)():
                logger.info("Telegram muted — skipping notification for '%s'", report.get("title", "?"))
                return
            try:
                ok = await self._telegram_sender.send_message(
                    text=message, reply_markup=reply_markup,
                )
                if ok:
                    logger.info("Telegram notification sent for report '%s'", report["title"])
                else:
                    logger.warning("Telegram notification failed for report '%s'", report["title"])
            except Exception as exc:
                logger.warning("Telegram notification error: %s", exc)
            return

        # Final fallback: inline httpx (bench / no gateway)
        # Only fires when the gateway is explicitly enabled — having a token
        # in .env is not enough. This prevents accidental message leaks when
        # the operator disables the gateway but forgets to clear the token.
        if not getattr(settings, "TELEGRAM_GATEWAY_ENABLED", False):
            return
        token   = getattr(settings, "telegram_token", None)
        chat_id = getattr(settings, "chat_id", None)
        if not token or not chat_id:
            return

        from ...telegram.sender import _markdown_to_telegram_html  # noqa: PLC0415
        url     = f"https://api.telegram.org/bot{token}/sendMessage"
        payload: dict = {"chat_id": chat_id, "text": _markdown_to_telegram_html(message), "parse_mode": "HTML"}
        if reply_markup:
            payload["reply_markup"] = reply_markup

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    logger.info("Telegram notification sent for report '%s'", report["title"])
                elif resp.status_code == 400 and "parse" in resp.text.lower():
                    payload["parse_mode"] = ""
                    resp2 = await client.post(url, json=payload)
                    if resp2.status_code == 200:
                        logger.info("Telegram notification sent (plain) for report '%s'", report["title"])
                    else:
                        logger.warning("Telegram notification failed (plain): %s", resp2.text[:200])
                else:
                    logger.warning("Telegram notification failed (%d): %s", resp.status_code, resp.text[:200])
        except Exception as exc:
            logger.warning("Telegram notification error: %s", exc)


# Local alias so callers that do `from .push_report_tool import _VALID_ALERT_LEVELS` work
_VALID_ALERT_LEVELS = PushReportTool._VALID_ALERT_LEVELS
