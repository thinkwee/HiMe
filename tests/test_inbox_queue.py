from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from backend.messaging.base import MessageChannel, MessageEnvelope
from backend.messaging.inbox import InboxQueue


def _msg(
    message_id: str,
    content: str,
    *,
    sender_id: str = "user-1",
    channel: MessageChannel = MessageChannel.TELEGRAM,
    seconds: int = 0,
) -> MessageEnvelope:
    base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    return MessageEnvelope(
        message_id=message_id,
        channel=channel,
        sender_id=sender_id,
        content=content,
        timestamp=base + timedelta(seconds=seconds),
        chat_id="chat-1",
    )


@pytest.mark.asyncio
async def test_push_and_pop_all_roundtrip() -> None:
    queue = InboxQueue()
    assert not queue.has_messages()

    await queue.push(_msg("1", "hello"))
    assert queue.has_messages()

    drained = await queue.pop_all()
    assert len(drained) == 1
    assert drained[0].content == "hello"
    assert not queue.has_messages()


@pytest.mark.asyncio
async def test_push_drops_oldest_when_full() -> None:
    queue = InboxQueue(maxsize=2)

    await queue.push(_msg("1", "first", sender_id="a"))
    await queue.push(_msg("2", "second", sender_id="b"))
    await queue.push(_msg("3", "third", sender_id="c"))

    drained = await queue.pop_all()
    assert [m.message_id for m in drained] == ["2", "3"]


@pytest.mark.asyncio
async def test_pop_all_debounces_burst_from_same_sender() -> None:
    queue = InboxQueue()

    await queue.push(_msg("1", "part-1", seconds=0))
    await queue.push(_msg("2", "part-2", seconds=3))
    await queue.push(_msg("3", "separate", seconds=9))

    drained = await queue.pop_all()
    assert len(drained) == 2
    assert drained[0].content == "part-1\npart-2"
    assert drained[1].content == "separate"


@pytest.mark.asyncio
async def test_pop_all_does_not_merge_across_sender_or_channel() -> None:
    queue = InboxQueue()

    await queue.push(_msg("1", "telegram-a", sender_id="same", channel=MessageChannel.TELEGRAM, seconds=0))
    await queue.push(_msg("2", "feishu-a", sender_id="same", channel=MessageChannel.FEISHU, seconds=1))
    await queue.push(_msg("3", "telegram-b", sender_id="other", channel=MessageChannel.TELEGRAM, seconds=2))

    drained = await queue.pop_all()
    assert [m.message_id for m in drained] == ["1", "2", "3"]


@pytest.mark.asyncio
async def test_wait_and_pop_all_drains_followup_messages() -> None:
    queue = InboxQueue()
    await queue.push(_msg("1", "first", sender_id="a"))
    await queue.push(_msg("2", "second", sender_id="b"))

    with patch("backend.messaging.inbox.asyncio.sleep", new_callable=AsyncMock) as sleep_mock:
        drained = await queue.wait_and_pop_all()

    sleep_mock.assert_awaited_once_with(0.3)
    assert [m.message_id for m in drained] == ["1", "2"]


@pytest.mark.asyncio
async def test_pop_all_handles_empty_and_single_message() -> None:
    queue = InboxQueue()
    assert await queue.pop_all() == []

    msg = _msg("1", "only")
    await queue.push(msg)
    drained = await queue.pop_all()
    assert len(drained) == 1
    assert drained[0] == msg
