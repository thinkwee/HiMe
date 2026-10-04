import contextvars
import copy
import json
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-flow tool state
# ---------------------------------------------------------------------------
# Tools are singletons shared by every flow the agent runs (chat, cron, quick
# analysis, plan designer ...), and those flows can overlap at await points.
# State that belongs to *one* flow (evidence trail, envelope, emitter ...) is
# therefore stored in ContextVars: each asyncio Task sees its own value, and
# child tasks (``asyncio.wait_for`` wrappers) inherit a copy.

class _FlowVar:
    """Descriptor storing an attribute in a per-instance ``ContextVar``."""

    def __init__(self, default: Any = None) -> None:
        self._default = default
        self._name = ""

    def __set_name__(self, owner: type, name: str) -> None:
        self._name = name

    def _var(self, obj: Any) -> contextvars.ContextVar:
        key = f"_cv_{self._name}"
        var = obj.__dict__.get(key)
        if var is None:
            var = contextvars.ContextVar(f"{type(obj).__name__}.{self._name}", default=self._default)
            obj.__dict__[key] = var
        return var

    def __get__(self, obj: Any, owner: type | None = None) -> Any:
        if obj is None:
            return self._default
        return self._var(obj).get()

    def __set__(self, obj: Any, value: Any) -> None:
        self._var(obj).set(value)


# Role of the LLM sub-agent currently executing tools ("analysis", "plan",
# "manage", "chat"); ``None`` outside any agent loop.  Role-aware tools (sql)
# use it to decide what they may do -- e.g. sub_analysis can only *read* memory.
_tool_role: contextvars.ContextVar[str | None] = contextvars.ContextVar("tool_role", default=None)


def current_tool_role() -> str | None:
    """Role of the flow executing the current tool call (None = unrestricted)."""
    return _tool_role.get()


@contextmanager
def tool_role(role: str | None) -> Iterator[None]:
    """Run a block with *role* as the current tool role (restored on exit)."""
    token = _tool_role.set(role)
    try:
        yield
    finally:
        _tool_role.reset(token)


# tools.json is static; parse it once instead of once per get_definition().
_TOOL_DEFS_PATH = Path(__file__).parent / "tools.json"
_tool_defs_cache: dict[str, Any] | None = None


def load_tool_definitions() -> dict[str, Any]:
    """All tool definitions from ``tools.json`` (parsed once, then cached)."""
    global _tool_defs_cache
    if _tool_defs_cache is None:
        try:
            with open(_TOOL_DEFS_PATH, encoding="utf-8") as f:
                _tool_defs_cache = json.load(f)
        except Exception:
            return {}
    return _tool_defs_cache


class BaseTool(ABC):
    """Abstract base class for agent tools.

    Subclasses may define:
      - input_schema: Pydantic model for automatic input validation
      - is_concurrency_safe: whether the tool can run in parallel with others
      - _progress_callback: function called to report execution progress
    """

    # Subclasses that support evidence should set these attributes
    _fact_verifier: Any = None
    _llm_provider: Any = None
    # Per-flow (ContextVar-backed) -- see _FlowVar.
    _current_tool_results: list[dict[str, Any]] = _FlowVar([])
    _current_user_message: str = _FlowVar("")  # user's original message for fabrication context
    _current_chat_history: list = _FlowVar([])
    _event_emitter: Any = _FlowVar(None)  # async callable: agent's _emit, injected per call

    # --- Input validation (optional, subclass sets this) ---
    input_schema: type[BaseModel] | None = None

    # --- Concurrency safety (for parallel tool execution) ---
    @property
    def is_concurrency_safe(self) -> bool:
        """Whether this tool can run concurrently with other safe tools.

        Override in subclasses. Default is False (serial execution).
        Read-only tools (sql SELECT) should return True.
        """
        return False

    # --- Progress reporting ---
    _progress_callback: Callable[[str, Any], None] | None = None

    def set_progress_callback(self, callback: Callable[[str, Any], None]) -> None:
        self._progress_callback = callback

    def report_progress(self, data: Any) -> None:
        """Report execution progress to the event stream."""
        if self._progress_callback:
            try:
                name = getattr(self, "name", self.__class__.__name__)
                self._progress_callback(name, data)
            except Exception:
                pass  # progress is best-effort

    # --- Unified call with validation ---
    async def __call__(self, **kwargs: Any) -> dict[str, Any]:
        """Validate input (if schema defined), then execute.

        This is the preferred entry point. Falls back to execute()
        if no input_schema is defined.
        """
        # Layer 1: Pydantic schema validation
        if self.input_schema is not None:
            try:
                validated = self.input_schema(**kwargs)
                kwargs = validated.model_dump()
            except ValidationError as e:
                return {
                    "success": False,
                    "error": f"Invalid arguments:\n{self._format_validation_error(e)}",
                    "error_type": "validation",
                }

        # Layer 2: Semantic validation (subclass override)
        semantic_error = await self.validate_input(**kwargs)
        if semantic_error:
            return {
                "success": False,
                "error": semantic_error,
                "error_type": "semantic_validation",
            }

        return await self.execute(**kwargs)

    async def validate_input(self, **kwargs: Any) -> str | None:
        """Semantic validation hook. Return error string or None if valid.

        Override in subclasses for domain-specific checks beyond schema.
        """
        return None

    @staticmethod
    def _format_validation_error(e: ValidationError) -> str:
        """Format Pydantic errors into LLM-friendly messages."""
        errors = []
        for err in e.errors():
            field = ".".join(str(x) for x in err["loc"])
            msg = err["msg"]
            errors.append(f"  - {field}: {msg}")
        return "\n".join(errors)

    def _get_definition_from_json(self, tool_name: str) -> dict[str, Any]:
        """Load tool definition from the centralized (cached) JSON file."""
        return copy.deepcopy(load_tool_definitions().get(tool_name, {}))

    async def _verify_and_build_markup(self, message_text: str) -> dict:
        """Verify message and build the "Show Evidence" reply markup.

        Returns ``{"status": str, "detail": str, "reply_markup": dict | None}``.
        Shared by push_report and reply_user tools.

        The ``reply_markup`` dict follows the Telegram inline-keyboard wire
        format (``{"inline_keyboard": [[{"text", "callback_data"}]]}``) and is
        the gateway-agnostic payload both senders understand: Telegram passes
        it straight to the Bot API; :class:`backend.feishu.sender.FeishuSender`
        translates it into a Feishu card action (see
        ``_reply_markup_to_card``) carrying the same ``message_hash`` so the
        evidence lookup is identical across platforms.

        Statuses that should block sending: ``"fabricated"``, ``"unverified"``.
        """
        if not self._fact_verifier:
            return {"status": "verified", "detail": "", "reply_markup": None}
        try:
            chat_history = getattr(self, "_current_chat_history", None) or []
            vresult = await self._fact_verifier.verify_message(
                message_text=message_text,
                tool_results=self._current_tool_results or [],
                llm_provider=self._llm_provider,
                user_message=self._current_user_message,
                chat_history=chat_history,
            )
            status = vresult.get("status", "verified")
            detail = vresult.get("detail", "")
            msg_hash = vresult.get("message_hash", "")
            reply_markup = None
            if msg_hash:
                reply_markup = {
                    "inline_keyboard": [[
                        {
                            "text": "\U0001f4ca Show Evidence",
                            "callback_data": f"evidence:{msg_hash}",
                        }
                    ]]
                }
            await self._emit_verification_event(
                status=status, detail=detail, msg_hash=msg_hash,
                message_text=message_text,
            )
            return {"status": status, "detail": detail, "reply_markup": reply_markup}
        except Exception as exc:
            logger.debug("Evidence verification failed: %s", exc)
            return {"status": "verified", "detail": "", "reply_markup": None}

    async def _emit_verification_event(
        self, *, status: str, detail: str, msg_hash: str, message_text: str,
    ) -> None:
        """Stream the fact-verifier verdict to the agent monitor.

        Best-effort: any failure is swallowed so monitor wiring never breaks
        the user-facing reply path.
        """
        emitter = getattr(self, "_event_emitter", None)
        if emitter is None:
            return
        evidence_count = len(self._current_tool_results or [])
        scenario = "chat" if evidence_count == 0 else "data_or_op"
        payload = {
            "type": "chat_verification",
            "tool": getattr(self, "name", "unknown"),
            "scenario": scenario,
            "status": status,
            "detail": (detail or "")[:300],
            "evidence_count": evidence_count,
            "message_hash": msg_hash,
            "preview": (message_text or "")[:200],
        }
        try:
            await emitter(payload)
        except Exception as exc:
            logger.debug("verification event emit failed: %s", exc)

    @abstractmethod
    def get_definition(self) -> dict:
        """
        Get the tool definition in OpenAI/Gemini function calling format.

        Returns:
            Dict containing 'type', 'function', 'name', 'description', and 'parameters'.
        """

    @abstractmethod
    async def execute(self, **kwargs) -> dict[str, Any]:
        """
        Execute the tool action.

        Args:
            **kwargs: Arguments provided by the LLM.

        Returns:
            Dict containing the execution result (must be JSON serializable).
            Standard keys: 'success', 'error', 'result', etc.
        """
