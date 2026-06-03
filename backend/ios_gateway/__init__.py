"""In-app iOS messaging channel.

A native alternative to the external IM gateways (Telegram / Feishu / WeChat):
the iOS app talks to the agent directly. Inbound chat arrives via
``POST /api/agent/chat``; outbound replies are emitted onto the agent's event
stream and consumed over the existing ``/api/stream/agent`` WebSocket;
proactive pushes reach a closed app via APNs.

Single instance bound to the (single) user, registered into the shared
``GatewayRegistry`` in ``main.py`` alongside the IM gateways.
"""
from __future__ import annotations

from .apns import APNSSender
from .connections import IOSConnectionRegistry, ios_connections
from .gateway import IOSGateway

__all__ = [
    "IOSGateway",
    "IOSConnectionRegistry",
    "ios_connections",
    "APNSSender",
]
