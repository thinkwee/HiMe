"""Feishu (Lark) gateway package.

Implements a :class:`backend.messaging.base.BaseGateway` on top of the
``lark-oapi`` SDK. Both transports (long-lived WebSocket and HTTP webhook)
are supported; the choice is driven by ``settings.FEISHU_TRANSPORT``.

This package is optional — the ``lark_oapi`` dependency is imported lazily
inside the sender/transport so the module is importable even when the SDK
is not installed (e.g. in CI / lightweight deployments).
"""
from __future__ import annotations

from .cards import build_back_card, build_evidence_card, build_plain_card
from .models import MessageChannel, MessageEnvelope
from .sender import FeishuSender

__all__ = [
    "FeishuSender",
    "MessageChannel",
    "MessageEnvelope",
    "build_evidence_card",
    "build_back_card",
    "build_plain_card",
]


def __getattr__(name: str):  # PEP 562 — lazy import of the gateway
    if name == "FeishuGateway":
        from .gateway import FeishuGateway as _G
        return _G
    raise AttributeError(name)
