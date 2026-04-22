"""
Skills subsystem — user-written analysis playbooks for the HIME agent.

A skill is a single ``.md`` file under ``skills/`` (or ``$HIME_SKILLS_DIR``
or ``~/.hime/skills``).  Filename stem is the canonical skill name; the
frontmatter contributes a one-line ``description`` only.

The agent sees ``name + description`` for every skill in its system
prompt (progressive disclosure) and pulls the full body on demand via
the ``read_skill`` tool.  Skills are documentation only — execution
goes through HIME's existing ``sql`` / ``code`` tools, scoped to the
same data-analysis sandbox as the rest of the agent.
"""
from .loader import (
    SKILL_NAME_RE,
    SkillEntry,
    discover_skills,
    parse_frontmatter,
)
from .prompt import format_skills_for_prompt
from .registry import SkillRegistry

__all__ = [
    "SKILL_NAME_RE",
    "SkillEntry",
    "discover_skills",
    "parse_frontmatter",
    "SkillRegistry",
    "format_skills_for_prompt",
]
