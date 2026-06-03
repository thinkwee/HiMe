"""Live-presence registry for the in-app iOS channel.

Tracks whether the iOS app's ``/api/stream/agent`` WebSocket is open. The
:class:`~backend.ios_gateway.gateway.IOSGateway` consults it to decide
whether a reply was delivered live over the socket (online) or needs an
APNs push to reach a closed app (offline).

This is deliberately separate from the agent event queue: the event
queue is the *delivery* path (the WS drains it), while this registry only
answers the *presence* question. The iOS app closes its stream when it
backgrounds and reopens it on foreground, so "has a registered
connection" is a faithful online signal. ``_PRESENCE_TTL_S`` is only a
safety net for ungraceful disconnects where the handler's ``finally``
never runs.

Keyed by ``user_id`` so the same structure works in single-user mode
(``LiveUser``) and would extend cleanly to multiple identities.
"""
from __future__ import annotations

import asyncio
import logging
import time

logger = logging.getLogger(__name__)

# A connection whose last heartbeat is older than this counts as gone even
# if it was never explicitly unregistered (crash / killed socket). The WS
# handler refreshes the heartbeat every few seconds while connected, so a
# live foreground app stays comfortably under this.
_PRESENCE_TTL_S = 120.0


class IOSConnectionRegistry:
    """In-memory map of ``user_id`` → set of live stream connections."""

    def __init__(self) -> None:
        # user_id -> {conn_id: last_seen_monotonic}
        self._conns: dict[str, dict[str, float]] = {}
        self._lock = asyncio.Lock()

    async def register(self, user_id: str, conn_id: str) -> None:
        async with self._lock:
            self._conns.setdefault(user_id, {})[conn_id] = time.monotonic()
        logger.info(
            "iOS presence: +user=%s (conn=%s, online_users=%d)",
            user_id, conn_id, len(self._conns),
        )

    def touch(self, user_id: str, conn_id: str) -> None:
        """Refresh a connection's heartbeat (non-blocking, best-effort)."""
        conns = self._conns.get(user_id)
        if conns is not None and conn_id in conns:
            conns[conn_id] = time.monotonic()

    async def unregister(self, user_id: str, conn_id: str) -> None:
        async with self._lock:
            conns = self._conns.get(user_id)
            if conns is not None:
                conns.pop(conn_id, None)
                if not conns:
                    self._conns.pop(user_id, None)
        logger.info("iOS presence: -user=%s (conn=%s)", user_id, conn_id)

    def is_online(self, user_id: str) -> bool:
        """True if the user has at least one fresh live stream connection."""
        conns = self._conns.get(user_id)
        if not conns:
            return False
        now = time.monotonic()
        return any((now - ts) < _PRESENCE_TTL_S for ts in list(conns.values()))

    def online_users(self) -> list[str]:
        return [u for u in list(self._conns) if self.is_online(u)]


# Process-global singleton — shared by the stream WS handler (writer of
# presence) and every IOSGateway (reader of presence).
ios_connections = IOSConnectionRegistry()
