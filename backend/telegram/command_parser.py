"""Backward-compatibility shim for ``backend.telegram.command_parser``.

The implementation now lives in ``backend.messaging.command_parser``; this
module re-exports it so existing Telegram-specific imports keep working.
"""
from __future__ import annotations

from ..messaging.command_parser import CommandParser

__all__ = ["CommandParser"]
