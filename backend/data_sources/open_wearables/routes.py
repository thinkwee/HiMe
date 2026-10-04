"""Frontend-facing endpoints for the open-wearables integration (Devices page).

Mounted at ``/api/integrations/openwearables`` — covered by the normal
``BearerAuthMiddleware`` like every other ``/api/*`` route, no special
casing needed there (unlike the webhook route, which authenticates inbound
provider events with its own Svix signature instead of the API bearer
token).

Every handler checks ``OPENWEARABLES_ENABLED`` itself and returns a clear
503/disabled response rather than attempting any network call when the
feature is off.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from ...config import settings
from .client import OpenWearablesClient

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/integrations/openwearables", tags=["open-wearables"])

# Providers that require a public HTTPS callback URL (webhook-only ingestion,
# no REST pull) — surfaced to the frontend so it can warn the user before
# they try to connect one on a purely-local deployment.
_REQUIRES_PUBLIC_HTTPS = frozenset({"garmin"})


def _disabled_response() -> dict:
    return {
        "enabled": False,
        "reachable": False,
        "ow_user_id": None,
        "connections": [],
        "cursors": {},
        "last_poll_at": None,
        "last_poll_status": None,
        "webhook_registered": False,
    }


@router.get("/status")
async def get_status():
    """Integration health snapshot for the Devices page."""
    if not settings.OPENWEARABLES_ENABLED:
        return _disabled_response()

    from ...api.config_routes import get_app_state
    state = get_app_state().get("openwearables") or {}

    client = OpenWearablesClient()
    reachable = await client.health()

    connections: list[dict] = []
    ow_user_id = state.get("ow_user_id")
    if reachable and ow_user_id:
        connections = await client.list_connections(ow_user_id)

    return {
        "enabled": True,
        "reachable": reachable,
        "ow_user_id": ow_user_id,
        "connections": connections,
        "cursors": state.get("cursors", {}),
        "last_poll_at": state.get("last_poll_at"),
        "last_poll_status": state.get("last_poll_status"),
        "last_poll_error": state.get("last_poll_error"),
        "webhook_registered": bool((state.get("webhook") or {}).get("endpoint_id")),
        "webhook_url": (state.get("webhook") or {}).get("url"),
    }


@router.get("/providers")
async def get_providers():
    """List providers open-wearables supports, annotated for the frontend."""
    if not settings.OPENWEARABLES_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="open-wearables integration is disabled (set OPENWEARABLES_ENABLED=true)",
        )
    client = OpenWearablesClient()
    providers = await client.get_providers()
    for p in providers:
        # open-wearables' provider objects key the slug as "provider" (e.g.
        # "garmin"), not "id" — that field doesn't exist on the payload, so
        # this used to silently fall through to "name" (e.g. "Garmin",
        # which happens to lowercase to the same slug — coincidence, not a
        # guarantee for multi-word provider names).
        slug = str(p.get("provider") or p.get("id") or p.get("name") or "").lower()
        p["requires_public_https"] = slug in _REQUIRES_PUBLIC_HTTPS
    return {"providers": providers}


@router.post("/connect/{provider}")
async def connect_provider(provider: str, redirect_uri: str | None = Query(None)):
    """Return the OAuth authorization URL for ``provider`` so the frontend can redirect there."""
    if not settings.OPENWEARABLES_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="open-wearables integration is disabled (set OPENWEARABLES_ENABLED=true)",
        )
    from ...api.config_routes import get_app_state
    state = get_app_state().get("openwearables") or {}
    user_id = state.get("ow_user_id")
    if not user_id:
        raise HTTPException(
            status_code=409,
            detail="open-wearables user not initialised yet — the poller creates it shortly after startup, retry in a few seconds",
        )

    client = OpenWearablesClient()
    result = await client.get_authorization_url(provider, user_id, redirect_uri)
    if not result:
        raise HTTPException(status_code=502, detail="failed to obtain an authorization URL from open-wearables")
    return result


@router.post("/sync")
async def trigger_sync(provider: str | None = Query(None)):
    """Trigger an open-wearables-side provider sync, then immediately run one
    local poll pass so newly-synced data shows up in HiMe without waiting
    for the next scheduled interval."""
    if not settings.OPENWEARABLES_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="open-wearables integration is disabled (set OPENWEARABLES_ENABLED=true)",
        )
    from ...api.config_routes import get_app_state
    state = get_app_state().get("openwearables") or {}
    user_id = state.get("ow_user_id")
    if not user_id:
        raise HTTPException(status_code=409, detail="open-wearables user not initialised yet")

    client = OpenWearablesClient()
    sync_result = None
    if provider:
        sync_result = await client.trigger_sync(provider, user_id)

    from ...agent.data_store import DataStore
    from .poller import poll_once

    data_store = DataStore(db_path=settings.DATA_STORE_PATH, user_id="LiveUser")
    poll_result = await poll_once(client, user_id, data_store)

    return {
        "success": True,
        "provider_sync": sync_result,
        "poll": poll_result,
    }
