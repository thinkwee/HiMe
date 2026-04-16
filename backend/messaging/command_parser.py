"""Platform-neutral slash-command parser.

Moved here from ``backend.telegram.command_parser`` so that both Telegram
and Feishu gateways can reuse the same parsing logic. The old module now
re-exports from this one to keep existing imports working.
"""
from __future__ import annotations

import logging

from .base import CommandType, MessageEnvelope, ParsedCommand

logger = logging.getLogger(__name__)

# Mapping from slash-token to CommandType
_COMMAND_MAP = {
    "/help": CommandType.HELP,
    "/clear": CommandType.CLEAR,
    "/mute": CommandType.MUTE,
    "/unmute": CommandType.UNMUTE,
    "/restart": CommandType.RESTART,
    "/log": CommandType.LOG,
}


class CommandParser:
    """Stateless parser: text in → ``ParsedCommand`` out."""

    @staticmethod
    def parse(text: str, envelope: MessageEnvelope | None = None) -> ParsedCommand:
        """Parse raw text into a ``ParsedCommand``.

        - ``/command args...`` → known command with args
        - ``/unknown``         → ``CommandType.UNKNOWN``
        - plain text           → ``CommandType.ASK`` (treated as free-form question)
        """
        stripped = text.strip()
        if not stripped:
            return ParsedCommand(
                command_type=CommandType.UNKNOWN,
                raw_text=text,
                envelope=envelope,
            )

        if stripped.startswith("/"):
            parts = stripped.split(None, 1)
            token = parts[0].split("@")[0].lower()  # strip @botname suffix
            args = parts[1] if len(parts) > 1 else ""

            cmd_type = _COMMAND_MAP.get(token, CommandType.UNKNOWN)
            return ParsedCommand(
                command_type=cmd_type,
                args=args,
                raw_text=text,
                envelope=envelope,
            )

        return ParsedCommand(
            command_type=CommandType.ASK,
            args=stripped,
            raw_text=text,
            envelope=envelope,
        )
