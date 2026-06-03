"""Tests for the iOS presence registry that drives the WS-vs-APNs delivery
decision (online => deliver live over the WebSocket; offline => APNs push)."""
from __future__ import annotations

from backend.ios_gateway import IOSConnectionRegistry


async def test_presence_registry():
    reg = IOSConnectionRegistry()
    assert reg.is_online("u") is False
    await reg.register("u", "c1")
    assert reg.is_online("u") is True
    # second device for the same user
    await reg.register("u", "c2")
    assert reg.is_online("u") is True
    await reg.unregister("u", "c1")
    assert reg.is_online("u") is True  # still has c2
    await reg.unregister("u", "c2")
    assert reg.is_online("u") is False


async def test_touch_keeps_alive():
    reg = IOSConnectionRegistry()
    await reg.register("u", "c1")
    reg.touch("u", "c1")  # best-effort, must not raise
    assert reg.is_online("u") is True
    assert "u" in reg.online_users()
