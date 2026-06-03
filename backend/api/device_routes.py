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

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..ios_gateway import device_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/devices", tags=["devices"])

_USER_ID = "LiveUser"


class DeviceTokenRequest(BaseModel):
    device_token: str
    bundle_id: str | None = None
    environment: str = "production"


@router.post("/register")
async def register_device(body: DeviceTokenRequest):
    """Register this device's APNs token for proactive push."""
    token = (body.device_token or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="Missing device_token")
    await asyncio.to_thread(
        device_store.upsert_device_token, _USER_ID, token, body.bundle_id, body.environment,
    )
    return {"success": True}


@router.post("/unregister")
async def unregister_device(body: DeviceTokenRequest):
    """Revoke this device's APNs token (e.g. on logout)."""
    token = (body.device_token or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="Missing device_token")
    await asyncio.to_thread(device_store.revoke_device_token, token)
    return {"success": True}
