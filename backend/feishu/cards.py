"""Feishu interactive-card builders.

Feishu's "interactive cards" are structured JSON documents rendered natively
by the Lark client. They are analogous to Telegram inline keyboards but
richer — each card carries a header, a body composed of text / divider /
action blocks, and optional button actions that fire a ``card.action.trigger``
event back to the bot.

To keep parity with the Telegram "Show Evidence" / "Back" toggle, each card
embeds the same ``message_hash`` used by :class:`FactVerifier` in the button
``value`` field. The webhook / ws transport forwards these as callbacks to
:meth:`FeishuGateway._handle_card_action`.

The raw JSON returned here matches the ``message_card`` schema accepted by
``im.v1.message.patch`` and ``im.v1.message.create`` (``msg_type="interactive"``).
"""
from __future__ import annotations

from typing import Any

# Feishu silently truncates ``lark_md`` content beyond ~2000 characters in a
# single ``div`` element.  We split on paragraph boundaries to stay within the
# limit while keeping the message readable.
_FEISHU_ELEMENT_CHAR_LIMIT = 1800  # conservative — well under the ~2 000 cap


def _plain_text_element(text: str) -> dict[str, Any]:
    """Return a plain-text markdown element for a card body."""
    return {
        "tag": "div",
        "text": {
            "tag": "lark_md",
            "content": text or "",
        },
    }


def _split_text_elements(text: str) -> list[dict[str, Any]]:
    """Split *text* into one or more ``div`` elements that each fit within
    Feishu's per-element character limit.

    Splitting is done on double-newline (paragraph) boundaries so that logical
    blocks stay together.  If a single paragraph exceeds the limit it is split
    on single-newline boundaries, and as a last resort on hard character
    offsets.
    """
    if not text or len(text) <= _FEISHU_ELEMENT_CHAR_LIMIT:
        return [_plain_text_element(text)]

    elements: list[dict[str, Any]] = []
    chunks = _split_to_chunks(text, _FEISHU_ELEMENT_CHAR_LIMIT)
    for chunk in chunks:
        elements.append(_plain_text_element(chunk))
    return elements


def _split_to_chunks(text: str, limit: int) -> list[str]:
    """Return a list of text chunks each ≤ *limit* characters."""
    # Try splitting on paragraph boundaries first (\n\n)
    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    current = ""

    for para in paragraphs:
        candidate = f"{current}\n\n{para}" if current else para
        if len(candidate) <= limit:
            current = candidate
        else:
            if current:
                chunks.append(current)
            # If a single paragraph itself is too long, split on newlines
            if len(para) > limit:
                sub_lines = para.split("\n")
                current = ""
                for line in sub_lines:
                    candidate2 = f"{current}\n{line}" if current else line
                    if len(candidate2) <= limit:
                        current = candidate2
                    else:
                        if current:
                            chunks.append(current)
                        # Hard split if a single line is too long
                        while len(line) > limit:
                            chunks.append(line[:limit])
                            line = line[limit:]
                        current = line
            else:
                current = para

    if current:
        chunks.append(current)
    return chunks


def _button(text: str, action: str, message_hash: str) -> dict[str, Any]:
    """Build a card button whose click payload carries ``(action, hash)``."""
    return {
        "tag": "button",
        "text": {
            "tag": "plain_text",
            "content": text,
        },
        "type": "primary",
        "value": {
            "action": action,
            "hash": message_hash,
        },
    }


def _header(title: str, emoji: str = "") -> dict[str, Any]:
    """Return a card header block with an optional leading emoji."""
    title_text = f"{emoji} {title}".strip() if emoji else title
    return {
        "template": "blue",
        "title": {
            "tag": "plain_text",
            "content": title_text,
        },
    }


def build_plain_card(message_text: str) -> dict[str, Any]:
    """Return a minimal interactive card with only a text body."""
    return {
        "config": {"wide_screen_mode": True},
        "elements": _split_text_elements(message_text),
    }


def build_evidence_card(
    message_text: str,
    message_hash: str,
    status_emoji: str = "\U0001f4ca",  # 📊
) -> dict[str, Any]:
    """Build the "original message + Show Evidence button" card.

    Parameters
    ----------
    message_text:
        The user-facing message body (already converted to plain / lark_md).
    message_hash:
        Stable hash produced by :class:`FactVerifier` — used to look up the
        stored tool-call trail when the user taps the button.
    status_emoji:
        Leading emoji for the card header (defaults to the bar chart).
    """
    body_elements = _split_text_elements(message_text)
    return {
        "config": {"wide_screen_mode": True},
        "header": _header("Hime", status_emoji),
        "elements": body_elements + [
            {"tag": "hr"},
            {
                "tag": "action",
                "actions": [
                    _button("Show Evidence", "evidence", message_hash),
                ],
            },
        ],
    }


def build_back_card(message_text: str, message_hash: str) -> dict[str, Any]:
    """Build the "evidence view + Back button" card.

    Used when the user tapped "Show Evidence": the card body is replaced with
    the formatted evidence trail and the button toggles back to the original
    message view.
    """
    body_elements = _split_text_elements(message_text)
    return {
        "config": {"wide_screen_mode": True},
        "header": _header("Evidence", "\U0001f50d"),  # 🔍
        "elements": body_elements + [
            {"tag": "hr"},
            {
                "tag": "action",
                "actions": [
                    _button("\u21a9 Back", "restore", message_hash),
                ],
            },
        ],
    }
