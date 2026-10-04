"""
Helpers for the chat event contract shared by iOS and the web monitor.

Chat event stream (all events carry ``chat_id``; those emitted while a user
message is being handled also carry ``run_id`` -- one uuid per handled message,
stamped centrally by ``AutonomousHealthAgent._emit``):

  chat_thinking / chat_content   LLM chunks (``content``)
  chat_reply_delta               ``{text, reset?}`` -- the reply_user ``message``
                                 argument decoded so far (full text, not a
                                 diff). ``reset: true`` with ``text: ""`` means
                                 the streamed reply was not delivered (blocked
                                 / rejected / provider retried): clear it.
  chat_tool_call                 ``{tool, call_id?, summary, status:"running",
                                 arguments, parent?}``
  chat_tool_result               ``{tool, call_id?, success, status:"ok"|"error",
                                 result_preview (<=300 chars), result, parent?}``
  chat_reply                     final delivered reply (as before)
  chat_stopped                   the run was cancelled by POST /chat/stop

``parent`` is "analyze"/"manage" on events emitted by a sub-agent, so clients
can nest those steps under the orchestrator's analyze/manage step.
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

PREVIEW_CHARS = 300
SUMMARY_CHARS = 200

_SIMPLE_ESCAPES = {
    '"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f",
    "n": "\n", "r": "\r", "t": "\t",
}


def _read_string(buf: str, i: int) -> tuple[str, int, bool]:
    """Decode a JSON string body starting just after the opening quote.

    Returns ``(decoded, next_index, closed)``. For an unterminated string the
    decoded prefix is returned (a dangling partial escape or a high surrogate
    awaiting its pair is withheld), ``closed`` False.
    """
    out: list[str] = []
    n = len(buf)
    while i < n:
        c = buf[i]
        if c == '"':
            return "".join(out), i + 1, True
        if c != "\\":
            out.append(c)
            i += 1
            continue
        if i + 1 >= n:
            break  # dangling backslash
        e = buf[i + 1]
        if e != "u":
            out.append(_SIMPLE_ESCAPES.get(e, e))
            i += 2
            continue
        hexs = buf[i + 2:i + 6]
        if len(hexs) < 4:
            break
        try:
            cp = int(hexs, 16)
        except ValueError:
            out.append("�")
            i += 6
            continue
        if 0xD800 <= cp < 0xDC00:  # high surrogate: needs a \uDC00-DFFF next
            nxt = buf[i + 6:i + 12]
            if len(nxt) < 6 and "\\u".startswith(nxt[:2]):
                break  # pair not fully received yet
            lo = -1
            if nxt.startswith("\\u"):
                try:
                    lo = int(nxt[2:6], 16)
                except ValueError:
                    lo = -1
            if 0xDC00 <= lo < 0xE000:
                out.append(chr(0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00)))
                i += 12
            else:
                out.append("�")
                i += 6
            continue
        out.append("�" if 0xDC00 <= cp < 0xE000 else chr(cp))
        i += 6
    return "".join(out), i, False


def extract_partial_string(buf: str, key: str = "message") -> str | None:
    """Decoded prefix of the top-level string value ``key`` in partial JSON.

    ``buf`` is a (possibly truncated) JSON object text. Returns ``None`` when
    the key has not appeared yet or its value is not a string; ``""`` when the
    value string has just opened. Never raises.
    """
    n = len(buf)
    i = 0
    depth = 0
    while i < n:
        c = buf[i]
        if c == '"':
            s, i, closed = _read_string(buf, i + 1)
            if not closed:
                return None
            if depth == 1 and s == key:
                j = i
                while j < n and buf[j] in " \t\r\n":
                    j += 1
                if j >= n or buf[j] != ":":
                    continue
                j += 1
                while j < n and buf[j] in " \t\r\n":
                    j += 1
                if j >= n:
                    return ""
                if buf[j] != '"':
                    return None
                return _read_string(buf, j + 1)[0]
            continue
        if c in "{[":
            depth += 1
        elif c in "}]":
            depth -= 1
        i += 1
    return None


class ReplyDeltaStreamer:
    """Turn ``tool_call_delta`` chunks of the first ``reply_user`` call into
    throttled "full text so far" snapshots. One instance per LLM call."""

    def __init__(
        self,
        tool_name: str = "reply_user",
        key: str = "message",
        min_interval: float = 0.15,
        min_chars: int = 40,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.tool_name = tool_name
        self.key = key
        self.min_interval = min_interval
        self.min_chars = min_chars
        self._clock = clock
        self._index: int | None = None
        self._buf = ""
        self._last_text = ""
        self._last_emit = 0.0
        self.emitted = False

    def _text(self) -> str:
        return extract_partial_string(self._buf, self.key) or ""

    def feed(self, chunk: dict) -> str | None:
        """Consume one chunk; return a snapshot to emit now, or None."""
        if chunk.get("name") != self.tool_name:
            return None
        idx = chunk.get("index")
        if self._index is None:
            self._index = idx
        elif idx != self._index:
            return None
        self._buf += chunk.get("arguments_delta") or ""
        text = self._text()
        if not text or text == self._last_text:
            return None
        now = self._clock()
        if (
            self.emitted
            and now - self._last_emit < self.min_interval
            and len(text) - len(self._last_text) < self.min_chars
        ):
            return None
        return self._mark(text, now)

    def finish(self) -> str | None:
        """Final snapshot if the last emitted one is stale."""
        text = self._text()
        if not text or text == self._last_text:
            return None
        return self._mark(text, self._clock())

    def _mark(self, text: str, now: float) -> str:
        self._last_text = text
        self._last_emit = now
        self.emitted = True
        return text


def summarize_tool_args(tool: str, arguments: Any) -> str:
    """Short, human-safe one-liner describing a tool call for a step list."""
    if not isinstance(arguments, dict):
        return ""
    for key in ("goal", "query", "message", "name", "filename", "page_id", "title"):
        v = arguments.get(key)
        if isinstance(v, str) and v.strip():
            return " ".join(v.split())[:SUMMARY_CHARS]
    code = arguments.get("code")
    if isinstance(code, str) and code.strip():
        first = next((ln.strip() for ln in code.splitlines() if ln.strip()), "")
        return first[:SUMMARY_CHARS]
    return ""


def result_preview(result: Any, limit: int = PREVIEW_CHARS) -> str:
    """Truncated text preview of a tool result."""
    if isinstance(result, str):
        text = result
    else:
        try:
            text = json.dumps(result, ensure_ascii=False, default=str)
        except Exception:
            text = str(result)
    return text[:limit]
