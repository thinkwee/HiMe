"""Agent event fan-out.

Historically every ``/api/stream/agent`` WebSocket drained the agent's single
``active_agents[uid]['event_queue']`` directly. With one viewer (the web
dashboard) that was fine, but the moment a second consumer appeared — the
native iOS chat socket — the two **competed** on the same queue: each event is
delivered to exactly one ``get()`` caller, so the app and the dashboard stole
events from each other and neither saw the full stream.

``EventHub`` fixes that: a single background pump drains the agent's
``event_queue`` and *broadcasts* every event to one private queue per
subscriber. Each WebSocket reads its own queue, so the app and the dashboard
both receive the complete stream. The pump is created lazily on first connect
(see ``ensure_hub``) so none of the agent-start paths need to change.
"""

import asyncio
import logging
from collections import deque

logger = logging.getLogger(__name__)

_REPLAY = 50          # events a late-joining subscriber sees immediately
_SUB_MAXSIZE = 500    # per-subscriber backlog cap (matches the old queue size)


class EventHub:
    """Broadcasts agent events to N independent subscriber queues."""

    def __init__(self, replay: int = _REPLAY, sub_maxsize: int = _SUB_MAXSIZE) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._recent: deque = deque(maxlen=replay)
        self._sub_maxsize = sub_maxsize

    def subscribe(self, replay: bool = True) -> asyncio.Queue:
        """Register a new subscriber.

        With ``replay=True`` the queue is pre-loaded with the recent backlog so
        a viewer that connects mid-conversation (e.g. the web dashboard, which
        has no history endpoint) still sees the tail. The iOS app must pass
        ``replay=False``: it loads past turns via ``/api/agent/chat-history`` and
        should only receive genuinely-live events — otherwise a WS reconnect
        would replay recent replies and the app would render them as duplicate
        bubbles."""
        q: asyncio.Queue = asyncio.Queue(maxsize=self._sub_maxsize)
        if replay:
            for ev in self._recent:
                try:
                    q.put_nowait(ev)
                except asyncio.QueueFull:
                    break
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def broadcast(self, event: dict) -> None:
        self._recent.append(event)
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Slow consumer — drop its oldest event and retry once.
                try:
                    q.get_nowait()
                    q.put_nowait(event)
                except Exception:
                    pass

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


async def _fanout_pump(user_id: str, source_queue: asyncio.Queue, hub: EventHub) -> None:
    """Sole reader of the agent's ``event_queue``; fans every event out to all
    subscribers. Self-terminates shortly after the agent is removed from
    ``active_agents`` (e.g. stop/restart) so it never leaks."""
    from .agent_state import active_agents
    try:
        while user_id in active_agents:
            try:
                event = await asyncio.wait_for(source_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            hub.broadcast(event)
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.error("fan-out pump crashed for %s", user_id, exc_info=True)


def ensure_hub(user_id: str, agent_info: dict) -> EventHub:
    """Return the agent's EventHub, creating it (and its pump) on first use.

    The check-and-set is synchronous (no ``await`` between the lookup and the
    assignment), so concurrent WebSocket connects can't start duplicate pumps.
    """
    hub = agent_info.get("event_hub")
    if hub is None:
        hub = EventHub()
        event_queue = agent_info.get("event_queue")
        agent_info["event_hub"] = hub
        agent_info["fanout_task"] = asyncio.create_task(
            _fanout_pump(user_id, event_queue, hub),
            name=f"fanout-{user_id}",
        )
    return hub
