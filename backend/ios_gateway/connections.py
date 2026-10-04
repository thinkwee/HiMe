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

# A connection that has shown real client activity (``touch``) and then goes
# quiet for longer than this counts as gone even if it was never explicitly
# unregistered (half-open socket). The WS handler enforces a tighter idle
# timeout (60s) and unregisters; this is the safety net behind it.
_PRESENCE_TTL_S = 90.0


class _Conn:
    __slots__ = ("last_seen", "active")

    def __init__(self) -> None:
        self.last_seen = time.monotonic()
        # True once the client has sent at least one message (heartbeat-aware
        # build). Legacy clients never do, so they are online for as long as
        # they stay registered and are never aged out by the TTL.
        self.active = False


class IOSConnectionRegistry:
    """In-memory map of ``user_id`` → set of live stream connections."""

    def __init__(self, ttl: float | None = None) -> None:
        # user_id -> {conn_id: _Conn}
        self._conns: dict[str, dict[str, _Conn]] = {}
        self._lock = asyncio.Lock()
        self._ttl = ttl  # None -> module default (read at call time)

    @property
    def ttl(self) -> float:
        return _PRESENCE_TTL_S if self._ttl is None else self._ttl

    async def register(self, user_id: str, conn_id: str) -> None:
        async with self._lock:
            self._conns.setdefault(user_id, {})[conn_id] = _Conn()
        logger.info(
            "iOS presence: +user=%s (conn=%s, online_users=%d)",
            user_id, conn_id, len(self._conns),
        )

    def touch(self, user_id: str, conn_id: str) -> None:
        """Record real client activity (a message received from the peer).

        Must only be called from the receive path, never from a server-side
        timer, otherwise presence stops reflecting the peer's liveness."""
        conn = (self._conns.get(user_id) or {}).get(conn_id)
        if conn is not None:
            conn.last_seen = time.monotonic()
            conn.active = True

    async def unregister(self, user_id: str, conn_id: str) -> None:
        """Remove a connection. Idempotent: a repeat call is a silent no-op."""
        async with self._lock:
            conns = self._conns.get(user_id)
            removed = conns is not None and conns.pop(conn_id, None) is not None
            if conns is not None and not conns:
                self._conns.pop(user_id, None)
        if removed:
            logger.info("iOS presence: -user=%s (conn=%s)", user_id, conn_id)

    def is_online(self, user_id: str) -> bool:
        """True if the user has at least one live stream connection."""
        conns = self._conns.get(user_id)
        if not conns:
            return False
        now = time.monotonic()
        ttl = self.ttl
        return any(
            (not c.active) or (now - c.last_seen) < ttl
            for c in list(conns.values())
        )

    def online_users(self) -> list[str]:
        return [u for u in list(self._conns) if self.is_online(u)]


# Process-global singleton — shared by the stream WS handler (writer of
# presence) and every IOSGateway (reader of presence).
ios_connections = IOSConnectionRegistry()
