"""
Cancellation signal propagation for the autonomous health agent.

Provides a hierarchical cancellation system where parent tokens
cascade cancellation to all child tokens. Used to cleanly stop
tool execution when the agent is stopped.

Usage:
    root = CancellationToken()
    child = root.create_child()

    # In tool execution:
    child.check()  # Raises CancelledError if cancelled

    # When stopping the agent:
    root.cancel("Agent stopped")  # All children are also cancelled
"""
from __future__ import annotations

import asyncio


class CancelledError(Exception):
    """Raised when an operation is cancelled via CancellationToken."""

    def __init__(self, reason: str = "Operation cancelled") -> None:
        self.reason = reason
        super().__init__(reason)


class CancellationToken:
    """Hierarchical cancellation token supporting parent-child cascading."""

    def __init__(self, parent: CancellationToken | None = None) -> None:
        self._cancelled = False
        self._reason = ""
        self._parent = parent
        self._children: list[CancellationToken] = []
        if parent is not None:
            parent._children.append(self)

    @property
    def is_cancelled(self) -> bool:
        """Check if this token or any ancestor has been cancelled."""
        if self._cancelled:
            return True
        if self._parent is not None and self._parent.is_cancelled:
            return True
        return False

    def cancel(self, reason: str = "") -> None:
        """Cancel this token and all descendants."""
        self._cancelled = True
        self._reason = reason
        for child in self._children:
            child.cancel(reason)

    def check(self) -> None:
        """Raise CancelledError if this token is cancelled.

        Call this at checkpoints in long-running operations.
        """
        if self.is_cancelled:
            raise CancelledError(self._reason or "Operation cancelled")

    def create_child(self) -> CancellationToken:
        """Create a child token that inherits cancellation from this one."""
        return CancellationToken(parent=self)

    def reset(self) -> None:
        """Reset this token (and detach old children).

        Used when restarting the agent.
        """
        self._cancelled = False
        self._reason = ""
        self._children.clear()

    async def wait_for_cancellation(self, timeout: float | None = None) -> None:
        """Block until this token is cancelled or timeout expires."""
        poll_interval = 0.1
        elapsed = 0.0
        while not self.is_cancelled:
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
            if timeout is not None and elapsed >= timeout:
                break
