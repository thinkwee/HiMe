"""
update_md tool — lets the agent edit the editable body of prompts/user.md
or prompts/experience.md.

Three operations, Edit-tool style:

* ``append`` (default) — add ``content`` to the end of the editable body.
  Safe, idempotent-ish, and the most common case.
* ``edit`` — replace ``old_string`` with ``new_string`` exactly once in the
  editable body. Lets the agent rewrite a single line or stanza without
  rewriting the whole file.
* ``replace`` — overwrite the entire editable body with ``content``. Use
  this when the agent wants to reorganise or prune accumulated notes.

The protected file header (everything up to and including the marker
comment) is never touched, regardless of ``op``.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from .base import BaseTool

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path("prompts")

_FILE_CONFIG = {
    "user.md": {
        "marker": "<!-- Agent: append your observations below this line. -->",
        "default_header": (
            "# User Profile\n\n"
            "> Written by the agent via `update_md`. Captures **stable** user\n"
            "> preferences, habits, and communication style learned over many\n"
            "> conversations. Not a chat log — append only durable signals.\n"
            "<!-- Agent: append your observations below this line. -->\n"
        ),
    },
    "experience.md": {
        "marker": "<!-- Agent: append your real learnings below this line. -->",
        "default_header": (
            "# Experience — Agent's Own Notes\n\n"
            "> Initially empty. Append things you have **actually learned** from\n"
            "> running on this user's data — surprising data quirks, gotchas you\n"
            "> hit and the workaround, schema details that aren't in\n"
            "> `data_schema.md`, edge cases worth remembering next time.\n\n"
            "<!-- Agent: append your real learnings below this line. -->\n"
        ),
    },
}


def _split_header_body(existing: str, marker: str, default_header: str) -> tuple[str, str]:
    """Return ``(header_including_marker, editable_body)``.

    If the file is missing the marker, the default header is used and the
    existing text becomes the body (so nothing is lost).
    """
    if marker in existing:
        header, _, body = existing.partition(marker)
        header = header.rstrip() + "\n" + marker
        return header, body.lstrip("\n")
    # Marker missing → fall back to default header, keep existing text as body.
    return default_header.rstrip(), existing.strip()


class UpdateMdTool(BaseTool):
    """Edit the editable body of ``prompts/user.md`` or ``prompts/experience.md``."""

    name = "update_md"

    def get_definition(self) -> dict[str, Any]:
        return self._get_definition_from_json("update_md")

    async def execute(
        self,
        file: str,
        op: str = "append",
        content: str | None = None,
        old_string: str | None = None,
        new_string: str | None = None,
    ) -> dict[str, Any]:
        file = file.strip().lower()
        if not file.endswith(".md"):
            file = file + ".md"
        if file not in _FILE_CONFIG:
            return {"success": False, "error": f"Unknown file: {file}. Use user.md or experience.md."}

        op = (op or "append").strip().lower()
        if op not in ("append", "edit", "replace"):
            return {"success": False, "error": f"Unknown op: {op}. Use append, edit, or replace."}

        try:
            path = _PROMPTS_DIR / file
            cfg = _FILE_CONFIG[file]
            marker = cfg["marker"]
            default_header = cfg["default_header"]

            path.parent.mkdir(parents=True, exist_ok=True)

            if path.exists():
                existing = await asyncio.to_thread(path.read_text, "utf-8")
            else:
                existing = default_header

            header, body = _split_header_body(existing, marker, default_header)

            if op == "append":
                if not content or not content.strip():
                    return {"success": False, "error": "op=append requires non-empty content."}
                new_body = (body.rstrip() + "\n\n" + content.strip()).lstrip("\n")
            elif op == "replace":
                if content is None:
                    return {"success": False, "error": "op=replace requires content (use empty string to clear)."}
                new_body = content.strip()
            else:  # op == "edit"
                if not old_string:
                    return {"success": False, "error": "op=edit requires old_string."}
                if new_string is None:
                    return {"success": False, "error": "op=edit requires new_string (use empty string to delete)."}
                if old_string not in body:
                    return {
                        "success": False,
                        "error": "old_string not found in editable body. Read the current content in your system prompt and pass an exact match.",
                    }
                if body.count(old_string) > 1:
                    return {
                        "success": False,
                        "error": "old_string matches multiple locations. Include more surrounding context so it is unique.",
                    }
                new_body = body.replace(old_string, new_string, 1)

            new_text = header.rstrip() + "\n\n" + new_body.strip() + "\n"
            await asyncio.to_thread(path.write_text, new_text, "utf-8")

            logger.info("update_md: op=%s file=%s new_body_chars=%d", op, file, len(new_body))
            return {
                "success": True,
                "op": op,
                "file": file,
                "body_characters": len(new_body),
            }

        except Exception as exc:
            logger.error("update_md failed (%s): %s", file, exc, exc_info=True)
            return {"success": False, "error": str(exc)}
