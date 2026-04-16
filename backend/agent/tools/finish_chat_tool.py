"""
finish_chat tool — lets the agent explicitly signal the end of a chat interaction.
"""
from __future__ import annotations

import logging
from typing import Any

from .base import BaseTool

logger = logging.getLogger(__name__)


class FinishChatTool(BaseTool):
    """Explicitly end the current chat interaction."""

    name = "finish_chat"

    def get_definition(self) -> dict[str, Any]:
        return self._get_definition_from_json("finish_chat")

    async def execute(self, summary: str = "") -> dict[str, Any]:
        """Signal completion of the chat turn."""
        logger.info("Chat interaction finished: %s", summary)
        return {
            "success": True,
            "message": "Chat interaction marked as finished.",
        }
