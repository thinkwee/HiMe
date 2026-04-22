"""
Skill → system prompt formatter.

Progressive disclosure: we only inject ``name + one-line description``
for each skill.  The full markdown body is loaded on demand by the
``read_skill`` tool when the LLM decides a skill is relevant.
"""
from __future__ import annotations

from collections.abc import Iterable
from xml.sax.saxutils import escape as _xml_escape

from .loader import SkillEntry


def format_skills_for_prompt(entries: Iterable[SkillEntry]) -> str:
    """Render skills as an ``<available_skills>`` XML block.

    An empty iterable produces an empty string so callers can append
    unconditionally without dragging in a dangling section header.
    """
    items = list(entries)
    if not items:
        return ""

    lines: list[str] = [
        "<available_skills>",
        "  <!-- Each skill is a user-written analysis playbook. Skills are",
        "       OPTIONAL — for simple tasks, query the data directly. When a",
        "       skill's description matches the current task and you'd benefit",
        "       from its method, call read_skill(name=...) to load the full",
        "       body, then follow it as guidance (not law). -->",
    ]
    for entry in items:
        name = _xml_escape(entry.name)
        description = _xml_escape(entry.description.strip())
        lines.append("  <skill>")
        lines.append(f"    <name>{name}</name>")
        lines.append(f"    <description>{description}</description>")
        lines.append("  </skill>")
    lines.append("</available_skills>")
    return "\n".join(lines)
