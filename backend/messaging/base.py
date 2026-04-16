"""Platform-agnostic data models and the ``BaseGateway`` interface.

Inspired by OpenClaw's Channel Envelope pattern — inbound messages are
normalised into a ``MessageEnvelope`` before being routed to handlers.
Outbound sends go through a ``BaseGateway`` implementation selected via the
shared :class:`backend.messaging.registry.GatewayRegistry`.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class MessageChannel(str, Enum):
    """Origin channel of a message."""

    TELEGRAM = "telegram"
    FEISHU = "feishu"
    WEB = "web"
    API = "api"
    BENCHMARK = "benchmark"


class CommandType(str, Enum):
    """Recognised slash-commands."""

    ASK = "ask"
    HELP = "help"
    CLEAR = "clear"
    MUTE = "mute"
    UNMUTE = "unmute"
    RESTART = "restart"
    LOG = "log"
    UNKNOWN = "unknown"


@dataclass
class MessageEnvelope:
    """Platform-agnostic message container.

    Every inbound message (Telegram, Feishu, web, API, ...) is first converted
    into a ``MessageEnvelope`` so that downstream routing and handling logic
    never depends on platform-specific details.

    ``chat_id`` and ``telegram_message_id`` are retained as Telegram-compatible
    convenience fields for backward compatibility with existing code paths.
    Feishu (and future channels) populate ``conversation_id`` /
    ``platform_message_id`` and mirror into ``chat_id`` so legacy callers that
    read ``envelope.chat_id`` continue to work.
    """

    message_id: str
    channel: MessageChannel
    sender_id: str
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    reply_to: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    # Telegram-compatible convenience fields (mirrored on other channels).
    chat_id: str | None = None
    telegram_message_id: int | None = None

    # Generic platform metadata (used by Feishu and any future channels).
    conversation_id: str | None = None
    platform_message_id: str | None = None


@dataclass
class ParsedCommand:
    """Result of parsing a slash-command from raw text."""

    command_type: CommandType
    args: str = ""
    raw_text: str = ""
    envelope: MessageEnvelope | None = None


class BaseGateway(abc.ABC):
    """Abstract interface for all messaging gateways.

    Every concrete gateway (Telegram, Feishu, ...) implements this interface so
    that tools like ``reply_user`` / ``push_report`` can route messages through
    the :class:`backend.messaging.registry.GatewayRegistry` without caring
    which platform actually owns the current conversation.

    Concrete gateways expose:
    - ``channel``: a :class:`MessageChannel` identifying the platform.
    - ``default_chat_id``: the fallback destination for outbound pushes.
    - ``allowed_chat_ids``: an optional default-deny whitelist of allowed IDs.
    - ``sender``: an opaque sender handle used by legacy code paths that need
      to call ``send_chat_action`` or similar platform-specific helpers.
    """

    #: The channel this gateway serves (set by subclasses).
    channel: MessageChannel

    #: Default destination chat for outbound pushes (``None`` => no push).
    default_chat_id: str | None = None

    #: Whitelist of chat IDs allowed to interact (``None``/empty => deny all).
    allowed_chat_ids: set[str] | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @abc.abstractmethod
    async def start(self) -> None:
        """Start the gateway (open transports, sessions, pollers, ...)."""

    @abc.abstractmethod
    async def stop(self) -> None:
        """Stop the gateway gracefully."""

    # ------------------------------------------------------------------
    # Outbound messaging
    # ------------------------------------------------------------------

    @abc.abstractmethod
    async def send_message(
        self,
        text: str,
        chat_id: str | None = None,
        reply_to_message_id: int | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> bool:
        """Send a text message. Returns ``True`` on success."""

    @abc.abstractmethod
    async def send_photo(
        self,
        photo_path: str,
        caption: str = "",
        chat_id: str | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> bool:
        """Send a photo with optional caption."""

    @abc.abstractmethod
    async def edit_message(
        self,
        chat_id: str,
        message_id: Any,
        text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> bool:
        """Edit an existing message's text in-place."""

    @abc.abstractmethod
    async def answer_callback(
        self,
        callback_id: str,
        text: str = "",
        show_alert: bool = False,
    ) -> bool:
        """Acknowledge an inline-button callback."""

    # ------------------------------------------------------------------
    # Optional convenience helpers
    # ------------------------------------------------------------------

    def is_muted(self) -> bool:
        """Return whether autonomous pushes should be suppressed."""
        return False
