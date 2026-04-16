"""HTTP routes for managing user-written skills.

Skills are plain markdown files (`<root>/<name>.md`) discovered by
``backend.agent.skills.SkillRegistry``.  This module exposes a thin
CRUD layer for the frontend Skills panel:

* ``GET /api/skills``           — list discovered skills
* ``GET /api/skills/{name}``     — fetch raw markdown
* ``POST /api/skills``           — create a new skill
* ``PUT /api/skills/{name}``     — overwrite an existing skill
* ``DELETE /api/skills/{name}``  — remove a skill
* ``POST /api/skills/refresh``   — re-scan disk (rare; mutating routes
                                   already refresh the running agent)

The write target is the *primary* skill root (the first existing
directory from ``_resolve_skill_roots``).  If none exist yet we create
``./skills`` on first write so the user never has to think about
filesystem layout.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..agent.skills import SKILL_NAME_RE, SkillRegistry
from ..config import settings
from .agent_state import active_agents

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/skills", tags=["skills"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_skill_roots() -> list[Path]:
    """Mirror ``AutonomousHealthAgent._resolve_skill_roots`` so the API
    works whether or not an agent is currently running."""
    roots: list[Path] = []
    raw = getattr(settings, "HIME_SKILLS_DIR", "") or ""
    for piece in raw.split(os.pathsep):
        piece = piece.strip()
        if piece:
            roots.append(Path(piece).expanduser())
    roots.append(Path.home() / ".hime" / "skills")
    roots.append(Path("./skills"))
    return roots


def _build_registry() -> SkillRegistry:
    """Fresh registry over the current on-disk state."""
    return SkillRegistry(roots=_resolve_skill_roots())


def _writable_root() -> Path:
    """Return the directory new skills should be written to.

    Picks the first existing root from ``_resolve_skill_roots``; if
    none exist, creates ``./skills`` and returns it so the very first
    POST works without prior shell setup.
    """
    for r in _resolve_skill_roots():
        try:
            if r.exists() and r.is_dir():
                return r
        except OSError:
            continue
    fallback = Path("./skills")
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def _validate_name(name: str) -> None:
    if not isinstance(name, str) or not SKILL_NAME_RE.match(name):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid skill name '{name}'. "
                f"Must match {SKILL_NAME_RE.pattern} (lowercase letters, digits, "
                "hyphen, underscore)."
            ),
        )


def _resolve_path(name: str) -> Path:
    """Map a validated skill name to its on-disk path inside the writable root.

    Re-checks that the resolved path is genuinely a child of the root, in
    case any name slipped past validation (defence in depth).
    """
    _validate_name(name)
    root = _writable_root().resolve()
    candidate = (root / f"{name}.md").resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=400, detail="Path traversal rejected")
    return candidate


def _refresh_running_agents() -> None:
    """Tell every running agent to re-scan its skills directory.

    No-op if no agent is currently running.
    """
    for info in active_agents.values():
        agent = info.get("agent")
        registry = getattr(agent, "skill_registry", None)
        if registry is not None:
            try:
                registry.refresh()
            except Exception as exc:
                logger.warning("SkillRegistry refresh failed for running agent: %s", exc)


def _build_markdown(description: str, body: str) -> str:
    """Compose a SKILL.md from the editor inputs.

    Quotes the description to keep YAML happy with values like ``yes``
    or ``no``.  ``"`` inside descriptions is escaped.
    """
    safe_desc = description.strip().replace('"', '\\"')
    body_clean = body.rstrip() + "\n" if body else ""
    return f'---\ndescription: "{safe_desc}"\n---\n{body_clean}'


# ---------------------------------------------------------------------------
# Pydantic shapes
# ---------------------------------------------------------------------------


class SkillCreate(BaseModel):
    name: str = Field(..., description="Filename stem; must match ^[a-z0-9_-]+$")
    description: str = Field(..., min_length=1)
    body: str = Field(default="", description="Markdown body (without frontmatter)")


class SkillUpdate(BaseModel):
    description: str = Field(..., min_length=1)
    body: str = Field(default="")


class SkillStateUpdate(BaseModel):
    """Bulk replacement of the disabled-skills set.

    The body lists every skill name that should be **disabled**; any
    discovered skill not in the list is enabled.  This is the natural
    shape for the frontend "select which skills are visible" UI.
    """
    disabled: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("")
async def list_skills():
    """Return all discovered skills (name + description + path + enabled)."""
    reg = await asyncio.to_thread(_build_registry)
    items = [
        {
            "name": e.name,
            "description": e.description,
            "file_path": str(e.file_path),
            "enabled": e.enabled,
        }
        for e in reg.list_all()
    ]
    return {
        "success": True,
        "skills": items,
        "writable_root": str(_writable_root()),
        "enabled_count": sum(1 for e in reg.list_all() if e.enabled),
        "total_count": len(reg.list_all()),
    }


@router.put("/state")
async def update_skill_state(payload: SkillStateUpdate):
    """Replace the disabled-skills set in one shot.

    Frontend posts the full set every time the user toggles or
    select-all/none — keeps the on-disk file as the single source of
    truth and avoids per-skill race conditions.
    """
    # Validate every supplied name to defend against malformed input.
    for n in payload.disabled:
        if not isinstance(n, str) or not SKILL_NAME_RE.match(n):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid skill name in disabled list: {n!r}",
            )
    reg = await asyncio.to_thread(_build_registry)
    await asyncio.to_thread(reg.set_disabled, payload.disabled)
    _refresh_running_agents()
    return {
        "success": True,
        "disabled_count": len(payload.disabled),
        "enabled_count": sum(1 for e in reg.list_all() if e.enabled),
    }


@router.get("/{name}")
async def get_skill(name: str):
    """Return the full raw markdown of a single skill."""
    _validate_name(name)
    reg = await asyncio.to_thread(_build_registry)
    entry = reg.get(name)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")
    try:
        raw = await asyncio.to_thread(entry.file_path.read_text, "utf-8")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    # Strip frontmatter so the editor can show body separately.
    from ..agent.skills.loader import parse_frontmatter
    _meta, body = parse_frontmatter(raw)
    return {
        "success": True,
        "name": entry.name,
        "description": entry.description,
        "body": body,
        "raw": raw,
        "file_path": str(entry.file_path),
    }


@router.post("")
async def create_skill(payload: SkillCreate):
    """Create a new skill file.  Fails if the name already exists."""
    path = _resolve_path(payload.name)
    if path.exists():
        raise HTTPException(
            status_code=409,
            detail=f"Skill '{payload.name}' already exists",
        )
    content = _build_markdown(payload.description, payload.body)
    try:
        await asyncio.to_thread(path.write_text, content, "utf-8")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    _refresh_running_agents()
    logger.info("Created skill %s at %s", payload.name, path)
    return {"success": True, "name": payload.name, "file_path": str(path)}


@router.put("/{name}")
async def update_skill(name: str, payload: SkillUpdate):
    """Overwrite an existing skill's description and body."""
    path = _resolve_path(name)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")
    content = _build_markdown(payload.description, payload.body)
    try:
        await asyncio.to_thread(path.write_text, content, "utf-8")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    _refresh_running_agents()
    logger.info("Updated skill %s at %s", name, path)
    return {"success": True, "name": name, "file_path": str(path)}


@router.delete("/{name}")
async def delete_skill(name: str):
    """Remove a skill file."""
    path = _resolve_path(name)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")
    try:
        await asyncio.to_thread(path.unlink)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    _refresh_running_agents()
    logger.info("Deleted skill %s at %s", name, path)
    return {"success": True, "name": name}


@router.post("/refresh")
async def refresh_skills():
    """Force every running agent to re-scan its skills directory."""
    _refresh_running_agents()
    reg = await asyncio.to_thread(_build_registry)
    return {"success": True, "count": len(reg.list_all())}
