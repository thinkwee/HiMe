"""
TelegramPoller — long-polling consumer for Telegram Bot API updates.

Design notes (inspired by OpenClaw):
  - Maintains a monotonically-increasing ``offset`` so messages are never
    processed twice.
  - The offset and the dedup set are mirrored to disk, so a restart resumes
    where the previous process stopped instead of re-processing (and
    re-answering) updates Telegram had already handed over.
  - Uses ``httpx.AsyncClient`` for non-blocking I/O.
  - Normalises every incoming Telegram message into a ``MessageEnvelope``
    before handing it off to the router callback.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from collections.abc import Callable, Coroutine
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from .models import MessageChannel, MessageEnvelope

logger = logging.getLogger(__name__)

_STATE_FILENAME = "telegram_poller_state.json"


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
        logger.warning("TelegramPoller: cursor persistence disabled: %s", exc)
        return None


class TelegramPoller:
    """
    Long-poll the Telegram ``getUpdates`` endpoint and dispatch envelopes.

    Parameters
    ----------
    token : str
        The Bot API token.
    on_message : callable
        ``async def on_message(envelope: MessageEnvelope) -> None`` — called
        for every incoming message.
    poll_timeout : int
        Telegram long-poll timeout in seconds (default 30).
    allowed_chat_ids : set[str] | None
        If set, only messages from these chat IDs are forwarded.  All others
        are silently dropped (security whitelist).
    state_path : Path | str | None
        Where to persist the offset + dedup set.  ``None`` (the default)
        resolves via :func:`_resolve_state_path`.
    """

    def __init__(
        self,
        token: str,
        on_message: Callable[[MessageEnvelope], Coroutine],
        poll_timeout: int = 30,
        allowed_chat_ids: set | None = None,
        on_callback_query: Callable | None = None,
        state_path: Path | str | None = None,
    ) -> None:
        self._token = token
        self._on_message = on_message
        self._on_callback_query = on_callback_query
        self._poll_timeout = poll_timeout
        self._allowed_chat_ids = allowed_chat_ids
        self._base_url = f"https://api.telegram.org/bot{token}"
        self._offset: int = 0
        self._running = False
        self._client: httpx.AsyncClient | None = None
        # Simple deduplication: remember the last N message IDs. Use an
        # insertion-ordered dict (not a set) so the "keep most recent half"
        # trim below actually evicts the OLDEST keys — set iteration order is
        # hash-based, so trimming a set can drop the newest keys and let a
        # re-delivered update be processed (and answered) twice.
        self._seen_ids: dict[str, None] = {}
        self._MAX_SEEN = 500

        # Durable cursor. The token fingerprint guards against resuming
        # another bot's offset (``update_id`` is a per-bot sequence) after
        # the operator swaps TELEGRAM_TOKEN.
        self._state_path = _resolve_state_path(state_path)
        self._state_key = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
        self._save_warned = False
        self._load_state()

    # ------------------------------------------------------------------
    # Durable cursor
    # ------------------------------------------------------------------

    def _load_state(self) -> None:
        """Restore the offset + dedup set from disk (best-effort).

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
                    "TelegramPoller: %s belongs to a different bot token — "
                    "starting from a clean cursor", path,
                )
                return
            offset = data.get("offset")
            if isinstance(offset, int) and not isinstance(offset, bool) and offset > 0:
                self._offset = offset
            seen = data.get("seen_ids")
            if isinstance(seen, list):
                for key in seen[-self._MAX_SEEN:]:
                    if isinstance(key, str):
                        self._seen_ids[key] = None
            logger.info(
                "TelegramPoller resumed from %s (offset=%d, %d seen ids)",
                path, self._offset, len(self._seen_ids),
            )
        except Exception as exc:
            logger.warning(
                "TelegramPoller: ignoring unusable state file %s (%s) — "
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
            "offset": self._offset,
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
                    "TelegramPoller: cannot persist cursor to %s: %s", path, exc,
                )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Begin the polling loop (non-blocking — returns immediately)."""
        if self._running:
            return
        self._running = True
        # Use a generous timeout for long-poll requests
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(self._poll_timeout + 10))
        logger.info("TelegramPoller started (timeout=%ds)", self._poll_timeout)

    async def stop(self) -> None:
        """Signal the polling loop to exit."""
        self._running = False
        if self._client:
            await self._client.aclose()
            self._client = None
        logger.info("TelegramPoller stopped")

    async def poll_loop(self) -> None:
        """
        Main poll loop — call ``start()`` first, then ``await poll_loop()``.

        Runs until ``stop()`` is called.  Errors are caught and retried with
        exponential back-off (capped at 60 s).
        """
        backoff = 1.0
        while self._running:
            try:
                updates = await self._fetch_updates()
                if updates:
                    backoff = 1.0  # reset on success
                    for update in updates:
                        await self._process_update(update)
                else:
                    # No updates — normal, just loop
                    pass
            except asyncio.CancelledError:
                logger.info("Poller cancelled")
                break
            except Exception as exc:
                logger.warning("Poller error (retry in %.0fs): %s", backoff, exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _fetch_updates(self) -> list[dict[str, Any]]:
        """Call ``getUpdates`` and advance the offset."""
        if not self._client:
            return []

        params: dict[str, Any] = {
            "timeout": self._poll_timeout,
            # The Bot API wants a JSON-serialised array here. Passing a Python
            # list makes httpx emit repeated query params
            # (?allowed_updates=message&allowed_updates=callback_query), which
            # Telegram rejects — so the filter silently stopped applying.
            "allowed_updates": json.dumps(["message", "callback_query"]),
        }
        if self._offset:
            params["offset"] = self._offset

        resp = await self._client.get(f"{self._base_url}/getUpdates", params=params)
        data = resp.json()

        if not data.get("ok"):
            raise RuntimeError(f"Telegram API error: {data}")

        results: list[dict] = data.get("result", [])
        if results:
            # Advance offset past the last received update
            self._offset = results[-1]["update_id"] + 1
            self._save_state()
        return results

    async def _process_update(self, update: dict[str, Any]) -> None:
        """Convert a raw Telegram update into an ``MessageEnvelope`` and dispatch."""
        # Handle callback queries (inline keyboard button presses)
        callback_query = update.get("callback_query")
        if callback_query and self._on_callback_query:
            # Default-deny applies to button callbacks too — they bypass the
            # message-branch allowlist check below, and the handler surfaces
            # the user's private evidence trail. Reject callbacks from chats
            # that aren't whitelisted.
            cb_chat_id = str(
                (callback_query.get("message") or {}).get("chat", {}).get("id", "")
            )
            if self._allowed_chat_ids is not None and cb_chat_id not in self._allowed_chat_ids:
                logger.warning(
                    "Telegram: rejecting callback from non-whitelisted chat %s", cb_chat_id,
                )
                return
            try:
                await self._on_callback_query(callback_query)
            except Exception as exc:
                logger.error("Error handling callback query: %s", exc, exc_info=True)
            return

        msg = update.get("message")
        if not msg:
            return

        text = msg.get("text", "")
        if not text:
            return  # ignore non-text messages (photos, stickers, etc.)

        chat = msg.get("chat", {})
        chat_id = str(chat.get("id", ""))
        message_id = msg.get("message_id")

        # Security: chat ID whitelist (default-deny when a set is provided).
        # Passing ``None`` disables the whitelist (allow all). Passing an
        # empty set denies everything — this is what main.py does when no
        # chat_id / TELEGRAM_ALLOWED_CHAT_IDS are configured.
        if self._allowed_chat_ids is not None and chat_id not in self._allowed_chat_ids:
            logger.warning(
                "Telegram: rejecting message from non-whitelisted chat %s "
                "(set TELEGRAM_ALLOWED_CHAT_IDS or chat_id in .env to allow)",
                chat_id,
            )
            return

        # Deduplication
        dedup_key = f"{chat_id}:{message_id}"
        if dedup_key in self._seen_ids:
            return
        self._seen_ids[dedup_key] = None
        if len(self._seen_ids) > self._MAX_SEEN:
            # Trim the oldest half (dict preserves insertion order)
            for _k in list(self._seen_ids)[: self._MAX_SEEN // 2]:
                del self._seen_ids[_k]
        # Persist *before* dispatching: a crash mid-handler must not replay
        # the message (and re-run whatever command it carried) on restart.
        self._save_state()

        # Build envelope
        sender = msg.get("from", {})
        sender_name = sender.get("first_name", "")
        if sender.get("last_name"):
            sender_name += f" {sender['last_name']}"

        ts = datetime.fromtimestamp(msg.get("date", 0), tz=timezone.utc)

        envelope = MessageEnvelope(
            message_id=dedup_key,
            channel=MessageChannel.TELEGRAM,
            sender_id=str(sender.get("id", "")),
            content=text,
            timestamp=ts,
            chat_id=chat_id,
            telegram_message_id=message_id,
            metadata={
                "sender_name": sender_name,
                "chat_type": chat.get("type", "private"),
                "chat_title": chat.get("title", ""),
            },
        )

        try:
            await self._on_message(envelope)
        except Exception as exc:
            logger.error("Error dispatching message: %s", exc, exc_info=True)
