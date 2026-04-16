"""
read_skill tool — load a skill markdown body on demand.

Skills are discovered at agent startup and their ``name + description``
are injected into the system prompt.  When the LLM decides a skill is
relevant, it calls ``read_skill(name=...)`` to retrieve the full body.

Security: this tool is deliberately *not* a generic file reader.  It
only serves files registered in the bound ``SkillRegistry``; any attempt
to pass a path, a path traversal, or an unknown name is rejected.
"""
from __future__ import annotations

import logging
from typing import Any

from ..skills.registry import SkillRegistry
from .base import BaseTool

logger = logging.getLogger(__name__)


class ReadSkillTool(BaseTool):
    """Return the full contents of a registered skill markdown file."""

    name = "read_skill"

    def __init__(self, skill_registry: SkillRegistry | None = None) -> None:
        self._skill_registry = skill_registry

    @property
    def is_concurrency_safe(self) -> bool:
        return True

    def get_definition(self) -> dict[str, Any]:
        return self._get_definition_from_json("read_skill")

    async def execute(self, name: str = "", **_: Any) -> dict[str, Any]:
        if not isinstance(name, str) or not name.strip():
            return {
                "success": False,
                "error": "read_skill requires a non-empty 'name' argument",
            }

        # Reject anything that looks like a path rather than a skill name.
        if any(ch in name for ch in ("/", "\\", "..", "\x00")):
            return {
                "success": False,
                "error": f"Invalid skill name '{name}': must be a plain identifier",
            }

        if self._skill_registry is None:
            return {
                "success": False,
                "error": "Skills subsystem is not configured on this agent",
            }

        entry = self._skill_registry.get(name.strip())
        if entry is None or not entry.enabled:
            # Disabled skills are deliberately invisible to the agent — they
            # are never injected into the system prompt and ``read_skill``
            # treats them as if they don't exist.
            available = [e.name for e in self._skill_registry.list_enabled()]
            return {
                "success": False,
                "error": f"Unknown skill: {name}",
                "available": available,
            }

        try:
            content = entry.file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("Failed to read skill %s at %s: %s", name, entry.file_path, exc)
            return {
                "success": False,
                "error": f"Failed to read skill {name}: {exc}",
            }

        return {
            "success": True,
            "name": entry.name,
            "description": entry.description,
            "content": content,
            "file_path": str(entry.file_path),
        }
