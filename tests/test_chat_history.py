"""Tests for server-side chat-history persistence (memory DB).

Exercises the same MemoryManager code path the agent uses to store the in-app
conversation transcript so the iOS app can reload scrollback.
"""
from __future__ import annotations

import pytest

from backend.agent.memory_manager import MemoryManager


@pytest.fixture
def mm(tmp_path):
    return MemoryManager(tmp_path, "hist-user")


async def test_persist_and_fetch_oldest_first(mm):
    await mm.persist_chat_turn("ios:hist-user", "user", "hello", client_msg_id="c1")
    await mm.persist_chat_turn("ios:hist-user", "assistant", "hi there", message_hash="abc1234567")
    rows = mm.get_chat_history("ios:hist-user")
    assert [r["role"] for r in rows] == ["user", "assistant"]
    assert rows[0]["content"] == "hello"
    assert rows[0]["client_msg_id"] == "c1"
    assert rows[1]["message_hash"] == "abc1234567"


async def test_history_key_isolation(mm):
    await mm.persist_chat_turn("ios:hist-user", "user", "a")
    await mm.persist_chat_turn("telegram:9", "user", "b")
    assert len(mm.get_chat_history("ios:hist-user")) == 1
    assert len(mm.get_chat_history("telegram:9")) == 1


async def test_survives_new_instance(tmp_path):
    m1 = MemoryManager(tmp_path, "hu2")
    await m1.persist_chat_turn("ios:hu2", "user", "persisted")
    m2 = MemoryManager(tmp_path, "hu2")  # simulated restart
    assert len(m2.get_chat_history("ios:hu2")) == 1


async def test_clear_scoped_to_key(mm):
    await mm.persist_chat_turn("ios:hist-user", "user", "x")
    await mm.persist_chat_turn("telegram:9", "user", "keep")
    removed = await mm.clear_chat_history("ios:hist-user")
    assert removed == 1
    assert mm.get_chat_history("ios:hist-user") == []
    assert len(mm.get_chat_history("telegram:9")) == 1


async def test_report_id_round_trips(mm):
    await mm.persist_chat_turn(
        "ios:hist-user", "assistant", "report digest",
        message_hash="h", report_id=42,
    )
    rows = mm.get_chat_history("ios:hist-user")
    assert rows[0]["report_id"] == 42


async def test_image_id_persisted_and_replayed(mm):
    # An agent-sent chart turn carries an image_id; it must survive a reload
    # so GET /chat-history can replay the image (the live chat_image WS event
    # is lost if the app is offline at emit time). The chart itself rides in
    # the text as a Markdown image line, which the app's MarkdownView renders.
    img_md = "![chart](/api/agent/chat-image/deadbeefcafe)"
    await mm.persist_chat_turn(
        "ios:hist-user", "assistant", f"weekly trend\n\n{img_md}",
        message_hash="hash123456", image_id="deadbeefcafe",
    )
    rows = mm.get_chat_history("ios:hist-user")
    assert rows[0]["image_id"] == "deadbeefcafe"
    assert img_md in rows[0]["content"]


def test_onboarding_survey_lifecycle(mm):
    mm.save_onboarding_survey(["sleep", "fitness"], {"focus": "sleep"})
    pending = mm.get_pending_survey()
    assert pending is not None
    assert pending["goals"] == ["sleep", "fitness"]
    assert pending["answers"] == {"focus": "sleep"}
    mm.mark_survey_planned(pending["id"])
    assert mm.get_pending_survey() is None
    # A new survey supersedes the previous pending one — only one stays pending.
    mm.save_onboarding_survey(["stress"])
    mm.save_onboarding_survey(["recovery"])
    pending2 = mm.get_pending_survey()
    assert pending2["goals"] == ["recovery"]
