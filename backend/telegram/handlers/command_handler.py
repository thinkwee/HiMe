"""
CommandHandler — process parsed slash-commands and return text responses.

Commands:
  /help    — show available commands
  /clear   — clear agent chat history for this Telegram chat
  /mute    — mute autonomous report pushes for a duration
  /unmute  — resume report pushes
  /restart — restart the running agent
  /log     — activity summary for the last N hours
"""
from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime, timedelta, timezone

from ..models import CommandType, ParsedCommand

logger = logging.getLogger(__name__)

# ── Duration parsing helpers ─────────────────────────────────────────

_DURATION_RE = re.compile(r"^(?:(\d+)h)?(?:(\d+)m)?$")


def _parse_duration(text: str) -> int | None:
    """Parse '30m', '2h', '1h30m', or bare number (hours) into seconds."""
    if not text:
        return None
    m = _DURATION_RE.match(text)
    if m and (m.group(1) or m.group(2)):
        hours = int(m.group(1) or 0)
        minutes = int(m.group(2) or 0)
        return hours * 3600 + minutes * 60
    try:
        return int(text) * 3600
    except ValueError:
        return None


def _format_duration(seconds: int) -> str:
    h, rem = divmod(seconds, 3600)
    m = rem // 60
    parts = []
    if h:
        parts.append(f"{h}h")
    if m:
        parts.append(f"{m}m")
    return " ".join(parts) or "0m"


class CommandHandler:
    """Stateless command executor."""

    def __init__(
        self,
        get_active_agents: callable,
        get_memory_manager: callable,
        get_telegram_sender: callable = lambda: None,
    ) -> None:
        self._get_active_agents = get_active_agents
        self._get_memory_manager = get_memory_manager
        self._get_telegram_sender = get_telegram_sender

    # ── Dispatch ─────────────────────────────────────────────────────

    async def handle(self, cmd: ParsedCommand) -> str:
        handler_map = {
            CommandType.HELP: self._handle_help,
            CommandType.CLEAR: self._handle_clear,
            CommandType.MUTE: self._handle_mute,
            CommandType.UNMUTE: self._handle_unmute,
            CommandType.RESTART: self._handle_restart,
            CommandType.LOG: self._handle_log,
        }
        handler = handler_map.get(cmd.command_type, self._handle_unknown)
        try:
            return await handler(cmd)
        except Exception as exc:
            logger.error("Command handler error: %s", exc, exc_info=True)
            return f"Error processing command: {exc}"

    # ── /help ────────────────────────────────────────────────────────

    async def _handle_help(self, cmd: ParsedCommand) -> str:
        return (
            "**HIME Telegram Commands**\n\n"
            "`/clear` — Clear chat history\n"
            "`/mute [duration]` — Mute report pushes (e.g. 2h, 30m, 8h)\n"
            "`/unmute` — Resume report pushes\n"
            "`/restart` — Restart the agent\n"
            "`/log [hours]` — Activity summary (default 6h)\n"
            "`/help` — Show this help\n\n"
            "Or just send any text to chat with the agent!"
        )

    # ── /clear ───────────────────────────────────────────────────────

    async def _handle_clear(self, cmd: ParsedCommand) -> str:
        agents = self._get_active_agents()
        if not agents:
            return "No agent running."

        agent = next(iter(agents.values())).get("agent")
        if agent is None:
            return "Agent not ready."

        chat_id = cmd.envelope.chat_id if cmd.envelope else None
        if chat_id and chat_id in agent._chat_histories:
            count = len(agent._chat_histories[chat_id])
            agent._chat_histories[chat_id] = []
            agent._save_state()
            return f"Cleared {count} messages from chat history."
        elif chat_id:
            return "Chat history is already empty."

        # No chat_id — clear all
        total = sum(len(v) for v in agent._chat_histories.values())
        agent._chat_histories.clear()
        agent._save_state()
        return f"Cleared all chat histories ({total} messages)."

    # ── /mute [duration] ─────────────────────────────────────────────

    async def _handle_mute(self, cmd: ParsedCommand) -> str:
        sender = self._get_telegram_sender()
        if sender is None:
            return "Telegram sender not available."

        seconds = _parse_duration(cmd.args.strip()) if cmd.args.strip() else 7200
        if seconds is None:
            return "Invalid duration. Examples: `30m`, `2h`, `8h`"
        if seconds <= 0 or seconds > 86400:
            return "Duration must be between 1m and 24h."

        sender.mute(seconds)
        return f"Muted report pushes for {_format_duration(seconds)}."

    # ── /unmute ──────────────────────────────────────────────────────

    async def _handle_unmute(self, cmd: ParsedCommand) -> str:
        sender = self._get_telegram_sender()
        if sender is None:
            return "Telegram sender not available."

        if not sender.is_muted():
            return "Reports are not currently muted."

        sender.unmute()
        return "Unmuted. Report pushes will resume."

    # ── /restart ─────────────────────────────────────────────────────

    async def _handle_restart(self, cmd: ParsedCommand) -> str:
        agents = self._get_active_agents()
        if not agents:
            return "No agent running to restart."

        pid = next(iter(agents))

        from ...api.agent_lifecycle import restart_agent
        success = await restart_agent(pid)

        if success:
            return "Agent restarting..."
        return "Failed to restart agent. Check logs."

    # ── /log [hours] ─────────────────────────────────────────────────

    async def _handle_log(self, cmd: ParsedCommand) -> str:
        hours = 6
        if cmd.args.strip():
            try:
                hours = max(1, min(int(cmd.args.strip()), 72))
            except ValueError:
                return "Usage: `/log [hours]` — hours must be a number (1-72)"

        agents = self._get_active_agents()
        if not agents:
            return "No agent running."

        pid = next(iter(agents))
        memory = self._get_memory_manager(pid)
        if memory is None:
            return f"No memory database for `{pid}`."

        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )

        try:
            def _query():
                with sqlite3.connect(str(memory.db_file), timeout=10) as conn:
                    conn.row_factory = sqlite3.Row
                    return conn.execute(
                        "SELECT event_type, COUNT(*) as cnt "
                        "FROM activity_log "
                        "WHERE created_at >= ? "
                        "GROUP BY event_type ORDER BY cnt DESC",
                        (cutoff,),
                    ).fetchall()

            import asyncio
            rows = await asyncio.to_thread(_query)
        except Exception as exc:
            logger.warning("Activity log query failed: %s", exc)
            return "Failed to query activity log."

        if not rows:
            return f"No activity in the last {hours}h."

        lines = [f"**Activity (last {hours}h)**\n"]
        for row in rows:
            lines.append(f"  `{row['event_type']}`: {row['cnt']}")
        return "\n".join(lines)

    # ── unknown ──────────────────────────────────────────────────────

    async def _handle_unknown(self, cmd: ParsedCommand) -> str:
        command = cmd.raw_text.split()[0] if cmd.raw_text.strip() else "?"
        return f"Unknown command: `{command}`\nType `/help` for available commands."
