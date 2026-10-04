"""
Autonomous Health Agent — Single-agent event-driven architecture.

  One agent handles everything sequentially:
  - Chat requests: user messages (any gateway — Telegram, Feishu, …) routed
    through the shared InboxQueue
  - Analysis tasks: scheduled/on-demand tasks via _analysis_queue
  - Quick analysis: budget-limited status check for iOS cat feature

  No dual-loop, no LLM lock. One event at a time, clean sequential execution.

Tools:
  sql, code, push_report, update_md, reply_user, finish_chat, create_page
  (sleep and wake_analysis removed — scheduling is now handled by the cron
   scheduler in main.py, not by the agent itself)
"""
import asyncio
import json
import logging
import os
import time
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import settings
from ..messaging.inbox import InboxQueue
from ..utils import ts_now
from .agent_loops import AgentLoopsMixin, spawn_background
from .agent_prompts import AgentPromptsMixin
from .agent_tools import AgentToolsMixin
from .cancellation import CancellationToken
from .llm_providers import BaseLLMProvider
from .persistence import AgentStateRepository
from .skills.registry import SkillRegistry
from .tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class AutonomousHealthAgent(AgentPromptsMixin, AgentToolsMixin, AgentLoopsMixin):
    """
    Single-agent event-driven health assistant.

    Processes events sequentially:
      1. User chat messages from any gateway (Telegram, Feishu, …) via the
         shared InboxQueue
      2. Scheduled/on-demand analysis tasks (via _analysis_queue)
      3. Quick analysis requests (via run_quick_analysis coroutine)
    """

    def __init__(
        self,
        user_id: str,
        llm_provider: BaseLLMProvider,
        data_store,
        memory_db_path,
        max_context_tokens: int = 100000,
        tool_registry: ToolRegistry | None = None,
    ):
        self.user_id = user_id
        self.llm = llm_provider
        self.data_store = data_store
        self.memory_db_path = memory_db_path
        self.max_context_tokens = max_context_tokens

        # Skills subsystem — scan configured + default roots once at init.
        # Later mode switches reuse the same registry; call
        # ``self.skill_registry.refresh()`` to pick up on-disk changes.
        self.skill_registry = SkillRegistry(roots=self._resolve_skill_roots())

        # Tool registry
        if tool_registry:
            self.tool_registry = tool_registry
        else:
            self.tool_registry = ToolRegistry.with_default_tools(
                data_store, memory_db_path, user_id,
                skill_registry=self.skill_registry,
            )

        # Inject LLM provider into tools that support semantic verification.
        # Use a dedicated lightweight provider if configured, else share the agent's.
        verify_provider = self._create_fact_verify_provider() or llm_provider
        for tool_name in ("push_report", "reply_user"):
            tool = self.tool_registry.get_tool(tool_name)
            if tool and hasattr(tool, "_llm_provider"):
                tool._llm_provider = verify_provider

        # Persistence
        self.state_repo = AgentStateRepository(memory_db_path / "agent_states")

        # Agent state
        self.state = "initialized"
        self.state_start_time = time.time()
        self.state_metadata: dict = {}

        # Per-loop state for UI display — derived from the unified state.
        # Only one of analysis/chat is active at a time; the other stays "idle".
        self._analysis_state = "initialized"
        self._analysis_state_start_time = self.state_start_time
        self._analysis_state_metadata: dict = {}
        self._chat_state = "idle"
        self._chat_state_start_time = self.state_start_time
        self._chat_state_metadata: dict = {}

        self.is_running = False
        self.cycle_count = 0
        self.last_analysis_complete_time: str | None = None
        self.last_sleep_time = time.time()   # kept for compat, updated after analysis
        self.last_data_check_time = None
        self.current_simulation_timestamp = None
        self.cycle_messages: list[dict] = []  # kept for compat / state persistence

        self.max_turns = getattr(settings, "AGENT_MAX_ITERATIONS", 100)
        self.context_window_size = getattr(settings, "AGENT_CONTEXT_WINDOW_SIZE", 20)

        # Shared inbox — every messaging gateway pushes user messages here
        self.inbox = InboxQueue()
        self.user_messages_received = 0

        # Queue for analysis tasks pushed by cron scheduler or user commands
        self._analysis_queue: asyncio.Queue = asyncio.Queue(maxsize=50)

        # Dedupe window for scheduled/manual analysis goals: maps goal →
        # monotonic timestamp of last enqueue. Guards against rare races
        # where the same cron tick is claimed twice (e.g. across a supervisor
        # restart) and the same goal reaches the queue back-to-back,
        # producing duplicate reports.
        self._recent_scheduled_goals: dict[str, float] = {}
        self._scheduled_dedupe_window_s: float = 120.0

        # Event queue for yielding events from run_forever
        self._event_queue: asyncio.Queue = asyncio.Queue()

        # Chat history per chat_id (sliding window)
        self._chat_histories: dict[str, list[dict]] = {}
        self._max_chat_history = getattr(settings, "CHAT_HISTORY_SIZE", 20)

        # Token usage tracking
        self.cumulative_tokens = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "thoughts_tokens": 0,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
        }

        # Serialises iOS quick checks (two overlapping runs would each publish a
        # report and fight over the agent state).
        self._quick_lock = asyncio.Lock()

        # State persistence is coalesced + written off the event loop (see
        # ``_save_state``).
        self._state_dirty = False
        self._state_flush_task: asyncio.Task | None = None

        # Cancellation token — cascade cancel to all child operations on stop
        self._cancellation = CancellationToken()
        # Per-run chat cancellation (POST /api/agent/chat/stop). The task is the
        # in-flight ``_handle_chat_message``; the flag lets run_forever tell a
        # user stop apart from agent shutdown. ``_chat_run_id`` tags chat_*
        # events (see ``_emit``) so clients can group them per user message.
        self._chat_task: asyncio.Task | None = None
        self._chat_stop_requested: bool = False
        self._chat_run_id: str | None = None
        # Thread of the in-flight in-app chat run (None for IM / idle).
        self._chat_thread_id: str | None = None

        # Set up tool progress callbacks (push events to WebSocket stream)
        self._setup_tool_progress()

        # Restore persisted state
        self._restore_state()

        # Build the chat-history MemoryManager (DDL / migrations) now, at
        # construction, rather than lazily on the event loop mid-conversation.
        try:
            self._chat_memory()
        except Exception as exc:
            logger.warning("MemoryManager pre-init failed (will retry lazily): %s", exc)

        logger.info("AutonomousHealthAgent initialized for %s", user_id)

    @staticmethod
    def _resolve_skill_roots() -> list[Path]:
        """Build the ordered list of skill root directories to scan.

        Order (first match wins for name conflicts):
        1. ``HIME_SKILLS_DIR`` from settings, split on ``os.pathsep``
        2. ``~/.hime/skills`` (user-local overrides)
        3. ``./skills`` (bundled / workspace-local)

        Non-existent and duplicate paths are filtered by SkillRegistry.
        """
        roots: list[Path] = []
        raw = getattr(settings, "HIME_SKILLS_DIR", "") or ""
        for piece in raw.split(os.pathsep):
            piece = piece.strip()
            if piece:
                roots.append(Path(piece).expanduser())
        roots.append(Path.home() / ".hime" / "skills")
        roots.append(Path("./skills"))
        return roots

    @staticmethod
    def _create_fact_verify_provider() -> BaseLLMProvider | None:
        """Create a dedicated LLM provider for fact verification if configured."""
        provider_name = settings.FACT_VERIFY_LLM_PROVIDER
        if not provider_name:
            return None
        try:
            from .llm_providers import create_provider
            model = settings.FACT_VERIFY_LLM_MODEL or None
            p = create_provider(provider_name, model=model)
            logger.info("Fact verification using dedicated provider: %s/%s", provider_name, model or "default")
            return p
        except Exception as exc:
            logger.warning("Failed to create fact-verify provider (%s), falling back to agent's: %s", provider_name, exc)
            return None

    # ==================================================================
    # State management
    # ==================================================================

    def _set_state(self, new_state: str, metadata: dict | None = None, loop: str = "analysis") -> None:
        """Update agent state and persist."""
        now = time.time()
        self.state = new_state
        self.state_start_time = now
        self.state_metadata = metadata or {}
        if loop == "analysis":
            self._analysis_state = new_state
            self._analysis_state_start_time = now
            self._analysis_state_metadata = metadata or {}
        else:
            self._chat_state = new_state
            self._chat_state_start_time = now
            self._chat_state_metadata = metadata or {}
        self._save_state()

    # Debounce window for state writes: ``_set_state`` fires several times per
    # turn and every write fsyncs, so writes are coalesced into one per window.
    _STATE_FLUSH_DELAY_S = 0.5

    def _state_snapshot_json(self) -> str:
        """Consistent JSON snapshot of the persisted state (taken on the loop)."""
        state = {
            "cycle_count": self.cycle_count,
            "last_sleep_time": self.last_sleep_time,
            "last_data_check_time": self.last_data_check_time,
            "cycle_messages": self.cycle_messages,
            "current_simulation_timestamp": self.current_simulation_timestamp,
            "state": self.state,
            "state_start_time": self.state_start_time,
            "state_metadata": self.state_metadata,
            "chat_histories": self._chat_histories,
            "cumulative_tokens": self.cumulative_tokens,
            "last_updated": ts_now(),
        }
        return json.dumps(state, indent=2, default=str)

    def _save_state(self) -> None:
        """Persist agent state.

        Inside a running event loop the write is coalesced (one per
        ``_STATE_FLUSH_DELAY_S``) and its blocking file I/O + fsync runs in a
        worker thread, so per-turn ``_set_state`` calls never stall the loop.
        Outside a loop (sync callers, tests) it writes immediately.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            self._write_state_now()
            return
        self._state_dirty = True
        task = self._state_flush_task
        if task is None or task.done():
            self._state_flush_task = spawn_background(
                self._flush_state_loop(), label="agent state flush",
            )

    def _write_state_now(self) -> None:
        try:
            self.state_repo.save_state_json(self.user_id, self._state_snapshot_json())
        except Exception as e:
            logger.error("Error saving state: %s", e)

    async def _flush_state_loop(self) -> None:
        """Write dirty state, at most once per debounce window."""
        while self._state_dirty:
            await asyncio.sleep(self._STATE_FLUSH_DELAY_S)
            self._state_dirty = False
            try:
                snapshot = self._state_snapshot_json()
                await asyncio.to_thread(self.state_repo.save_state_json, self.user_id, snapshot)
            except Exception as e:
                logger.error("Error saving state: %s", e)

    async def flush_state(self) -> None:
        """Write any pending state now (shutdown / tests)."""
        self._state_dirty = False
        snapshot = self._state_snapshot_json()
        await asyncio.to_thread(self.state_repo.save_state_json, self.user_id, snapshot)

    def _restore_state(self) -> None:
        try:
            state = self.state_repo.load_state(self.user_id)
            if state:
                self.cycle_count = state.get("cycle_count", 0)
                self.last_sleep_time = state.get("last_sleep_time", time.time())
                self.cumulative_tokens = state.get(
                    "cumulative_tokens",
                    {"prompt_tokens": 0, "completion_tokens": 0, "thoughts_tokens": 0},
                )
                self.last_data_check_time = state.get("last_data_check_time")
                self.cycle_messages = state.get("cycle_messages", [])
                self.current_simulation_timestamp = state.get("current_simulation_timestamp")
                # Transient in-flight states must not survive a restart —
                # they'd show stale durations (e.g. "Quick Analysis (559s)").
                _TRANSIENT_STATES = {"quick_analysis", "analyzing", "chat_analyzing"}
                restored_state = state.get("state", "restored")
                if restored_state in _TRANSIENT_STATES:
                    restored_state = "idle"
                self.state = restored_state
                self.state_start_time = time.time()
                self.state_metadata = state.get("state_metadata", {})
                s = self.state
                if s and s.startswith("chat_"):
                    self._chat_state = s
                    self._chat_state_start_time = self.state_start_time
                    self._chat_state_metadata = self.state_metadata
                    self._analysis_state = "idle"
                    self._analysis_state_start_time = self.state_start_time
                    self._analysis_state_metadata = {}
                else:
                    self._analysis_state = s or "idle"
                    self._analysis_state_start_time = self.state_start_time
                    self._analysis_state_metadata = self.state_metadata
                    self._chat_state = "idle"
                    self._chat_state_start_time = self.state_start_time
                    self._chat_state_metadata = {}
                self._chat_histories = state.get("chat_histories", {})
                logger.info(
                    "Restored agent state for %s: cycle %d, %d chat histories",
                    self.user_id, self.cycle_count, len(self._chat_histories),
                )
        except Exception as e:
            logger.error("Error restoring state: %s", e)

    # ==================================================================
    # Event helper
    # ==================================================================

    async def _emit(self, event: dict) -> None:
        # Stamp every chat event emitted during a handled message with its run
        # id (additive field; also covers gateway-emitted chat_reply/chat_image).
        etype = event.get("type", "")
        is_chat = isinstance(etype, str) and (
            etype.startswith("chat_") or etype == "user_message"
        ) and etype not in ("chat_thread_updated", "chat_thread_deleted")
        rid = self._chat_run_id
        if rid and is_chat and "run_id" not in event:
            event["run_id"] = rid
        # In-app threads: every chat event names its thread. An explicit
        # chat_id (``<uid>`` / ``<uid>:<tid>``) wins, e.g. proactive pushes
        # are main even if a thread's run is in flight.
        if is_chat and "thread_id" not in event:
            from ..ios_gateway.threads import thread_id_from_chat_id
            cid = event.get("chat_id")
            uid = self.user_id
            if isinstance(cid, str) and (cid == uid or cid.startswith(f"{uid}:")):
                event["thread_id"] = thread_id_from_chat_id(cid, uid)
            elif self._chat_thread_id:
                event["thread_id"] = self._chat_thread_id
        await self._event_queue.put(event)

    def stop_chat(self, thread_id: str | None = None) -> bool:
        """Cancel the in-flight chat run (not the agent, not queued analysis).

        Returns True if a run was active and cancellation was requested. The
        run_forever chat worker sees the cancelled task, treats it as a user
        stop (not shutdown) and carries on with the next queued message.
        """
        task = self._chat_task
        if task is None or task.done():
            return False
        if thread_id is not None and (self._chat_thread_id or "main") != thread_id:
            return False  # the active run belongs to another thread
        self._chat_stop_requested = True
        task.cancel()
        return True

    async def _run_chat_envelope(self, env: Any) -> None:
        """Run ``_handle_chat_message`` as a stoppable child task.

        ``asyncio.wait`` (instead of ``await task``) keeps the two sources of
        cancellation apart: shutdown cancels *this* coroutine (CancelledError
        raised here, child cancelled and re-raised), while a user stop cancels
        only the child, which simply comes back ``cancelled()``. No reliance on
        ``Task.cancelling()`` so it works on Python 3.10.
        """
        from .chat_threads import envelope_thread_id
        self._chat_stop_requested = False
        self._chat_thread_id = envelope_thread_id(env, self.user_id)
        task = asyncio.create_task(self._handle_chat_message(env))
        self._chat_task = task
        try:
            await asyncio.wait({task})
            # Explicit end-of-run marker (additive): lets clients settle the
            # turn block as soon as the run is over, however it ended.
            await self._emit({"type": "chat_run_done"})
        except asyncio.CancelledError:
            task.cancel()
            raise
        finally:
            self._chat_task = None
            self._chat_run_id = None
            self._chat_thread_id = None
        if task.cancelled():
            logger.info("Chat run stopped by user")
            return
        exc = task.exception()
        if exc is not None:
            raise exc

    def _chat_memory(self):
        """Lazily-constructed MemoryManager for chat-history persistence.

        The agent persists its own state via ``state_repo``; chat-history
        persistence opens its own handle to the same memory DB on first use.
        """
        mm = getattr(self, "_chat_memory_mgr", None)
        if mm is None:
            from .memory_manager import MemoryManager
            mm = MemoryManager(self.memory_db_path, self.user_id)
            self._chat_memory_mgr = mm
        return mm

    # ==================================================================
    # run_forever — single event loop
    # ==================================================================

    async def run_forever(self) -> AsyncIterator[dict]:
        """
        Main entry point. Single event loop processing:
        1. User chat messages from the shared InboxQueue (any gateway)
        2. Scheduled/on-demand analysis tasks from _analysis_queue

        Events are yielded in real-time: work runs as background Tasks while
        the generator continuously drains _event_queue.
        """
        self.is_running = True
        # A restarted agent must not inherit the previous run's /stop.
        self._cancellation.reset()

        # Drain stale events
        while not self._event_queue.empty():
            try:
                self._event_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        yield {
            "type": "agent_started",
            "user_id": self.user_id,
            "timestamp": ts_now(),
        }

        # Notify user that agent is online (no initial analysis)
        try:
            reply_tool = self.tool_registry.get_tool("reply_user")
            if reply_tool:
                await reply_tool.execute(message="\U0001f431 HIME is online and ready!")
        except Exception:
            logger.debug("Could not send online notification via messaging gateway")

        # Pre-warm the IPython session in the background so the first code
        # tool call doesn't spend its entire 30 s timeout on initialisation.
        code_tool = self.tool_registry.get_tool("code")
        if code_tool and hasattr(code_tool, "warm_up"):
            spawn_background(code_tool.warm_up(), label="code tool warm-up")
            logger.info("Code tool warm-up task started")

        active_task: asyncio.Task | None = None

        try:
            while self.is_running:
                # Yield any queued events
                while not self._event_queue.empty():
                    try:
                        yield self._event_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break

                # If a work task is running, drain events until it finishes
                if active_task and not active_task.done():
                    try:
                        event = await asyncio.wait_for(self._event_queue.get(), timeout=0.15)
                        yield event
                    except asyncio.TimeoutError:
                        pass
                    continue

                # Collect result/error from completed task
                if active_task is not None:
                    if not active_task.cancelled() and active_task.exception():
                        err = active_task.exception()
                        logger.error("Work task error: %s", err, exc_info=err)
                        yield {"type": "agent_error", "error": str(err)}
                    active_task = None

                # --- Poll for pending work ---
                # Simple poll avoids asyncio.Queue.get() cancellation race
                # conditions that caused messages to be silently lost.

                # 1. Chat messages in inbox?
                if self.inbox.has_messages():
                    envelopes = await self.inbox.pop_all()
                    if envelopes:
                        async def _process_chats(envs):
                            for env in envs:
                                # One bad envelope must not drop the rest of
                                # the batch (they were already popped).
                                try:
                                    await self._run_chat_envelope(env)
                                except asyncio.CancelledError:
                                    raise
                                except Exception as exc:
                                    logger.error(
                                        "Chat message handling failed: %s", exc, exc_info=True,
                                    )
                                    await self._emit({
                                        "type": "agent_error",
                                        "error": f"chat message failed: {exc}",
                                    })
                        active_task = asyncio.create_task(_process_chats(envelopes))
                        continue

                # 2. Analysis tasks in queue?
                try:
                    goal = self._analysis_queue.get_nowait()
                    active_task = asyncio.create_task(self._run_one_shot_analysis(goal))
                    continue
                except asyncio.QueueEmpty:
                    pass

                # 3. Nothing pending — brief sleep then poll again
                await asyncio.sleep(0.15)

        except asyncio.CancelledError:
            logger.info("Agent run_forever cancelled")
            if active_task:
                active_task.cancel()
        except Exception as e:
            logger.error("Agent run_forever error: %s", e, exc_info=True)
            yield {"type": "agent_error", "error": str(e)}
        finally:
            self.is_running = False
            if active_task and not active_task.done():
                active_task.cancel()
            # Drain remaining events
            while not self._event_queue.empty():
                try:
                    yield self._event_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
            try:
                await self.flush_state()
            except Exception as exc:
                logger.debug("final state flush failed: %s", exc)
            yield {"type": "agent_stopped", "timestamp": ts_now()}

    # ==================================================================
    # Scheduled analysis — called by cron scheduler
    # ==================================================================

    async def run_scheduled_analysis(self, goal: str, task_id: int | str | None = None) -> None:
        """
        Queue an analysis task. Called by the cron scheduler or via chat command.
        The task will be processed in the main event loop.

        ``task_id`` (the ``scheduled_tasks`` row id, when the caller knows it)
        scopes the duplicate guard to that task. Without it the guard falls
        back to the goal text, which also collapses two *different* tasks that
        share the same goal.
        """
        # Dedupe: reject the same task (or, lacking an id, the same goal) if it
        # was queued within the window.
        dedupe_key = f"task:{task_id}" if task_id is not None else f"goal:{goal}"
        now_mono = time.monotonic()
        self._recent_scheduled_goals = {
            g: t for g, t in self._recent_scheduled_goals.items()
            if now_mono - t < self._scheduled_dedupe_window_s
        }
        last_queued = self._recent_scheduled_goals.get(dedupe_key)
        if last_queued is not None and (now_mono - last_queued) < self._scheduled_dedupe_window_s:
            logger.warning(
                "Dedupe: dropping scheduled goal queued %.1fs ago for %s: %s",
                now_mono - last_queued, self.user_id, (goal or "")[:60],
            )
            return
        self._recent_scheduled_goals[dedupe_key] = now_mono

        if self._analysis_queue.full():
            try:
                dropped = self._analysis_queue.get_nowait()
                logger.warning("Analysis queue full — dropped oldest task: %s", (dropped or "")[:60])
            except asyncio.QueueEmpty:
                pass
        await self._analysis_queue.put(goal)
        logger.info("Queued scheduled analysis for %s: %s", self.user_id, (goal or "default")[:60])

    # ==================================================================
    # Control
    # ==================================================================

    def _setup_tool_progress(self) -> None:
        """Inject progress callbacks into all tools — events are pushed to WebSocket."""
        from datetime import timezone

        def on_progress(tool_name: str, data) -> None:
            try:
                self._event_queue.put_nowait({
                    "type": "tool_progress",
                    "tool": tool_name,
                    "data": data,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
            except asyncio.QueueFull:
                pass  # Drop progress events if queue is full

        for tool in self.tool_registry.get_all_tools():
            tool.set_progress_callback(on_progress)

    def stop(self) -> None:
        self.is_running = False
        self._cancellation.cancel("Agent stopped")
        logger.info("Agent %s stopping...", self.user_id)

    def get_status(self) -> dict:
        now = time.time()
        return {
            "user_id": self.user_id,
            "is_running": self.is_running,
            "cycle_count": self.cycle_count,
            "last_sleep_time": self.last_sleep_time,
            "last_analysis_time": self.last_analysis_complete_time,
            "state": self.state,
            "state_duration": now - self.state_start_time,
            "state_metadata": self.state_metadata,
            "analysis_state": self._analysis_state,
            "analysis_state_duration": now - self._analysis_state_start_time,
            "analysis_state_metadata": self._analysis_state_metadata,
            "chat_state": self._chat_state,
            "chat_state_duration": now - self._chat_state_start_time,
            "chat_state_metadata": self._chat_state_metadata,
            "time_since_sleep": now - self.last_sleep_time,
            "current_turn_count": len(self.cycle_messages),
            "data_store_stats": self.data_store.get_stats(),
            "user_messages_received": self.user_messages_received,
            "cumulative_tokens": self.cumulative_tokens,
            "analysis_queue_size": self._analysis_queue.qsize(),
        }
