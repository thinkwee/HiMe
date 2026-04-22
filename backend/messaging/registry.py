"""``GatewayRegistry`` — shared lookup container for messaging gateways.

Tools that need to send messages (``reply_user``, ``push_report``) consult
the registry to pick the right gateway based on the inbound envelope's
channel, avoiding any hard dependency on a specific backend package and
preventing circular imports.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable

from .base import BaseGateway, MessageChannel, MessageEnvelope

logger = logging.getLogger(__name__)


class GatewayRegistry:
    """In-memory map of ``MessageChannel`` → :class:`BaseGateway`.

    A registry instance is normally created once during application startup,
    populated with whatever gateways are enabled (Telegram, Feishu, ...), then
    passed into the tool registry so outbound tools can dispatch messages
    correctly.
    """

    def __init__(self) -> None:
        self._gateways: dict[MessageChannel, BaseGateway] = {}

    # ------------------------------------------------------------------
    # Mutators
    # ------------------------------------------------------------------

    def register(self, gateway: BaseGateway) -> None:
        """Register a gateway under its declared channel."""
        channel = getattr(gateway, "channel", None)
        if channel is None:
            raise ValueError(
                f"Gateway {type(gateway).__name__} has no 'channel' attribute"
            )
        if channel in self._gateways:
            logger.warning(
                "GatewayRegistry: replacing existing gateway for channel %s",
                channel,
            )
        self._gateways[channel] = gateway
        logger.info("GatewayRegistry: registered %s gateway", channel.value)

    def unregister(self, channel: MessageChannel) -> None:
        self._gateways.pop(channel, None)

    def clear(self) -> None:
        self._gateways.clear()

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get(self, channel: MessageChannel) -> BaseGateway | None:
        return self._gateways.get(channel)

    def all(self) -> list[BaseGateway]:
        return list(self._gateways.values())

    def channels(self) -> Iterable[MessageChannel]:
        return self._gateways.keys()

    def for_envelope(self, envelope: MessageEnvelope) -> BaseGateway | None:
        """Return the gateway that should handle replies for ``envelope``.

        Falls back to the first registered gateway when the envelope's channel
        isn't registered (useful for tests/benchmarks that only wire up one
        gateway but reuse generic envelopes).
        """
        gw = self._gateways.get(envelope.channel)
        if gw is not None:
            return gw
        if self._gateways:
            return next(iter(self._gateways.values()))
        return None

    # ------------------------------------------------------------------
    # Introspection helpers
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._gateways)

    def __contains__(self, channel: MessageChannel) -> bool:
        return channel in self._gateways
