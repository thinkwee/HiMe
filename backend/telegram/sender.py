"""
TelegramSender — async, non-blocking message sender.

Extracted from the original ``push_report_tool._notify_telegram`` so that both
the report tool and the new Gateway can share the same sending logic.

All outbound text is converted from Markdown to Telegram-compatible HTML
before sending (headings, lists, tables, bold, italic, code blocks).
"""
from __future__ import annotations

import logging
import re
import time

import httpx

logger = logging.getLogger(__name__)

# Telegram hard limit for a single message
_MAX_MESSAGE_LENGTH = 4096
_TRUNCATION_SUFFIX = "\n\n<i>…message truncated</i>"

# ---------------------------------------------------------------------------
# Markdown → Telegram HTML conversion
# ---------------------------------------------------------------------------

_CODE_BLOCK_RE = re.compile(r"```(\w*)\n?([\s\S]*?)```")
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_BOLD_DBL_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_BOLD_SGL_RE = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
_ITALIC_RE = re.compile(r"(?<![_\w])_([^_]+)_(?![_\w])")
_STRIKE_RE = re.compile(r"~~(.+?)~~")


def _markdown_to_telegram_html(text: str) -> str:
    """Convert Markdown text to Telegram-compatible HTML.

    Handles: bold (``*`` / ``**``), italic (``_``), strikethrough (``~~``),
    code / code blocks, headings (→ bold), unordered lists (→ bullet char ``•``),
    and tables (→ ``<pre>`` block).
    """
    # --- 1. Extract code blocks to protect their content ----------------
    code_blocks: list[tuple[str, str]] = []

    def _save_cb(m: re.Match) -> str:
        code_blocks.append((m.group(1), m.group(2)))
        return f"\x00CB{len(code_blocks) - 1}\x00"

    text = _CODE_BLOCK_RE.sub(_save_cb, text)

    # --- 2. Extract inline code ----------------------------------------
    inline_codes: list[str] = []

    def _save_ic(m: re.Match) -> str:
        inline_codes.append(m.group(1))
        return f"\x00IC{len(inline_codes) - 1}\x00"

    text = _INLINE_CODE_RE.sub(_save_ic, text)

    # --- 3. Escape HTML entities (only in non-code text) ---------------
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")

    # --- 4. Line-level elements ----------------------------------------
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()

        # Headings → bold
        hm = re.match(r"^#{1,6}\s+(.+)", stripped)
        if hm:
            out.append(f"\n<b>{hm.group(1)}</b>")
            i += 1
            continue

        # Tables → <pre> block
        if (
            stripped.startswith("|")
            and stripped.endswith("|")
            and stripped.count("|") >= 3
        ):
            table: list[str] = []
            while i < len(lines):
                s = lines[i].strip()
                if s.startswith("|") and s.endswith("|"):
                    table.append(lines[i])
                    i += 1
                else:
                    break
            if table:
                out.append("<pre>" + "\n".join(table) + "</pre>")
            continue

        # Unordered list items → bullet char
        lm = re.match(r"^(\s*)[-*+]\s+(.*)", lines[i])
        if lm:
            out.append(f"{lm.group(1)}\u2022 {lm.group(2)}")
            i += 1
            continue

        out.append(lines[i])
        i += 1

    text = "\n".join(out)

    # --- 5. Inline formatting ------------------------------------------
    text = _BOLD_DBL_RE.sub(r"<b>\1</b>", text)   # **text**
    text = _BOLD_SGL_RE.sub(r"<b>\1</b>", text)   # *text*
    text = _ITALIC_RE.sub(r"<i>\1</i>", text)     # _text_
    text = _STRIKE_RE.sub(r"<s>\1</s>", text)     # ~~text~~

    # --- 6. Restore code blocks (escaped) ------------------------------
    for idx, (_lang, code) in enumerate(code_blocks):
        esc = code.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        text = text.replace(f"\x00CB{idx}\x00", f"<pre>{esc}</pre>")

    # --- 7. Restore inline code (escaped) ------------------------------
    for idx, code in enumerate(inline_codes):
        esc = code.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        text = text.replace(f"\x00IC{idx}\x00", f"<code>{esc}</code>")

    return text


class TelegramSender:
    """
    Thin async wrapper around ``POST /bot<token>/sendMessage``.

    Keeps an ``httpx.AsyncClient`` alive for connection reuse (important when
    sending many messages in quick succession, e.g. during benchmarking).
    """

    def __init__(self, token: str, default_chat_id: str | None = None) -> None:
        self._token = token
        self._default_chat_id = default_chat_id
        self._base_url = f"https://api.telegram.org/bot{token}"
        self._client: httpx.AsyncClient | None = None
        self._muted_until: float | None = None  # Unix timestamp; None = not muted

    # ------------------------------------------------------------------
    # Mute control (suppress autonomous report pushes)
    # ------------------------------------------------------------------

    def mute(self, duration_seconds: float) -> None:
        self._muted_until = time.time() + duration_seconds

    def unmute(self) -> None:
        self._muted_until = None

    def is_muted(self) -> bool:
        if self._muted_until is None:
            return False
        if time.time() >= self._muted_until:
            self._muted_until = None
            return False
        return True

    def mute_remaining(self) -> float | None:
        if not self.is_muted():
            return None
        return self._muted_until - time.time()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Create the shared HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=15.0)
            logger.info("TelegramSender started")

    async def stop(self) -> None:
        """Close the HTTP client gracefully."""
        if self._client:
            await self._client.aclose()
            self._client = None
            logger.info("TelegramSender stopped")

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------

    async def send_message(
        self,
        text: str,
        chat_id: str | None = None,
        parse_mode: str = "HTML",
        reply_to_message_id: int | None = None,
        reply_markup: dict | None = None,
    ) -> bool:
        """
        Send a text message.  Returns ``True`` on success.

        Falls back to ``default_chat_id`` when *chat_id* is not supplied.
        Messages longer than Telegram's 4 096-char limit are truncated.
        ``reply_markup`` is an optional Telegram InlineKeyboardMarkup dict.
        """
        target = chat_id or self._default_chat_id
        if not target:
            logger.warning("TelegramSender.send_message: no chat_id available")
            return False

        # Convert Markdown → Telegram HTML
        text = _markdown_to_telegram_html(text)

        # Truncate if needed
        if len(text) > _MAX_MESSAGE_LENGTH - len(_TRUNCATION_SUFFIX):
            text = text[: _MAX_MESSAGE_LENGTH - len(_TRUNCATION_SUFFIX)] + _TRUNCATION_SUFFIX

        payload: dict = {
            "chat_id": target,
            "text": text,
            "parse_mode": parse_mode,
        }
        if reply_to_message_id is not None:
            payload["reply_to_message_id"] = reply_to_message_id
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup

        client = self._client or httpx.AsyncClient(timeout=15.0)
        try:
            resp = await client.post(f"{self._base_url}/sendMessage", json=payload)
            if resp.status_code == 200:
                logger.debug("Telegram message sent to %s", target)
                return True

            # Retry without parse_mode if HTML parsing fails
            if resp.status_code == 400 and "parse" in resp.text.lower():
                payload["parse_mode"] = ""
                resp2 = await client.post(f"{self._base_url}/sendMessage", json=payload)
                if resp2.status_code == 200:
                    logger.debug("Telegram message sent (plain) to %s", target)
                    return True
            logger.warning(
                "Telegram send failed (%d) to chat_id=%s: %s",
                resp.status_code, target, resp.text[:500],
            )
            return False
        except Exception as exc:
            logger.warning("Telegram send error: %s", exc)
            return False
        finally:
            # If we created a one-shot client, close it
            if self._client is None:
                await client.aclose()

    async def send_photo(
        self,
        photo_path: str,
        caption: str = "",
        chat_id: str | None = None,
        parse_mode: str = "HTML",
        reply_markup: dict | None = None,
    ) -> bool:
        """Send a photo with optional caption. Returns True on success."""
        target = chat_id or self._default_chat_id
        if not target:
            logger.warning("TelegramSender.send_photo: no chat_id available")
            return False

        if caption:
            caption = _markdown_to_telegram_html(caption)
            if len(caption) > 1024:
                caption = caption[:1020] + "..."

        data: dict = {"chat_id": target, "caption": caption, "parse_mode": parse_mode}
        if reply_markup:
            import json
            data["reply_markup"] = json.dumps(reply_markup)

        client = self._client or httpx.AsyncClient(timeout=30.0)
        try:
            with open(photo_path, "rb") as f:
                resp = await client.post(
                    f"{self._base_url}/sendPhoto",
                    data=data,
                    files={"photo": ("chart.png", f, "image/png")},
                )
            if resp.status_code == 200:
                logger.debug("Telegram photo sent to %s", target)
                return True
            logger.warning("Telegram photo failed (%d): %s", resp.status_code, resp.text[:300])
            return False
        except Exception as exc:
            logger.warning("Telegram photo error: %s", exc)
            return False
        finally:
            if self._client is None:
                await client.aclose()

    async def edit_message_text(
        self,
        chat_id: str,
        message_id: int,
        text: str,
        parse_mode: str = "HTML",
        reply_markup: dict | None = None,
    ) -> bool:
        """Edit an existing message's text.  Returns ``True`` on success."""
        # Convert Markdown → Telegram HTML
        text = _markdown_to_telegram_html(text)

        if len(text) > _MAX_MESSAGE_LENGTH - len(_TRUNCATION_SUFFIX):
            text = text[: _MAX_MESSAGE_LENGTH - len(_TRUNCATION_SUFFIX)] + _TRUNCATION_SUFFIX

        payload: dict = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": parse_mode,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup

        client = self._client or httpx.AsyncClient(timeout=15.0)
        try:
            resp = await client.post(f"{self._base_url}/editMessageText", json=payload)
            if resp.status_code == 200:
                return True

            # Retry without parse_mode if HTML parsing fails
            if resp.status_code == 400 and "parse" in resp.text.lower():
                payload["parse_mode"] = ""
                resp2 = await client.post(
                    f"{self._base_url}/editMessageText", json=payload
                )
                if resp2.status_code == 200:
                    return True
            logger.warning(
                "Telegram edit failed (%d): %s",
                resp.status_code,
                resp.text[:500],
            )
            return False
        except Exception as exc:
            logger.warning("Telegram edit error: %s", exc)
            return False
        finally:
            if self._client is None:
                await client.aclose()

    async def send_chat_action(
        self,
        chat_id: str,
        action: str = "typing",
    ) -> bool:
        """Send a chat action (e.g. 'typing' indicator).

        The indicator auto-expires after 5 seconds or when a message is sent.
        """
        client = self._client or httpx.AsyncClient(timeout=5.0)
        try:
            resp = await client.post(
                f"{self._base_url}/sendChatAction",
                json={"chat_id": chat_id, "action": action},
            )
            return resp.status_code == 200
        except Exception:
            return False  # typing indicator is best-effort
        finally:
            if self._client is None:
                await client.aclose()

    async def answer_callback_query(
        self,
        callback_query_id: str,
        text: str = "",
        show_alert: bool = False,
    ) -> bool:
        """Answer a Telegram callback query (from inline keyboard buttons)."""
        client = self._client or httpx.AsyncClient(timeout=15.0)
        try:
            payload = {
                "callback_query_id": callback_query_id,
                "text": text[:200] if text else "",
                "show_alert": show_alert,
            }
            resp = await client.post(f"{self._base_url}/answerCallbackQuery", json=payload)
            return resp.status_code == 200
        except Exception as exc:
            logger.debug("answerCallbackQuery error: %s", exc)
            return False
        finally:
            if self._client is None:
                await client.aclose()
