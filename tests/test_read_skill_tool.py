"""Unit tests for the ReadSkillTool."""
from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from backend.agent.skills.registry import SkillRegistry
from backend.agent.tools.read_skill_tool import ReadSkillTool


def _write_skill(base: Path, name: str, description: str, body: str) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"{name}.md"
    path.write_text(
        f"---\ndescription: {description}\n---\n{body}",
        encoding="utf-8",
    )
    return path


@pytest.fixture()
def tool_with_skills(tmp_path: Path) -> tuple[ReadSkillTool, Path]:
    _write_skill(
        tmp_path,
        "hello",
        "a hello skill",
        dedent(
            """\
            # Hello

            Run a tiny SQL query to confirm the skill loaded end-to-end.
            """
        ),
    )
    reg = SkillRegistry(roots=[tmp_path])
    return ReadSkillTool(skill_registry=reg), tmp_path


# ---------------------------------------------------------------------------


async def test_read_skill_happy_path(tool_with_skills: tuple[ReadSkillTool, Path]) -> None:
    tool, tmp_path = tool_with_skills
    result = await tool.execute(name="hello")
    assert result["success"] is True
    assert result["name"] == "hello"
    assert result["description"] == "a hello skill"
    assert "# Hello" in result["content"]
    assert result["file_path"].endswith("hello.md")


async def test_read_skill_definition_is_loadable(tool_with_skills: tuple[ReadSkillTool, Path]) -> None:
    tool, _ = tool_with_skills
    defn = tool.get_definition()
    assert defn["function"]["name"] == "read_skill"
    assert "name" in defn["function"]["parameters"]["properties"]
    assert defn["function"]["parameters"]["required"] == ["name"]


async def test_read_skill_missing_name_errors(tool_with_skills: tuple[ReadSkillTool, Path]) -> None:
    tool, _ = tool_with_skills
    result = await tool.execute(name="")
    assert result["success"] is False
    assert "name" in result["error"].lower()


async def test_read_skill_unknown_skill_errors(tool_with_skills: tuple[ReadSkillTool, Path]) -> None:
    tool, _ = tool_with_skills
    result = await tool.execute(name="nope")
    assert result["success"] is False
    assert "Unknown skill" in result["error"]
    assert "available" in result
    assert "hello" in result["available"]


async def test_read_skill_rejects_path_traversal(tool_with_skills: tuple[ReadSkillTool, Path]) -> None:
    tool, _ = tool_with_skills
    for evil in ("../etc/passwd", "hello/../../evil", "/etc/passwd", "hello\x00.md"):
        result = await tool.execute(name=evil)
        assert result["success"] is False, f"Should reject {evil!r}"
        assert "Invalid" in result["error"] or "Unknown" in result["error"]


async def test_read_skill_no_registry_configured() -> None:
    tool = ReadSkillTool(skill_registry=None)
    result = await tool.execute(name="hello")
    assert result["success"] is False
    assert "not configured" in result["error"]


async def test_read_skill_disabled_is_invisible(tool_with_skills: tuple[ReadSkillTool, Path]) -> None:
    """A disabled skill must behave exactly as if it didn't exist."""
    tool, _ = tool_with_skills
    tool._skill_registry.set_disabled(["hello"])
    result = await tool.execute(name="hello")
    assert result["success"] is False
    assert "Unknown skill" in result["error"]
    # The 'available' list reflects only enabled skills, so hello must
    # NOT appear (it's currently disabled).
    assert "hello" not in result.get("available", [])
