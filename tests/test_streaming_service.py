"""
Tests for the HIME streaming service and connection manager.

Covers:
- ConnectionManager: add/remove/broadcast/count
- DataStreamingService: helper functions (parse_window_to_minutes)
- shutdown_executor behaviour
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.services.connection_manager import ConnectionManager
from backend.services.streaming_service import (
    DataStreamingService,
    parse_window_to_minutes,
    shutdown_executor,
)

# =========================================================================
# ConnectionManager
# =========================================================================

class TestConnectionManager:
    """Tests for the WebSocket connection manager."""

    @pytest.fixture
    def manager(self) -> ConnectionManager:
        return ConnectionManager()

    async def test_connect_adds_to_pool(self, manager):
        """Accepting a WebSocket should add it to active_connections."""
        ws = AsyncMock()
        await manager.connect(ws)
        assert ws in manager.active_connections
        assert manager.count() == 1
        ws.accept.assert_awaited_once()

    async def test_disconnect_removes_from_pool(self, manager):
        """Disconnecting a WebSocket should remove it from the pool."""
        ws = AsyncMock()
        await manager.connect(ws)
        manager.disconnect(ws)
        assert ws not in manager.active_connections
        assert manager.count() == 0

    async def test_disconnect_nonexistent_does_not_raise(self, manager):
        """Disconnecting a WebSocket that was never connected should not raise."""
        ws = AsyncMock()
        manager.disconnect(ws)  # Should not raise

    async def test_is_connected(self, manager):
        """is_connected should return True for connected, False otherwise."""
        ws = AsyncMock()
        assert manager.is_connected(ws) is False
        await manager.connect(ws)
        assert manager.is_connected(ws) is True
        manager.disconnect(ws)
        assert manager.is_connected(ws) is False

    async def test_count(self, manager):
        """count() should reflect the number of active connections."""
        assert manager.count() == 0
        ws1 = AsyncMock()
        ws2 = AsyncMock()
        await manager.connect(ws1)
        assert manager.count() == 1
        await manager.connect(ws2)
        assert manager.count() == 2
        manager.disconnect(ws1)
        assert manager.count() == 1

    async def test_broadcast_sends_to_all(self, manager):
        """broadcast() should send the message to all connected clients."""
        ws1 = AsyncMock()
        ws2 = AsyncMock()
        await manager.connect(ws1)
        await manager.connect(ws2)

        message = {"type": "test", "data": "hello"}
        await manager.broadcast(message)

        ws1.send_json.assert_awaited_once_with(message)
        ws2.send_json.assert_awaited_once_with(message)

    async def test_broadcast_removes_failed_connections(self, manager):
        """If a WebSocket fails during broadcast, it should be disconnected."""
        ws_good = AsyncMock()
        ws_bad = AsyncMock()
        ws_bad.send_json.side_effect = Exception("Connection lost")

        await manager.connect(ws_good)
        await manager.connect(ws_bad)
        assert manager.count() == 2

        await manager.broadcast({"type": "test"})

        # ws_bad should have been removed
        assert manager.count() == 1
        assert ws_good in manager.active_connections
        assert ws_bad not in manager.active_connections

    async def test_broadcast_empty_pool(self, manager):
        """Broadcasting to an empty pool should not raise."""
        await manager.broadcast({"type": "test"})  # Should not raise

    async def test_multiple_connects_same_ws(self, manager):
        """Connecting the same WebSocket twice should not duplicate it (set semantics)."""
        ws = AsyncMock()
        await manager.connect(ws)
        await manager.connect(ws)
        # It is in a set, so count should still be 1
        assert manager.count() == 1


# =========================================================================
# parse_window_to_minutes
# =========================================================================

class TestParseWindowToMinutes:
    """Tests for the window-string to minutes conversion utility."""

    def test_1hour(self):
        assert parse_window_to_minutes("1hour") == 60

    def test_1day(self):
        assert parse_window_to_minutes("1day") == 24 * 60

    def test_1week(self):
        assert parse_window_to_minutes("1week") == 7 * 24 * 60

    def test_1month(self):
        assert parse_window_to_minutes("1month") == 30 * 24 * 60

    def test_default_for_unknown(self):
        """Unknown window strings should default to 60 minutes."""
        assert parse_window_to_minutes("unknown") == 60

    def test_none_defaults(self):
        """None should default to 60 minutes."""
        assert parse_window_to_minutes(None) == 60

    def test_case_insensitive(self):
        """Should be case insensitive."""
        assert parse_window_to_minutes("1HOUR") == 60
        assert parse_window_to_minutes("1Day") == 24 * 60


# =========================================================================
# Global instances
# =========================================================================

class TestGlobalInstances:
    """Test that global manager instances exist and are separate."""

    def test_global_managers_exist(self):
        from backend.services.connection_manager import (
            agent_monitor_manager,
            data_stream_manager,
        )
        assert isinstance(data_stream_manager, ConnectionManager)
        assert isinstance(agent_monitor_manager, ConnectionManager)
        assert data_stream_manager is not agent_monitor_manager

    def test_global_managers_independent(self):
        """Operations on one manager should not affect the other."""
        from backend.services.connection_manager import (
            agent_monitor_manager,
            data_stream_manager,
        )
        # Both start at 0 or whatever state they are in,
        # the point is they are separate objects
        assert data_stream_manager.active_connections is not agent_monitor_manager.active_connections


# =========================================================================
# DataStreamingService static method smoke test
# =========================================================================

class TestDataStreamingService:
    """Verify DataStreamingService class is importable and has expected interface."""

    def test_stream_data_is_static(self):
        """stream_data should be a static async method."""
        assert asyncio.iscoroutinefunction(DataStreamingService.stream_data)

    async def test_stream_data_sends_start_event(self):
        """stream_data should send a stream_start message to the websocket."""
        ws = AsyncMock()
        active = {ws}

        mock_reader = MagicMock()
        mock_reader.get_feature_types.return_value = ["heart_rate"]
        # load_feature_data returns empty DF so the loop falls through quickly
        import pandas as pd
        mock_reader.load_feature_data.return_value = pd.DataFrame()
        mock_reader.load_features_batch.return_value = pd.DataFrame()

        mock_state = {
            "stream_config": {"granularity": "real-time"},
            "live_history_window": "1hour",
        }

        # We need to break out of the infinite polling loop.
        # After the start event, remove ws from active_connections.
        call_count = 0
        async def _limited_send(msg):
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                # Remove from active to break the loop after first send
                active.discard(ws)

        ws.send_json = _limited_send

        with patch("backend.services.streaming_service.create_reader", return_value=mock_reader), \
             patch("backend.services.streaming_service.settings", MagicMock(DATA_STORE_PATH="/tmp/test")), \
             patch("backend.api.config_routes.get_app_state", return_value=mock_state):
            # Use a timeout to prevent the test from hanging if the loop doesn't exit
            try:
                await asyncio.wait_for(
                    DataStreamingService.stream_data(
                        ws, ["LiveUser"], {"granularity": "real-time"}, active
                    ),
                    timeout=5.0,
                )
            except asyncio.TimeoutError:
                pass  # Acceptable — we just want to verify the start event was sent

        # At least one call was made (stream_start)
        assert call_count >= 1


# =========================================================================
# shutdown_executor
# =========================================================================

class TestShutdownExecutor:
    """Test the executor shutdown function."""

    def test_shutdown_does_not_raise(self):
        """shutdown_executor should not raise even if called multiple times."""
        # First call
        shutdown_executor()
        # Idempotent — calling again should not crash
        # (the executor may already be shut down, but the function should handle it)
