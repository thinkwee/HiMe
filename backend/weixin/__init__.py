"""WeChat (Weixin ClawBot / iLink) gateway package.

Implements a :class:`backend.messaging.base.BaseGateway` on top of the
public iLink bot API at ``https://ilinkai.weixin.qq.com``. The transport is
HTTP long-poll on ``/ilink/bot/getupdates`` plus ``/ilink/bot/sendmessage``
for outbound replies. A persistent ``bot_token`` is obtained out-of-band by
running ``python -m backend.weixin.qr_login`` and scanning the QR with the
WeChat ClawBot plugin (Settings → Plugins → ClawBot).

The gateway only consumes a previously-issued token from disk; the QR flow
is intentionally separated so backend startup remains non-interactive.
"""
from __future__ import annotations

from .models import MessageChannel, MessageEnvelope
from .sender import WeixinSender

__all__ = ["WeixinSender", "MessageChannel", "MessageEnvelope"]


def __getattr__(name: str):  # PEP 562 — lazy import of the gateway
    if name == "WeixinGateway":
        from .gateway import WeixinGateway as _G
        return _G
    raise AttributeError(name)
