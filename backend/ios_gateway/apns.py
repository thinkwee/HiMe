"""APNs sender for proactive push to a closed iOS app.

Token-based auth (JWT / ES256) using the operator's ``.p8`` key — no
per-message certificates. Built once in the lifespan from settings; a no-op
when ``APNS_ENABLED`` is false or the key isn't configured, so the backend
runs fine without push set up. ``aioapns`` is imported lazily so it is only
required when APNs is actually enabled.

Device tokens live in ``ios_gateway.device_store`` (a small plain-SQLite
file); on a 410 *Unregistered* response the token is revoked so dead installs
stop being pushed to.
"""
from __future__ import annotations

import asyncio
import logging

from . import device_store

logger = logging.getLogger(__name__)


class APNSSender:
    """Sends APNs alerts to all of a user's registered devices."""

    def __init__(self, settings) -> None:
        self._settings = settings
        self._client = None
        self._enabled = bool(getattr(settings, "APNS_ENABLED", False))
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def _get_client(self):
        if self._client is not None:
            return self._client
        async with self._lock:
            if self._client is not None:
                return self._client
            try:
                from aioapns import APNs
            except Exception as e:
                logger.warning(
                    "APNS_ENABLED but aioapns is not installed (%s) — "
                    "run `pip install aioapns`. Push disabled.", e,
                )
                self._enabled = False
                return None
            s = self._settings
            use_sandbox = getattr(s, "APNS_ENV", "production").lower() != "production"
            # aioapns hands `key` straight to jwt.encode(), which expects the
            # PEM *contents*, not a file path. Passing the path raises
            # "Unable to load PEM file ... MalformedFraming" at first send.
            try:
                with open(s.APNS_KEY_PATH) as f:
                    key_content = f.read()
            except OSError as e:
                logger.error("APNs: cannot read key file %s: %s", s.APNS_KEY_PATH, e)
                self._enabled = False
                return None
            try:
                self._client = APNs(
                    key=key_content,
                    key_id=s.APNS_KEY_ID,
                    team_id=s.APNS_TEAM_ID,
                    topic=s.APNS_BUNDLE_ID,
                    use_sandbox=use_sandbox,
                )
            except Exception as e:
                logger.error("APNs client init failed: %s", e)
                self._enabled = False
                return None
            return self._client

    async def send(
        self, user_id: str, title: str, body: str, data: dict | None = None,
    ) -> int:
        """Send an alert to every active device of *user_id*.

        Returns the number of devices the push was accepted for. Revokes any
        token APNs reports as unregistered (410).
        """
        if not self._enabled:
            return 0
        # The client is bound to one APNs environment; tokens minted for the
        # other one can never succeed, so don't even try them.
        env = str(getattr(self._settings, "APNS_ENV", "production") or "production").lower()
        tokens = await asyncio.to_thread(
            device_store.list_device_tokens, user_id, env,
        )
        if not tokens:
            return 0
        client = await self._get_client()
        if client is None:
            return 0
        try:
            from aioapns import NotificationRequest, PushType
        except Exception:
            return 0

        sent = 0
        for t in tokens:
            device_token = t["device_token"]
            try:
                req = NotificationRequest(
                    device_token=device_token,
                    message={
                        "aps": {
                            "alert": {"title": title, "body": body},
                            "sound": "default",
                            # Surface proactive health nudges more prominently —
                            # breaks through Focus/lock and is more likely to
                            # show on the iPhone itself (not just a mirrored
                            # Apple Watch banner). Requires the
                            # com.apple.developer.usernotifications.time-sensitive
                            # entitlement in the app build; ignored otherwise.
                            "interruption-level": "time-sensitive",
                        },
                        **(data or {}),
                    },
                    push_type=PushType.ALERT,
                )
                resp = await client.send_notification(req)
                if getattr(resp, "is_successful", False):
                    sent += 1
                else:
                    desc = str(getattr(resp, "description", ""))
                    status = str(getattr(resp, "status", ""))
                    # 410/Unregistered = uninstalled. 400/BadDeviceToken = the
                    # token is malformed or belongs to the other environment;
                    # it can never succeed either, so retire it rather than
                    # re-warning on every push.
                    if status == "410" or desc in ("Unregistered", "BadDeviceToken"):
                        await asyncio.to_thread(device_store.revoke_device_token, device_token)
                        logger.info(
                            "APNs: revoked dead token (%s) for user=%s",
                            desc or status, user_id,
                        )
            except Exception as e:
                logger.warning("APNs send failed for user=%s: %s", user_id, e)
        return sent
