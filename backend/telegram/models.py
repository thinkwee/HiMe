"""Backward-compatibility shim for ``backend.telegram.models``.

The canonical data models now live in ``backend.messaging.base``. This
module re-exports them so any code importing from the old location keeps
working unchanged.
"""
from __future__ import annotations

from ..messaging.base import (
    CommandType,
    MessageChannel,
    MessageEnvelope,
    ParsedCommand,
)

__all__ = [
    "CommandType",
    "MessageChannel",
    "MessageEnvelope",
    "ParsedCommand",
]
