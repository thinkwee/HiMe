"""
Tool registry — manages agent tools.

Tool routing goes through :class:`GatewayRegistry` so a single
``reply_user`` / ``push_report`` tool instance can dispatch to any enabled
channel (Telegram, Feishu, …). The legacy ``telegram_sender=`` kwarg is still
accepted for backward compatibility with tests and benchmark harnesses.
"""
import logging

from ...config import settings
from ...messaging.base import MessageChannel
from ...messaging.registry import GatewayRegistry
from .base import BaseTool
from .code_tool import CodeTool
from .create_page_tool import CreatePageTool
from .finish_chat_tool import FinishChatTool
from .push_report_tool import PushReportTool
from .read_skill_tool import ReadSkillTool
from .reply_user_tool import ReplyUserTool
from .sql_tool import SQLTool
from .update_md_tool import UpdateMdTool

if False:  # typing-only
    from ..skills.registry import SkillRegistry  # noqa: F401

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Registry for agent tools."""

    def __init__(self):
        self._tools: dict[str, BaseTool] = {}

    def register_tool(self, tool: BaseTool) -> None:
        definition = tool.get_definition()
        name = definition.get("function", {}).get("name")
        if not name:
            logger.error("Tool %s has invalid definition, skipping registration", type(tool).__name__)
            return
        self._tools[name] = tool
        logger.debug("Registered tool: %s", name)

    def get_tool(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def get_all_tools(self) -> list[BaseTool]:
        return list(self._tools.values())

    def get_definitions(self) -> list[dict]:
        return [tool.get_definition() for tool in self._tools.values()]

    @classmethod
    def with_default_tools(
        cls,
        data_store,
        memory_db_path,
        user_id: str,
        telegram_sender=None,
        default_chat_id: str | None = None,
        gateway_registry: GatewayRegistry | None = None,
        skill_registry: "SkillRegistry | None" = None,
    ) -> "ToolRegistry":
        """Factory: create a registry pre-populated with the default tools.

        Parameters
        ----------
        gateway_registry :
            Preferred way to wire outbound messaging — a pre-built
            :class:`GatewayRegistry` containing every enabled gateway
            (Telegram, Feishu, ...). When provided, the reply / push tools
            will dispatch through it.
        telegram_sender :
            Legacy knob (benchmarks, older tests). When supplied without a
            ``gateway_registry``, a temporary registry is built that wraps
            the sender as a minimal Telegram gateway, so reply/push tools
            continue to work without code changes.
        default_chat_id :
            Fallback destination chat for the legacy Telegram path.
        skill_registry :
            If provided, ``ReadSkillTool`` is registered so the agent can
            load openclaw-style skills on demand.  Pass ``None`` to omit.
        """
        registry = cls()

        # Fact verifier for evidence tracking
        fact_verifier = None
        if memory_db_path and user_id and user_id != "dummy":
            try:
                from ..fact_verifier import FactVerifier
                fact_verifier = FactVerifier(memory_db_path, user_id)
            except Exception as e:
                logger.error("FactVerifier init failed: %s", e)
                fact_verifier = None

        # Backward-compat: if only telegram_sender was supplied, wrap it in a
        # temporary registry so the rest of the code stays single-path.
        if gateway_registry is None and telegram_sender is not None:
            gateway_registry = _build_legacy_telegram_registry(
                telegram_sender, default_chat_id,
            )

        registry.register_tool(SQLTool(data_store, memory_db_path, user_id))
        registry.register_tool(CodeTool(data_store, memory_db_path, user_id))
        registry.register_tool(PushReportTool(
            memory_db_path, user_id,
            telegram_sender=telegram_sender,
            gateway_registry=gateway_registry,
            fact_verifier=fact_verifier,
        ))
        registry.register_tool(UpdateMdTool())
        registry.register_tool(FinishChatTool())
        registry.register_tool(CreatePageTool(
            memory_db_path=memory_db_path,
            user_id=user_id,
            data_store_path=settings.DATA_STORE_PATH,
        ))

        if gateway_registry is not None and len(gateway_registry) > 0:
            registry.register_tool(
                ReplyUserTool(
                    gateway_registry=gateway_registry,
                    default_chat_id=default_chat_id,
                    fact_verifier=fact_verifier,
                )
            )

        if skill_registry is not None:
            registry.register_tool(ReadSkillTool(skill_registry=skill_registry))

        return registry


# ---------------------------------------------------------------------------
# Legacy helper: wrap a bare TelegramSender in a minimal gateway so older
# callers (tests, benchmarks) can opt into the new registry plumbing without
# building a full TelegramGateway.
# ---------------------------------------------------------------------------

def _build_legacy_telegram_registry(
    telegram_sender,
    default_chat_id: str | None,
) -> GatewayRegistry:
    """Build a ``GatewayRegistry`` containing only a minimal Telegram adapter
    around an existing ``TelegramSender`` instance.
    """
    from ...messaging.base import BaseGateway  # local import avoids cycles

    class _LegacyTelegramGateway(BaseGateway):
        channel = MessageChannel.TELEGRAM

        def __init__(self, sender, chat_id: str | None) -> None:
            self.sender = sender
            self.default_chat_id = chat_id
            self.allowed_chat_ids = None

        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            return None

        async def send_message(self, text, chat_id=None, reply_to_message_id=None, reply_markup=None):
            return await self.sender.send_message(
                text=text,
                chat_id=chat_id,
                reply_to_message_id=reply_to_message_id,
                reply_markup=reply_markup,
            )

        async def send_photo(self, photo_path, caption="", chat_id=None, reply_markup=None):
            return await self.sender.send_photo(
                photo_path=photo_path,
                caption=caption,
                chat_id=chat_id,
                reply_markup=reply_markup,
            )

        async def edit_message(self, chat_id, message_id, text, reply_markup=None):
            return await self.sender.edit_message_text(
                chat_id=chat_id,
                message_id=int(message_id),
                text=text,
                reply_markup=reply_markup,
            )

        async def answer_callback(self, callback_id, text="", show_alert=False):
            return await self.sender.answer_callback_query(
                callback_query_id=callback_id,
                text=text,
                show_alert=show_alert,
            )

        def is_muted(self) -> bool:
            return getattr(self.sender, "is_muted", lambda: False)()

    reg = GatewayRegistry()
    reg.register(_LegacyTelegramGateway(telegram_sender, default_chat_id))
    return reg
