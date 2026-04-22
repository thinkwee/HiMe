"""Re-export of the platform-agnostic message models used by Feishu.

Feishu historically might need a handful of extra fields (open_id,
tenant_key, ...); these live inside ``MessageEnvelope.metadata`` so the
shared dataclass stays lean. This module simply re-exports the common
symbols so Feishu code can import from a single local namespace.
"""
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
