import json
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)


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
    _current_tool_results: list[dict[str, Any]] = []
    _current_user_message: str = ""  # user's original message for fabrication context

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
        """Load tool definition from the centralized JSON file."""
        json_path = Path(__file__).parent / "tools.json"
        if not json_path.exists():
            return {}
        try:
            with open(json_path, encoding="utf-8") as f:
                data = json.load(f)
            return data.get(tool_name, {})
        except Exception:
            return {}

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
            return {"status": status, "detail": detail, "reply_markup": reply_markup}
        except Exception as exc:
            logger.debug("Evidence verification failed: %s", exc)
            return {"status": "verified", "detail": "", "reply_markup": None}

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
