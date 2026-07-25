"""WeChat ClawBot inbound transport — long-poll the iLink ``getupdates`` endpoint.

The iLink API holds the connection for ~35 s when no updates are pending,
so the poller uses an HTTP timeout that gives the server room to reply
before treating the call as a network failure. A monotonically-advancing
``get_updates_buf`` cursor prevents re-delivery; we still keep a small
dedup set as a belt-and-braces guard against revisions that sometimes
resend on cursor regressions. Both are mirrored to disk: iLink resends
from its own retained position when the cursor is empty, so a restart
that forgot the cursor would re-deliver — and, with the dedup set gone
too, re-answer — messages the agent had already handled.

Every inbound message carries a ``context_token`` that the iLink
``sendmessage`` endpoint requires for replies. The poller caches the
latest token per ``from_user_id`` so the sender can thread autonomous
replies back into an existing conversation.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from .models import MessageChannel, MessageEnvelope
from .qr_login import CHANNEL_VERSION, common_headers

logger = logging.getLogger(__name__)

ILINK_BASE = "https://ilinkai.weixin.qq.com"
# iLink's getupdates holds the connection ~35 s when no messages are
# pending. The HTTP timeout is set generously so a clean server reply
# never looks like a network failure to httpx.
_LONG_POLL_HTTP_TIMEOUT = 50.0

_STATE_FILENAME = "weixin_poller_state.json"

OnMessage = Callable[[MessageEnvelope], Awaitable[None]]


def _resolve_state_path(explicit: Path | str | None) -> Path | None:
    """Resolve the cursor file, or ``None`` when persistence is disabled.

    An explicit path always wins.  The default sits next to the rest of the
    runtime state (``settings.MEMORY_DB_PATH`` — the directory that already
    holds ``app_state.json`` and the agent memory DBs); ``settings`` is
    imported lazily so a patched singleton is honoured.

    Under pytest the default resolves to ``None`` instead: the suite builds
    pollers directly and ``tests/conftest.py`` does not redirect
    ``MEMORY_DB_PATH``, so an implicit default would drop cursor files into
    the working tree.  Tests that exercise persistence pass an explicit path.
    """
    if explicit is not None:
        return Path(explicit)
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return None
    try:
        from ..config import settings
        return Path(settings.MEMORY_DB_PATH) / _STATE_FILENAME
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("WeixinPoller: cursor persistence disabled: %s", exc)
        return None


class WeixinPoller:
    """Long-poll iLink ``getupdates`` and dispatch normalised envelopes.

    Parameters
    ----------
    bot_token : str
        Persistent token from the QR login step.
    on_message : callable
        ``async def(envelope) -> None`` — invoked per inbound text message.
    allowed_user_ids : set[str] | None
        If non-None, only messages from these user IDs are forwarded
        (default-deny). ``None`` disables the whitelist.
    state_path : Path | str | None
        Where to persist the cursor + dedup set. ``None`` (the default)
        resolves via :func:`_resolve_state_path`.
    """

    def __init__(
        self,
        bot_token: str,
        on_message: OnMessage,
        allowed_user_ids: set[str] | None = None,
        state_path: Path | str | None = None,
    ) -> None:
        self._token = bot_token
        self._on_message = on_message
        self._allowed = allowed_user_ids
        self._cursor: str = ""
        self._running = False
        self._client: httpx.AsyncClient | None = None
        # insertion-ordered (dict) so trimming evicts the OLDEST ids, not an
        # arbitrary half (set iteration order is hash-based, not insertion order)
        self._seen_ids: dict[str, None] = {}
        self._MAX_SEEN = 500
        self._last_context: dict[str, str] = {}

        # Durable cursor. The token fingerprint guards against replaying a
        # cursor minted for a different bot after a fresh QR login — iLink
        # rejects a foreign ``get_updates_buf`` with a non-zero ``ret``.
        self._state_path = _resolve_state_path(state_path)
        self._state_key = hashlib.sha256(bot_token.encode("utf-8")).hexdigest()[:16]
        self._save_warned = False
        self._load_state()

    # ------------------------------------------------------------------
    # Durable cursor
    # ------------------------------------------------------------------
    def _load_state(self) -> None:
        """Restore the cursor + dedup set from disk (best-effort).

        A missing, corrupt, foreign or unreadable file degrades to the
        previous in-memory-only behaviour — it must never stop the gateway
        from starting.
        """
        path = self._state_path
        if path is None or not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("state file is not a JSON object")
            if data.get("token") != self._state_key:
                logger.info(
                    "WeixinPoller: %s belongs to a different bot_token — "
                    "starting from a clean cursor", path,
                )
                return
            cursor = data.get("cursor")
            if isinstance(cursor, str):
                self._cursor = cursor
            seen = data.get("seen_ids")
            if isinstance(seen, list):
                for msg_id in seen[-self._MAX_SEEN:]:
                    if isinstance(msg_id, str):
                        self._seen_ids[msg_id] = None
            logger.info(
                "WeixinPoller resumed from %s (%d seen ids)",
                path, len(self._seen_ids),
            )
        except Exception as exc:
            logger.warning(
                "WeixinPoller: ignoring unusable state file %s (%s) — "
                "continuing with an in-memory cursor", path, exc,
            )

    def _save_state(self) -> None:
        """Write the cursor atomically (temp file + ``os.replace``).

        Called on every cursor advance rather than on a timer: inbound IM
        traffic is human-paced and each message already costs a multi-second
        LLM turn, so a ~10 kB atomic write is noise — whereas coalescing
        would leave open exactly the duplicate-reply window this closes.
        """
        path = self._state_path
        if path is None:
            return
        payload = {
            "token": self._state_key,
            "cursor": self._cursor,
            # Bounded so the file cannot grow without limit.
            "seen_ids": list(self._seen_ids)[-self._MAX_SEEN:],
        }
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        except Exception as exc:
            tmp.unlink(missing_ok=True)
            # Log once — a broken disk must not spam the poll loop.
            if not self._save_warned:
                self._save_warned = True
                logger.warning(
                    "WeixinPoller: cannot persist cursor to %s: %s", path, exc,
                )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(_LONG_POLL_HTTP_TIMEOUT)
        )
        logger.info("WeixinPoller started")

    async def stop(self) -> None:
        self._running = False
        if self._client:
            await self._client.aclose()
            self._client = None
        logger.info("WeixinPoller stopped")

    def latest_context_token(self, user_id: str) -> str | None:
        """Return the most recent context_token observed for ``user_id``."""
        return self._last_context.get(user_id)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    async def poll_loop(self) -> None:
        backoff = 1.0
        while self._running:
            try:
                updates = await self._fetch_updates()
                if updates:
                    backoff = 1.0
                    for upd in updates:
                        await self._process_update(upd)
            except asyncio.CancelledError:
                logger.info("WeixinPoller cancelled")
                break
            except Exception as exc:
                logger.warning(
                    "WeixinPoller error (retry in %.0fs): %s", backoff, exc,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    async def _fetch_updates(self) -> list[dict[str, Any]]:
        if not self._client:
            return []
        # iLink controls the long-poll hold itself (~35 s); the request body
        # only carries the resume cursor + the channel-version handshake.
        body: dict[str, Any] = {
            "get_updates_buf": self._cursor,
            "base_info": {"channel_version": CHANNEL_VERSION},
        }

        resp = await self._client.post(
            f"{ILINK_BASE}/ilink/bot/getupdates",
            json=body,
            headers=common_headers(self._token),
        )
        if resp.status_code == 401:
            raise RuntimeError(
                "iLink: 401 Unauthorized — bot_token expired. "
                "Re-run `python -m backend.weixin.qr_login`."
            )
        if resp.status_code != 200:
            raise RuntimeError(
                f"iLink getupdates HTTP {resp.status_code}: {resp.text[:200]}"
            )
        data = resp.json()
        # ``ret`` is iLink's app-level status code; non-zero means the
        # request was understood but rejected (e.g. quota, bad cursor).
        ret = data.get("ret")
        if ret not in (None, 0):
            raise RuntimeError(
                f"iLink getupdates app error: ret={ret} "
                f"err_msg={data.get('err_msg', '')!r}"
            )
        nxt = data.get("get_updates_buf") or ""
        if nxt and nxt != self._cursor:
            self._cursor = nxt
            self._save_state()
        return data.get("msgs") or []

    async def _process_update(self, update: dict[str, Any]) -> None:
        msg_id = str(update.get("message_id") or update.get("id") or "")
        if msg_id and msg_id in self._seen_ids:
            return
        if msg_id:
            self._seen_ids[msg_id] = None
            if len(self._seen_ids) > self._MAX_SEEN:
                # evict the oldest half (dict preserves insertion order)
                for old in list(self._seen_ids)[: self._MAX_SEEN // 2]:
                    del self._seen_ids[old]
            # Persist *before* dispatching: a crash mid-handler must not
            # replay the message (and re-run whatever command it carried).
            self._save_state()

        from_user = str(update.get("from_user_id") or "")
        if not from_user:
            return

        if self._allowed is not None and from_user not in self._allowed:
            logger.warning(
                "WeChat: rejecting message from non-whitelisted user %s "
                "(set WEIXIN_ALLOWED_USER_IDS to allow)",
                from_user,
            )
            return

        ctx_token = str(update.get("context_token") or "")
        if ctx_token:
            self._last_context[from_user] = ctx_token

        text = _extract_text(update)
        if not text:
            return

        # iLink updates don't always carry a timestamp; fall back to wall time
        # rather than 0, which would resolve to 1970-01-01 and poison the
        # chat-history timestamp prefix the agent uses to ground "today".
        ts_raw = update.get("timestamp") or update.get("ts")
        if ts_raw:
            try:
                ts = datetime.fromtimestamp(int(ts_raw), tz=timezone.utc)
            except (TypeError, ValueError, OSError):
                ts = datetime.now(timezone.utc)
        else:
            ts = datetime.now(timezone.utc)

        envelope = MessageEnvelope(
            message_id=msg_id or f"weixin:{ts.timestamp()}",
            channel=MessageChannel.WEIXIN,
            sender_id=from_user,
            content=text,
            timestamp=ts,
            # WeChat ClawBot is 1-on-1 — the user ID *is* the chat.
            chat_id=from_user,
            conversation_id=from_user,
            platform_message_id=msg_id,
            metadata={
                "context_token": ctx_token,
                "raw": update,
            },
        )

        try:
            await self._on_message(envelope)
        except Exception as exc:
            logger.error(
                "WeixinPoller dispatch error: %s", exc, exc_info=True,
            )


def _extract_text(update: dict[str, Any]) -> str:
    """Pull the text payload out of an iLink message.

    Each entry in ``item_list`` is a typed wrapper — text items expose the
    body at ``text_item.text``. Non-text items (image/file/audio/video)
    are intentionally ignored; a future revision can wire them into the
    agent once the AES-128-ECB CDN handshake is implemented.
    """
    parts: list[str] = []
    for item in update.get("item_list") or []:
        if not isinstance(item, dict):
            continue
        text_item = item.get("text_item")
        if isinstance(text_item, dict):
            text = text_item.get("text") or ""
            if text:
                parts.append(text)
            continue
        logger.info(
            "WeChat: skipping non-text item type=%s keys=%s",
            item.get("type"), list(item.keys()),
        )
    return "\n".join(parts).strip()
