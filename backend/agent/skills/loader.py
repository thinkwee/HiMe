"""
Skill discovery and frontmatter parsing.

A skill is a single ``.md`` file directly under one of the configured
skill root directories.  The filename stem is the canonical skill name
(``skills/overtraining.md`` → ``overtraining``); the markdown's
frontmatter contributes a one-line ``description`` only.

Format:

    ---
    description: Combine HRV, resting HR, and sleep efficiency to assess overtraining
    ---
    # Full playbook body
    1. Use the sql tool to query ...
    2. Use the code tool to compute ...

That is the entire format.  No nested directories, no execution
manifest, no dependency declarations — HIME's skills are pure analysis
playbooks consumed by the LLM via the existing ``sql`` / ``code`` tools.

Security / robustness:

- Skill names must match ``^[a-z0-9_-]+$`` to safely round-trip through
  the filesystem and HTTP routes.
- Hidden files (names starting with ``.``) are skipped.
- Files larger than ``DEFAULT_MAX_SKILL_FILE_BYTES`` (256 KB) are skipped.
- Each root is capped at ``MAX_SKILLS_PER_ROOT`` (300) files.
- Frontmatter parse errors are logged as warnings, never raised.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

DEFAULT_MAX_SKILL_FILE_BYTES = 256 * 1024
MAX_SKILLS_PER_ROOT = 300

#: Allowed skill name characters.  Enforced both at discovery time and by the
#: CRUD HTTP routes so the on-disk path can never escape the skills root.
SKILL_NAME_RE = re.compile(r"^[a-z0-9_-]+$")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class SkillEntry:
    """A discovered skill.  Body is loaded lazily by ``ReadSkillTool``."""

    name: str
    description: str
    file_path: Path
    enabled: bool = True  # whether the skill is shown to the agent


# ---------------------------------------------------------------------------
# Frontmatter parser
# ---------------------------------------------------------------------------


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Parse a ``---\\n...\\n---\\n`` YAML frontmatter block.

    Returns ``(metadata_dict, remaining_body)``.  If the text has no
    frontmatter or the YAML is malformed, returns ``({}, text)`` and
    logs a warning.  Never raises.
    """
    if not text:
        return {}, ""
    norm = text.replace("\r\n", "\n").replace("\r", "\n")
    if not norm.startswith("---"):
        return {}, text
    lines = norm.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, text
    end_idx = -1
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx == -1:
        logger.warning("Frontmatter opening delimiter found but no closing ---")
        return {}, text
    yaml_block = "\n".join(lines[1:end_idx])
    body = "\n".join(lines[end_idx + 1 :])
    try:
        data = yaml.safe_load(yaml_block) or {}
    except yaml.YAMLError as exc:
        logger.warning("Failed to parse skill frontmatter YAML: %s", exc)
        return {}, text
    if not isinstance(data, dict):
        logger.warning(
            "Skill frontmatter did not parse to a mapping (got %s)",
            type(data).__name__,
        )
        return {}, text
    return data, body


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _iter_skill_files(root: Path) -> list[Path]:
    """Return ``*.md`` files directly under ``root`` (non-recursive)."""
    if not root.exists() or not root.is_dir():
        return []
    candidates: list[Path] = []
    try:
        entries = sorted(root.iterdir())
    except (OSError, PermissionError) as exc:
        logger.warning("Cannot iterate skills root %s: %s", root, exc)
        return []
    for entry in entries:
        if len(candidates) >= MAX_SKILLS_PER_ROOT:
            logger.warning(
                "Reached MAX_SKILLS_PER_ROOT (%d) under %s; stopping scan",
                MAX_SKILLS_PER_ROOT,
                root,
            )
            break
        if not entry.is_file():
            continue
        if entry.name.startswith("."):
            continue
        if entry.suffix.lower() != ".md":
            continue
        candidates.append(entry)
    return candidates


def discover_skills(roots: list[Path]) -> list[SkillEntry]:
    """Scan each root and return all successfully-parsed skills.

    Duplicates (same name) are resolved first-root-wins, so user-local
    overrides (``~/.hime/skills``) should come before bundled defaults.
    """
    seen_names: set[str] = set()
    results: list[SkillEntry] = []

    for root in roots:
        if root is None:
            continue
        try:
            files = _iter_skill_files(Path(root))
        except Exception as exc:
            logger.warning("Skill discovery failed for root %s: %s", root, exc)
            continue

        for path in files:
            name = path.stem
            if not SKILL_NAME_RE.match(name):
                logger.warning(
                    "Skipping skill %s: name must match %s",
                    path,
                    SKILL_NAME_RE.pattern,
                )
                continue
            try:
                stat = path.stat()
            except OSError as exc:
                logger.warning("Cannot stat %s: %s", path, exc)
                continue
            if stat.st_size > DEFAULT_MAX_SKILL_FILE_BYTES:
                logger.warning(
                    "Skipping oversized skill file %s (%d bytes > %d)",
                    path,
                    stat.st_size,
                    DEFAULT_MAX_SKILL_FILE_BYTES,
                )
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                logger.warning("Cannot read %s: %s", path, exc)
                continue

            metadata, _body = parse_frontmatter(text)
            description = metadata.get("description") if isinstance(metadata, dict) else None
            if not isinstance(description, str) or not description.strip():
                logger.warning(
                    "Skipping skill %s: missing one-line 'description' in frontmatter",
                    path,
                )
                continue

            if name in seen_names:
                logger.info(
                    "Skill name '%s' already loaded from earlier root; "
                    "ignoring duplicate at %s",
                    name,
                    path,
                )
                continue
            seen_names.add(name)
            results.append(
                SkillEntry(
                    name=name,
                    description=description.strip(),
                    file_path=path,
                )
            )

    return results
