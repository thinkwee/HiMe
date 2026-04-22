"""InboxQueue — thread-safe async queue for inbound user messages.

Every messaging gateway (Telegram, Feishu, ...) pushes ``MessageEnvelope``
objects into a single shared queue when a user sends free-form text. The
agent's single event loop consumes them with highest priority via
``pop_all()`` in the ``run_forever()`` polling cycle, regardless of which
platform they originated on.
"""
from __future__ import annotations

import asyncio
import logging

from .base import MessageEnvelope

logger = logging.getLogger(__name__)

_DEBOUNCE_WINDOW_S = 5.0  # merge messages within this window


class InboxQueue:
    """Async queue bridging gateway user messages and the agent loop.

    Thread-safe: ``push`` can be called from any gateway coroutine while
    ``pop_all`` is called inside the agent's ``run_forever`` loop.
    """

    def __init__(self, maxsize: int = 50) -> None:
        self._queue: asyncio.Queue[MessageEnvelope] = asyncio.Queue(maxsize=maxsize)

    # ------------------------------------------------------------------
    # Producer side (called by any gateway)
    # ------------------------------------------------------------------

    async def push(self, envelope: MessageEnvelope) -> None:
        """Enqueue a user message.  Drops oldest if full."""
        if self._queue.full():
            try:
                self._queue.get_nowait()  # discard oldest
            except asyncio.QueueEmpty:
                pass
        await self._queue.put(envelope)
        logger.info(
            "InboxQueue: queued %s message from %s (chat=%s): %s",
            envelope.channel.value,
            envelope.sender_id,
            envelope.chat_id,
            envelope.content[:80],
        )

    # ------------------------------------------------------------------
    # Consumer side (called by the agent)
    # ------------------------------------------------------------------

    def has_messages(self) -> bool:
        """Non-blocking check."""
        return not self._queue.empty()

    async def pop_all(self) -> list[MessageEnvelope]:
        """Drain all pending messages (non-blocking), debouncing rapid bursts."""
        raw: list[MessageEnvelope] = []
        while True:
            try:
                msg = self._queue.get_nowait()
                raw.append(msg)
            except asyncio.QueueEmpty:
                break
        if not raw:
            return []
        merged = _debounce(raw)
        logger.info(
            "InboxQueue: drained %d raw message(s) → %d after debounce",
            len(raw), len(merged),
        )
        return merged

    async def wait_and_pop_all(self) -> list[MessageEnvelope]:
        """Block until at least one message arrives, then drain all pending."""
        first = await self._queue.get()  # blocks
        raw: list[MessageEnvelope] = [first]
        # Short grace period to catch rapid follow-up messages
        await asyncio.sleep(0.3)
        while True:
            try:
                raw.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        merged = _debounce(raw)
        logger.info(
            "InboxQueue: received %d message(s) → %d after debounce",
            len(raw), len(merged),
        )
        return merged


def _debounce(messages: list[MessageEnvelope]) -> list[MessageEnvelope]:
    """Merge messages from the same sender that arrived within ``_DEBOUNCE_WINDOW_S``.

    Only messages from the same ``(channel, sender_id)`` pair are considered
    for merging, so a rapid-fire Telegram burst and a simultaneous Feishu
    message from a user with the same ID will never collapse into one.
    """
    if len(messages) <= 1:
        return messages

    groups: list[list[MessageEnvelope]] = []
    current_group: list[MessageEnvelope] = [messages[0]]

    for msg in messages[1:]:
        prev = current_group[-1]
        same_sender = (msg.channel == prev.channel and msg.sender_id == prev.sender_id)
        within_window = (msg.timestamp.timestamp() - prev.timestamp.timestamp()) < _DEBOUNCE_WINDOW_S

        if same_sender and within_window:
            current_group.append(msg)
        else:
            groups.append(current_group)
            current_group = [msg]
    groups.append(current_group)

    # Build merged envelopes
    result: list[MessageEnvelope] = []
    for group in groups:
        if len(group) == 1:
            result.append(group[0])
        else:
            # Merge content with newlines
            combined_text = "\n".join(m.content for m in group)
            merged = MessageEnvelope(
                message_id=group[0].message_id,
                channel=group[0].channel,
                sender_id=group[0].sender_id,
                content=combined_text,
                timestamp=group[0].timestamp,
                chat_id=group[0].chat_id,
                telegram_message_id=group[0].telegram_message_id,
                conversation_id=group[0].conversation_id,
                platform_message_id=group[0].platform_message_id,
                metadata=group[0].metadata,
            )
            result.append(merged)
    return result
