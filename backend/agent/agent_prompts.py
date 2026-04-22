"""
Single source of truth for prompt assembly across every agent / mode.

After the read/write split refactor HIME has exactly **three** LLM
roles, each with one prompt-building entry point — all of which live
here:

* ``_get_system_prompt(mode="chat")``  — chat orchestrator (any messaging
                                          gateway — Telegram, Feishu, …).
                                          Pure dispatcher with ``analyze`` /
                                          ``manage`` / ``reply_user`` /
                                          ``finish_chat``.
* ``build_sub_analysis_prompt()``      — read-only data analysis sub-
                                          agent.  The single engine used
                                          by **every** path that needs
                                          to query health data: chat
                                          orchestrator's ``analyze``
                                          delegation, cron / trigger
                                          autonomous analysis, and the
                                          iOS quick cat-state check.
* ``build_sub_manage_prompt()``        — write executor sub-agent.  The
                                          only path that mutates state
                                          (memory CRUD, ``update_md``,
                                          ``create_page``).  Always
                                          invoked through chat → manage.

Each builder composes a per-mode template with auxiliary layers from
``LAYER_PLAN``.  All prompts are fully static (no ``{placeholders}``
anywhere) so the entire system-prompt prefix is KV-cache friendly
across sessions and across the three callers of sub_analysis.

Layered prompt files in ``prompts/`` and how they map to roles:

    ┌──────────────────────┬──────┬──────────────┬─────────────┐
    │ file                 │ chat │ sub_analysis │ sub_manage  │
    ├──────────────────────┼──────┼──────────────┼─────────────┤
    │ soul.md              │  ✓   │      ·       │      ·      │
    │ rules_chat.md        │  ✓   │      ·       │      ·      │
    │ user.md              │  ✓   │      ·       │      ·      │
    │ sub_analysis.md      │  ·   │      ✓       │      ·      │
    │ data_schema.md       │  ·   │      ✓       │      ·      │
    │ sub_manage.md        │  ·   │      ·       │      ✓      │
    │ memory_guide.md      │  ·   │      ·       │      ✓      │
    │ create_page_guide.md │  ·   │      ·       │      ✓      │
    │ experience.md        │  ✓   │      ✓       │      ·      │
    │ <available_skills>   │  ✓   │      ✓       │      ·      │
    └──────────────────────┴──────┴──────────────┴─────────────┘

Notes:
* The autonomous-analysis and iOS-quick "modes" no longer exist as
  prompts.  Both are thin Python wrappers around ``sub_analysis`` (see
  ``_run_one_shot_analysis`` / ``run_quick_analysis`` in
  ``agent_loops.py``).  This means cron, trigger, iOS, and chat-spawned
  analysis all share the same system prompt → cross-entry KV cache
  reuse.
* The iOS quick path passes a **goal template** (``quick_goal.md``) as
  the user message, not as a system prompt — that file is not in the
  layer plan.
* Conditions are encoded in ``LAYER_PLAN`` below.  Adding a new prompt
  layer = adding one row.  Do not assemble prompts anywhere else in
  the codebase.  The chat orchestrator also appends
  ``prompts/conversation_header.md`` at stage 4 (after the skills
  block) as the final dispatcher instruction — it lives outside
  ``LAYER_PLAN`` because it must come *after* the skills block.
"""
import logging
from datetime import datetime, timezone
from pathlib import Path

from ..config import settings
from .prompt_loader import load_prompt
from .skills.prompt import format_skills_for_prompt

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Layer plan: which auxiliary prompt files to append for each role.
#
# All prompts are fully static (no ``{placeholders}`` anywhere) so no
# ``str.format`` is run on the result and no brace-escaping is needed —
# code samples in the markdown reach the LLM verbatim.
#
# Order matters: layers later in the list appear later in the prompt.
# ---------------------------------------------------------------------------

#: Per-role tuple of (filename, label) pairs to append after the core template.
#: Files that are missing on disk are silently skipped.
LAYER_PLAN: dict[str, tuple[tuple[str, str], ...]] = {
    "chat": (
        # Chat orchestrator is a pure dispatcher.  It delegates analysis
        # to ``sub_analysis`` and all writes to ``sub_manage``.  It needs
        # soul (persona, prepended via ``_build_core_template``),
        # rules_chat (operational dispatcher behaviour), and user.md
        # (user's communication preferences) — it's the only role that
        # actually talks to the user.
        ("rules_chat.md",    "chat rules"),
        ("experience.md",    "experience"),
        ("user.md",          "user profile"),
    ),
    "sub_analysis": (
        # The single read-only data engine — used by chat orchestrator's
        # ``analyze`` tool, cron / trigger autonomous analyses, and the
        # iOS quick cat-state check.  Same prompt for all three callers
        # so they share KV cache.
        ("data_schema.md",   "data schema"),
        ("experience.md",    "experience"),
    ),
    "sub_manage": (
        # Sub-manage is the chat path's write executor: memory CRUD,
        # ``update_md``, and personalised pages.  It needs memory_guide
        # for the schema/write patterns and create_page_guide for the
        # HimeUI component library.  user.md and experience.md are also
        # loaded so the agent can see the current editable body before
        # choosing update_md op=append vs edit vs replace.
        ("memory_guide.md",      "memory guide"),
        ("create_page_guide.md", "create_page guide"),
        ("user.md",              "user profile (current content)"),
        ("experience.md",        "experience (current content)"),
    ),
}

#: Roles that should get the ``<available_skills>`` block appended.
#: ``sub_analysis`` is the only role that can actually CALL ``read_skill``
#: and execute a skill — it's the data engine.  Chat orchestrator also
#: sees the index (name + description only) so it can: (a) answer the
#: user when asked "what skills do you have", and (b) pass a specific
#: skill name through to ``analyze(goal=...)`` when the user requests
#: one by name.  Sub-manage never analyses data, so it stays out.
SKILLS_ROLES = frozenset({"chat", "sub_analysis"})


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------


def _read_prompt_file(path: Path, label: str) -> str:
    """Best-effort prompt file read; return empty string on any failure."""
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8").strip()
        logger.debug("Loaded %s from %s", label, path)
        return text
    except Exception as exc:
        logger.warning("%s unreadable: %s", label, exc)
        return ""


# ---------------------------------------------------------------------------
# Mixin
# ---------------------------------------------------------------------------


class AgentPromptsMixin:
    """All prompt assembly for the chat orchestrator and its sub-agents."""

    # ------------------------------------------------------------------
    # Public utilities
    # ------------------------------------------------------------------

    def _format_message_timestamp(self, dt: datetime) -> str:
        import pytz
        tz_name = getattr(settings, "TIMEZONE", "UTC")
        try:
            local_tz = pytz.timezone(tz_name)
        except Exception:
            local_tz = pytz.UTC
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(local_tz).strftime("%Y-%m-%d %H:%M")

    # ------------------------------------------------------------------
    # Layer composition
    # ------------------------------------------------------------------

    def _append_layers(self, base: str, role: str) -> str:
        """Append every aux file from ``LAYER_PLAN[role]`` to ``base``."""
        plan = LAYER_PLAN.get(role, ())
        if not plan:
            return base
        prompts_dir = Path("prompts")
        chunks: list[str] = [base] if base else []
        for filename, label in plan:
            text = _read_prompt_file(prompts_dir / filename, label)
            if text:
                chunks.append(text)
        return "\n\n---\n\n".join(chunks)

    def _append_skills_block(self, base: str, role: str) -> str:
        """Append ``<available_skills>`` if the role is allowed to see them."""
        if role not in SKILLS_ROLES:
            return base
        skill_registry = getattr(self, "skill_registry", None)
        if skill_registry is None:
            return base
        try:
            skills_xml = format_skills_for_prompt(skill_registry.list_enabled())
        except Exception as exc:
            logger.warning("Failed to format skills for prompt: %s", exc)
            return base
        if not skills_xml:
            return base
        return f"{base}\n\n{skills_xml}"

    # ------------------------------------------------------------------
    # Chat orchestrator builder
    # ------------------------------------------------------------------

    def _get_system_prompt(self, mode: str = "chat") -> str:
        """Build the chat orchestrator's system prompt.

        ``mode`` is retained as a parameter for forward compatibility
        but only ``"chat"`` is supported now — the autonomous-analysis
        mode was retired when cron / trigger / iOS were unified onto the
        ``sub_analysis`` engine.

        Cache-friendly by construction: fully static per
        prompt-files-on-disk, no runtime values injected.  The whole
        prefix participates in cross-session KV cache hits.
        """
        if mode != "chat":
            logger.warning(
                "_get_system_prompt called with legacy mode=%r; only 'chat' is supported. "
                "Use build_sub_analysis_prompt or build_sub_manage_prompt for sub-agents.",
                mode,
            )

        # Stage 1: always-on core (soul + identity).
        result = self._build_core_template()
        # Stage 2: per-role aux layers (rules_chat / experience / user).
        result = self._append_layers(result, "chat")
        # Stage 3: skills index — chat orchestrator only sees names &
        # descriptions; it cannot call read_skill itself, but it can
        # pass a skill name through to analyze() so sub_analysis loads
        # and follows it.
        result = self._append_skills_block(result, "chat")
        # Stage 4: chat-only conversation header (post-skills dispatcher
        # instruction — lives outside LAYER_PLAN because it must come
        # after the skills block).
        result += _read_prompt_file(
            Path("prompts/conversation_header.md"), "conversation header"
        )
        return result

    # ------------------------------------------------------------------
    # Sub-analysis builder: read-only data engine for ALL data callers
    # ------------------------------------------------------------------

    def build_sub_analysis_prompt(self) -> str:
        """Build the sub_analysis system prompt.

        This is the single read-only engine used by every data path:
        chat-spawned ``analyze``, cron / trigger autonomous analysis,
        and the iOS quick cat-state check.  All three callers share
        this exact prompt so the KV cache prefix is reused across
        entries.

        Fully static per prompt-files-on-disk — no runtime values
        injected.
        """
        base = load_prompt("sub_analysis.md")
        result = self._append_layers(base, "sub_analysis")
        result = self._append_skills_block(result, "sub_analysis")
        return result

    # ------------------------------------------------------------------
    # Sub-manage builder: write executor for the chat path
    # ------------------------------------------------------------------

    def build_sub_manage_prompt(self) -> str:
        """Build the sub_manage system prompt.

        Sub-manage is the chat path's write executor — every persistence
        side-effect the chat orchestrator wants performed (memory CRUD,
        agent markdown updates, personalised pages) is delegated here.
        It owns ``sql`` (memory DB only), ``update_md`` and
        ``create_page``.

        It does NOT load ``data_schema.md`` because it never touches
        ``health_data``.  It DOES load ``memory_guide.md`` for the
        memory schema and ``create_page_guide.md`` for the HimeUI
        component library.

        Fully static — no runtime values injected.
        """
        base = load_prompt("sub_manage.md")
        return self._append_layers(base, "sub_manage")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_core_template(self) -> str:
        """Always-on chat-orchestrator core: ``soul.md``.

        soul is the only "core" left — everything else lives in
        ``LAYER_PLAN["chat"]``.  Cached on the instance because the
        source file doesn't change at runtime (the prompt editor reloads
        the agent if it does).
        """
        if not hasattr(self, "_soul_text"):
            self._soul_text = _read_prompt_file(Path("prompts/soul.md"), "soul")
        return self._soul_text or ""

