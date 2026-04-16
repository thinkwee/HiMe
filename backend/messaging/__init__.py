"""Platform-agnostic messaging abstraction.

This package hosts the ``BaseGateway`` interface, shared envelope/command
models, and the ``GatewayRegistry`` used by tools to route outbound messages
to the correct channel (Telegram, Feishu, ...).

Concrete gateway implementations live in ``backend.telegram`` and
``backend.feishu`` and register themselves with the shared
``GatewayRegistry`` during application startup.
"""
from .base import (
    BaseGateway,
    CommandType,
    MessageChannel,
    MessageEnvelope,
    ParsedCommand,
)
from .command_parser import CommandParser
from .inbox import InboxQueue
from .registry import GatewayRegistry

__all__ = [
    "BaseGateway",
    "CommandParser",
    "CommandType",
    "GatewayRegistry",
    "InboxQueue",
    "MessageChannel",
    "MessageEnvelope",
    "ParsedCommand",
]
