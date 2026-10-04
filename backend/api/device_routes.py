"""Device registration endpoints for APNs proactive push (iOS).

The iOS app registers its APNs device token here after the user grants
notification permission; the token is stored (see
:mod:`backend.ios_gateway.device_store`) and used by
:class:`~backend.ios_gateway.apns.APNSSender` to reach a closed app.

Single-user build: the owner is always ``LiveUser``. The endpoints are
covered by the optional global bearer auth (``API_AUTH_TOKEN``) like the rest
of ``/api/*``.
"""
from __future__ import annotations

import asyncio
import logging
import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..ios_gateway import device_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/devices", tags=["devices"])

_USER_ID = "LiveUser"


# APNs device tokens are hex strings (32 bytes today; Apple reserves room to grow).
_TOKEN_RE = re.compile(r"^[0-9a-fA-F]{32,200}$")
_ENVIRONMENTS = frozenset({"sandbox", "production"})


class DeviceTokenRequest(BaseModel):
    device_token: str
    bundle_id: str | None = Field(default=None, max_length=255)
    environment: str = "production"


def _clean_token(raw: str) -> str:
    token = (raw or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="Missing device_token")
    if not _TOKEN_RE.match(token):
        raise HTTPException(status_code=400, detail="device_token must be a hex string")
    return token


@router.post("/register")
async def register_device(body: DeviceTokenRequest):
    """Register this device's APNs token for proactive push."""
    token = _clean_token(body.device_token)
    environment = (body.environment or "").strip().lower()
    if environment not in _ENVIRONMENTS:
        raise HTTPException(
            status_code=400, detail="environment must be 'sandbox' or 'production'"
        )
    await asyncio.to_thread(
        device_store.upsert_device_token, _USER_ID, token, body.bundle_id, environment,
    )
    return {"success": True}


@router.post("/unregister")
async def unregister_device(body: DeviceTokenRequest):
    """Revoke this device's APNs token (e.g. on logout)."""
    token = _clean_token(body.device_token)
    await asyncio.to_thread(device_store.revoke_device_token, token)
    return {"success": True}
