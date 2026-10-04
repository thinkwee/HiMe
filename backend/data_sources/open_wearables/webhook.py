"""Inbound webhook receiver for open-wearables push events (Svix-signed).

Mounted unconditionally (like the Feishu webhook — see
``backend/main.py::BearerAuthMiddleware``) so the bearer-auth exemption and
route registration don't depend on ``OPENWEARABLES_ENABLED`` at import time.
The handler itself no-ops (404) when the feature is disabled, so there is
still zero behavior/network impact while off.

Signature verification is implemented by hand against Svix's documented
algorithm (https://docs.svix.com/receiving/verifying-payloads/how-manual) —
deliberately not the ``svix`` pip package, to avoid a new dependency for a
handful of HMAC lines:

    secret  = base64_decode(strip_prefix(whsec_secret, "whsec_"))
    signed  = f"{svix-id}.{svix-timestamp}.{raw_body}"
    expected = base64_encode(hmac_sha256(secret, signed))
    header  = "v1,<sig1> v1,<sig2> ..."   (accept any matching version)

Requests older than 5 minutes are rejected outright (replay protection) even
before the signature is checked.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ...config import settings

logger = logging.getLogger(__name__)

router = APIRouter()

WEBHOOK_PATH = "/api/integrations/openwearables/webhook"

_MAX_TIMESTAMP_SKEW_S = 5 * 60

# Event types that are NOT timeseries batches — handled by their own mapper,
# or intentionally ignored (lifecycle/connection events HiMe doesn't act on).
_WORKOUT_EVENT = "workout.created"
_SLEEP_EVENT = "sleep.created"
_IGNORED_EVENTS = frozenset({
    "menstrual_cycle.created",
    "connection.created",
    "connection.revoked",
    "sync.started",
    "sync.completed",
    "sync.failed",
})

_data_store = None  # lazy singleton — see _get_data_store()


def _get_data_store():
    global _data_store
    if _data_store is None:
        from ...agent.data_store import DataStore
        _data_store = DataStore(db_path=settings.DATA_STORE_PATH, user_id="LiveUser")
    return _data_store


# ---------------------------------------------------------------------------
# Signature verification (pure — no I/O, easy to unit test)
# ---------------------------------------------------------------------------

def verify_svix_signature(secret: str, msg_id: str, timestamp: str, raw_body: bytes, signature_header: str) -> bool:
    """Verify a Svix ``svix-signature`` header. Returns False on any malformed input."""
    if not secret or not msg_id or not timestamp or not signature_header:
        return False
    try:
        sent_at = int(timestamp)
    except (TypeError, ValueError):
        return False
    if abs(time.time() - sent_at) > _MAX_TIMESTAMP_SKEW_S:
        return False
    try:
        key = base64.b64decode(secret.removeprefix("whsec_"))
    except Exception:
        return False
    signed_content = f"{msg_id}.{timestamp}.".encode() + raw_body
    expected = base64.b64encode(hmac.new(key, signed_content, hashlib.sha256).digest()).decode("utf-8")
    for part in signature_header.split():
        if "," not in part:
            continue
        version, sig = part.split(",", 1)
        if version != "v1":
            continue
        if hmac.compare_digest(sig, expected):
            return True
    return False


def _get_configured_secret() -> str | None:
    from ...api.config_routes import get_app_state
    state = get_app_state().get("openwearables") or {}
    return (state.get("webhook") or {}).get("secret")


# ---------------------------------------------------------------------------
# Event dispatch
# ---------------------------------------------------------------------------

async def _handle_event(payload: dict) -> None:
    from . import mapper
    from .features import provider_allowlist_from_settings

    event_type = str(payload.get("type") or "")
    data = payload.get("data") or {}
    allowlist = provider_allowlist_from_settings()

    if event_type == _WORKOUT_EVENT:
        rows = mapper.map_workout(data, allowlist)
    elif event_type == _SLEEP_EVENT:
        rows = mapper.map_sleep_session(data, allowlist)
    elif event_type in _IGNORED_EVENTS:
        logger.debug("open-wearables webhook: ignoring event type %s", event_type)
        return
    elif event_type.endswith(".created"):
        # Group events (heart_rate.created) and granular events
        # (series.heart_rate.created) are both timeseries-batch shaped:
        # {user_id, provider, series_type, samples: [...]}.
        rows = mapper.map_webhook_timeseries_event(data, allowlist)
    else:
        logger.debug("open-wearables webhook: unhandled event type %r", event_type)
        return

    if not rows:
        return
    data_store = _get_data_store()
    await asyncio.to_thread(data_store.ingest_batch, {"data": rows})
    logger.info("open-wearables webhook: ingested %d sample(s) for event %s", len(rows), event_type)


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

@router.post(WEBHOOK_PATH)
async def openwearables_webhook(request: Request):
    """Receive a Svix-signed open-wearables event. Always answers fast and
    never 500s on a bad payload — malformed bodies are logged and 200'd so
    the sender doesn't retry-storm us; only auth failures return non-2xx."""
    if not settings.OPENWEARABLES_ENABLED:
        return JSONResponse({"detail": "not found"}, status_code=404)

    raw = await request.body()
    svix_id = request.headers.get("svix-id", "")
    svix_timestamp = request.headers.get("svix-timestamp", "")
    svix_signature = request.headers.get("svix-signature", "")

    secret = _get_configured_secret()
    if not secret:
        logger.info("open-wearables webhook: received event but no signing secret is configured — rejecting")
        return JSONResponse({"detail": "webhook not configured"}, status_code=401)

    if not verify_svix_signature(secret, svix_id, svix_timestamp, raw, svix_signature):
        logger.warning("open-wearables webhook: signature verification failed (id=%s)", svix_id)
        return JSONResponse({"detail": "invalid signature"}, status_code=401)

    try:
        payload = json.loads(raw.decode("utf-8") or "{}")
    except Exception as exc:
        logger.warning("open-wearables webhook: bad JSON payload: %s", exc)
        return {"ok": True}

    try:
        await _handle_event(payload)
    except Exception as exc:  # never let a payload we can't handle 500
        logger.error("open-wearables webhook: handler error: %s", exc, exc_info=True)

    return {"ok": True}
