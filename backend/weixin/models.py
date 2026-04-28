"""Re-export of platform-agnostic message models for the WeChat gateway."""
from __future__ import annotations

from ..messaging.base import (
    BaseGateway,
    CommandType,
    MessageChannel,
    MessageEnvelope,
    ParsedCommand,
)

__all__ = [
    "BaseGateway",
    "CommandType",
    "MessageChannel",
    "MessageEnvelope",
    "ParsedCommand",
]
