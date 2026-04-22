"""
WebSocket connection manager.
Handles active connections and broadcasting.
"""
import logging

from fastapi import WebSocket

logger = logging.getLogger(__name__)

class ConnectionManager:
    """Manages WebSocket connections."""

    def __init__(self):
        self.active_connections: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket):
        """Accept connection and add to pool."""
        await websocket.accept()
        self.active_connections.add(websocket)
        logger.debug(f"WebSocket connected. Total: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        """Remove connection from pool."""
        self.active_connections.discard(websocket)
        logger.debug(f"WebSocket disconnected. Total: {len(self.active_connections)}")

    async def broadcast(self, message: dict):
        """Broadcast message to all connected clients."""
        # iterate over a copy to avoid runtime error if set changes size
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.warning(f"Failed to broadcast to {connection}: {e}")
                self.disconnect(connection)

    def is_connected(self, websocket: WebSocket) -> bool:
        """Check if a websocket is currently connected."""
        return websocket in self.active_connections

    def count(self) -> int:
        """Return number of active connections."""
        return len(self.active_connections)

# Global instances
data_stream_manager = ConnectionManager()
agent_monitor_manager = ConnectionManager()
