"""Unit tests for the skills loader, registry and prompt formatter."""
from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from backend.agent.skills.loader import (
    SKILL_NAME_RE,
    SkillEntry,
    discover_skills,
    parse_frontmatter,
)
from backend.agent.skills.prompt import format_skills_for_prompt
from backend.agent.skills.registry import SkillRegistry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_skill(base: Path, name: str, description: str, body: str = "# Body\n") -> Path:
    """Write a skill file ``base/<name>.md`` with the given description."""
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"{name}.md"
    # Quote the description so YAML doesn't interpret values like
    # ``yes`` / ``no`` / ``on`` as booleans.
    safe = description.replace('"', '\\"')
    path.write_text(
        f'---\ndescription: "{safe}"\n---\n{body}',
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# parse_frontmatter
# ---------------------------------------------------------------------------


def test_parse_frontmatter_valid() -> None:
    text = dedent(
        """\
        ---
        description: a test skill
        ---
        # Body
        content here
        """
    )
    meta, body = parse_frontmatter(text)
    assert meta["description"] == "a test skill"
    assert "# Body" in body


def test_parse_frontmatter_missing_returns_empty() -> None:
    text = "just a plain markdown file\n"
    meta, body = parse_frontmatter(text)
    assert meta == {}
    assert body == text


def test_parse_frontmatter_malformed_yaml_returns_empty() -> None:
    text = "---\ndescription: hi\n  bad: : : indent\n---\nbody\n"
    meta, body = parse_frontmatter(text)
    assert meta == {}
    assert body == text


def test_parse_frontmatter_unclosed_returns_empty() -> None:
    text = "---\ndescription: hi\n\nno closing delimiter\n"
    meta, body = parse_frontmatter(text)
    assert meta == {}


def test_parse_frontmatter_scalar_yaml_returns_empty() -> None:
    text = "---\njust-a-string\n---\nbody\n"
    meta, _ = parse_frontmatter(text)
    assert meta == {}


def test_parse_frontmatter_empty_input() -> None:
    meta, body = parse_frontmatter("")
    assert meta == {}
    assert body == ""


# ---------------------------------------------------------------------------
# Name validation
# ---------------------------------------------------------------------------


def test_skill_name_regex_accepts_lowercase_alnum_dash_underscore() -> None:
    for ok in ("hello", "over_training", "weekly-report", "a1b2", "x"):
        assert SKILL_NAME_RE.match(ok)
    for bad in ("Hello", "with space", "dot.name", "CAPS", "", "../etc"):
        assert not SKILL_NAME_RE.match(bad)


# ---------------------------------------------------------------------------
# discover_skills
# ---------------------------------------------------------------------------


def test_discover_flat_files(tmp_path: Path) -> None:
    _write_skill(tmp_path, "alpha", "first skill")
    _write_skill(tmp_path, "beta", "second skill")
    entries = discover_skills([tmp_path])
    names = {e.name for e in entries}
    assert names == {"alpha", "beta"}


def test_discover_skips_hidden_files(tmp_path: Path) -> None:
    _write_skill(tmp_path, "visible", "yes")
    hidden = tmp_path / ".hidden.md"
    hidden.write_text("---\ndescription: invisible\n---\nbody", encoding="utf-8")
    entries = discover_skills([tmp_path])
    assert {e.name for e in entries} == {"visible"}


def test_discover_skips_non_md_files(tmp_path: Path) -> None:
    _write_skill(tmp_path, "real", "yes")
    (tmp_path / "readme.txt").write_text("not a skill", encoding="utf-8")
    (tmp_path / "data.json").write_text("{}", encoding="utf-8")
    entries = discover_skills([tmp_path])
    assert [e.name for e in entries] == ["real"]


def test_discover_skips_subdirectories(tmp_path: Path) -> None:
    """Loader is non-recursive — files in subdirs must not appear."""
    _write_skill(tmp_path, "top", "shown")
    nested = tmp_path / "nested"
    _write_skill(nested, "buried", "hidden")
    entries = discover_skills([tmp_path])
    assert [e.name for e in entries] == ["top"]


def test_discover_oversized_file_skipped(tmp_path: Path) -> None:
    huge = "x" * (300 * 1024)  # 300 KB > 256 KB cap
    _write_skill(tmp_path, "big", "too large", body=huge)
    entries = discover_skills([tmp_path])
    assert entries == []


def test_discover_missing_description_skipped(tmp_path: Path) -> None:
    path = tmp_path / "nodesc.md"
    path.write_text("---\nfoo: bar\n---\nbody", encoding="utf-8")
    entries = discover_skills([tmp_path])
    assert entries == []


def test_discover_invalid_filename_skipped(tmp_path: Path) -> None:
    path = tmp_path / "Bad Name.md"
    path.write_text("---\ndescription: x\n---\n", encoding="utf-8")
    entries = discover_skills([tmp_path])
    assert entries == []


def test_discover_duplicate_names_first_root_wins(tmp_path: Path) -> None:
    root1 = tmp_path / "r1"
    root2 = tmp_path / "r2"
    _write_skill(root1, "dup", "from root1")
    _write_skill(root2, "dup", "from root2")
    entries = discover_skills([root1, root2])
    assert len(entries) == 1
    assert entries[0].description == "from root1"


def test_discover_nonexistent_root_is_noop(tmp_path: Path) -> None:
    entries = discover_skills([tmp_path / "does-not-exist"])
    assert entries == []


# ---------------------------------------------------------------------------
# SkillRegistry
# ---------------------------------------------------------------------------


def test_registry_get_and_list(tmp_path: Path) -> None:
    _write_skill(tmp_path, "alpha", "a")
    _write_skill(tmp_path, "beta", "b")
    reg = SkillRegistry(roots=[tmp_path])
    assert reg.get("alpha") is not None
    assert reg.get("missing") is None
    assert {e.name for e in reg.list_all()} == {"alpha", "beta"}


def test_registry_refresh_picks_up_new_skills(tmp_path: Path) -> None:
    reg = SkillRegistry(roots=[tmp_path])
    assert reg.list_all() == []
    _write_skill(tmp_path, "fresh", "new")
    reg.refresh()
    assert [e.name for e in reg.list_all()] == ["fresh"]


def test_registry_refresh_drops_deleted_skills(tmp_path: Path) -> None:
    path = _write_skill(tmp_path, "doomed", "soon")
    reg = SkillRegistry(roots=[tmp_path])
    assert [e.name for e in reg.list_all()] == ["doomed"]
    path.unlink()
    reg.refresh()
    assert reg.list_all() == []


def test_registry_skips_nonexistent_and_dedupes_roots(tmp_path: Path) -> None:
    _write_skill(tmp_path, "solo", "d")
    reg = SkillRegistry(roots=[tmp_path, tmp_path, tmp_path / "nope"])
    assert len(reg.roots) == 1
    assert [e.name for e in reg.list_all()] == ["solo"]


def test_registry_primary_root_first_existing(tmp_path: Path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    reg = SkillRegistry(roots=[a, b])
    assert reg.primary_root == a


# ---------------------------------------------------------------------------
# format_skills_for_prompt
# ---------------------------------------------------------------------------


def test_format_empty_returns_empty_string() -> None:
    assert format_skills_for_prompt([]) == ""


def test_format_contains_required_fields(tmp_path: Path) -> None:
    _write_skill(tmp_path, "overtraining", "Detect overtraining from HRV + RHR")
    reg = SkillRegistry(roots=[tmp_path])
    xml = format_skills_for_prompt(reg.list_all())
    assert "<available_skills>" in xml
    assert "<name>overtraining</name>" in xml
    assert "Detect overtraining from HRV + RHR" in xml


def test_format_escapes_xml_special_chars(tmp_path: Path) -> None:
    path = tmp_path / "weird.md"
    path.write_text(
        '---\ndescription: "uses <, > & \\"quotes\\""\n---\nbody',
        encoding="utf-8",
    )
    reg = SkillRegistry(roots=[tmp_path])
    xml = format_skills_for_prompt(reg.list_all())
    assert "&lt;" in xml
    assert "&gt;" in xml
    assert "&amp;" in xml


def test_skill_entry_dataclass_fields(tmp_path: Path) -> None:
    """Pin the SkillEntry shape so consumers don't break silently."""
    path = _write_skill(tmp_path, "shape", "shape test")
    entries = discover_skills([tmp_path])
    e = entries[0]
    assert isinstance(e, SkillEntry)
    assert e.name == "shape"
    assert e.description == "shape test"
    assert e.file_path == path
    # Default-enabled until set_disabled says otherwise.
    assert e.enabled is True


# ---------------------------------------------------------------------------
# Enable / disable persistence
# ---------------------------------------------------------------------------


def test_new_skills_default_enabled(tmp_path: Path) -> None:
    _write_skill(tmp_path, "alpha", "a")
    _write_skill(tmp_path, "beta", "b")
    reg = SkillRegistry(roots=[tmp_path])
    assert all(e.enabled for e in reg.list_all())
    assert {e.name for e in reg.list_enabled()} == {"alpha", "beta"}


def test_set_disabled_persists_and_filters(tmp_path: Path) -> None:
    _write_skill(tmp_path, "alpha", "a")
    _write_skill(tmp_path, "beta", "b")
    _write_skill(tmp_path, "gamma", "c")
    reg = SkillRegistry(roots=[tmp_path])
    reg.set_disabled(["beta"])

    # In-memory entries reflect the change.
    by_name = {e.name: e for e in reg.list_all()}
    assert by_name["alpha"].enabled is True
    assert by_name["beta"].enabled is False
    assert by_name["gamma"].enabled is True
    assert {e.name for e in reg.list_enabled()} == {"alpha", "gamma"}

    # Persistence file exists.
    state_file = tmp_path / ".skill_state.json"
    assert state_file.exists()

    # A fresh registry pointed at the same root re-reads the disabled set.
    reg2 = SkillRegistry(roots=[tmp_path])
    by_name2 = {e.name: e for e in reg2.list_all()}
    assert by_name2["beta"].enabled is False
    assert by_name2["alpha"].enabled is True


def test_disabled_skill_excluded_from_format(tmp_path: Path) -> None:
    _write_skill(tmp_path, "alpha", "alpha desc")
    _write_skill(tmp_path, "beta", "beta desc")
    reg = SkillRegistry(roots=[tmp_path])
    reg.set_disabled(["beta"])

    xml = format_skills_for_prompt(reg.list_enabled())
    assert "<name>alpha</name>" in xml
    assert "<name>beta</name>" not in xml
    assert "beta desc" not in xml


def test_set_disabled_clear_re_enables_all(tmp_path: Path) -> None:
    _write_skill(tmp_path, "alpha", "a")
    _write_skill(tmp_path, "beta", "b")
    reg = SkillRegistry(roots=[tmp_path])
    reg.set_disabled(["alpha", "beta"])
    assert reg.list_enabled() == []
    reg.set_disabled([])
    assert {e.name for e in reg.list_enabled()} == {"alpha", "beta"}


def test_state_file_is_hidden_from_discovery(tmp_path: Path) -> None:
    """The .skill_state.json file must not be picked up as a skill."""
    _write_skill(tmp_path, "alpha", "a")
    reg = SkillRegistry(roots=[tmp_path])
    reg.set_disabled(["alpha"])
    # State file lives next to the skill but starts with a dot.
    assert (tmp_path / ".skill_state.json").exists()
    reg.refresh()
    assert {e.name for e in reg.list_all()} == {"alpha"}


def test_refresh_after_new_file_keeps_disabled_state(tmp_path: Path) -> None:
    _write_skill(tmp_path, "alpha", "a")
    reg = SkillRegistry(roots=[tmp_path])
    reg.set_disabled(["alpha"])
    # Drop a brand new skill in.
    _write_skill(tmp_path, "beta", "b")
    reg.refresh()
    by_name = {e.name: e for e in reg.list_all()}
    # Pre-existing disabled skill stays disabled; new skill defaults to enabled.
    assert by_name["alpha"].enabled is False
    assert by_name["beta"].enabled is True
