"""
Tests for backend.data_sources.open_wearables.webhook.

Covers:
- verify_svix_signature(): valid signature, invalid signature, stale
  timestamp, malformed inputs (pure function — no app/network needed).
- The POST /api/integrations/openwearables/webhook route end-to-end via the
  shared `test_client` fixture: signature accepted/rejected, timeseries /
  workout / sleep event payloads correctly ingested into a fake DataStore,
  and the "always 200, never 500" contract for malformed JSON.
- OPENWEARABLES_ENABLED=False -> route 404s without touching the network or
  the (unconfigured) secret.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import pytest

from backend.data_sources.open_wearables.webhook import WEBHOOK_PATH, verify_svix_signature

TEST_SECRET = "whsec_" + base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()


def _sign(secret: str, msg_id: str, timestamp: str, body: bytes) -> str:
    key = base64.b64decode(secret.removeprefix("whsec_"))
    signed_content = f"{msg_id}.{timestamp}.".encode() + body
    sig = base64.b64encode(hmac.new(key, signed_content, hashlib.sha256).digest()).decode("utf-8")
    return f"v1,{sig}"


# ---------------------------------------------------------------------------
# Pure signature verification
# ---------------------------------------------------------------------------


class TestVerifySvixSignature:
    def test_valid_signature_accepted(self):
        body = b'{"type":"heart_rate.created"}'
        msg_id = "msg_123"
        ts = str(int(time.time()))
        sig = _sign(TEST_SECRET, msg_id, ts, body)
        assert verify_svix_signature(TEST_SECRET, msg_id, ts, body, sig) is True

    def test_tampered_body_rejected(self):
        body = b'{"type":"heart_rate.created"}'
        msg_id = "msg_123"
        ts = str(int(time.time()))
        sig = _sign(TEST_SECRET, msg_id, ts, body)
        tampered = b'{"type":"heart_rate.created","evil":true}'
        assert verify_svix_signature(TEST_SECRET, msg_id, ts, tampered, sig) is False

    def test_wrong_secret_rejected(self):
        body = b"{}"
        msg_id = "msg_1"
        ts = str(int(time.time()))
        sig = _sign(TEST_SECRET, msg_id, ts, body)
        other_secret = "whsec_" + base64.b64encode(b"ffffffffffffffffffffffffffffffff").decode()
        assert verify_svix_signature(other_secret, msg_id, ts, body, sig) is False

    def test_stale_timestamp_rejected(self):
        body = b"{}"
        msg_id = "msg_1"
        stale_ts = str(int(time.time()) - 3600)  # 1 hour old
        sig = _sign(TEST_SECRET, msg_id, stale_ts, body)
        assert verify_svix_signature(TEST_SECRET, msg_id, stale_ts, body, sig) is False

    def test_future_timestamp_rejected(self):
        body = b"{}"
        msg_id = "msg_1"
        future_ts = str(int(time.time()) + 3600)
        sig = _sign(TEST_SECRET, msg_id, future_ts, body)
        assert verify_svix_signature(TEST_SECRET, msg_id, future_ts, body, sig) is False

    def test_multi_signature_header_accepts_any_match(self):
        body = b"{}"
        msg_id = "msg_1"
        ts = str(int(time.time()))
        real_sig = _sign(TEST_SECRET, msg_id, ts, body)
        header = f"v1,bogusbase64sig== {real_sig}"
        assert verify_svix_signature(TEST_SECRET, msg_id, ts, body, header) is True

    def test_missing_signature_header_rejected(self):
        assert verify_svix_signature(TEST_SECRET, "msg_1", str(int(time.time())), b"{}", "") is False

    def test_missing_secret_rejected(self):
        assert verify_svix_signature("", "msg_1", str(int(time.time())), b"{}", "v1,abc") is False

    def test_non_numeric_timestamp_rejected(self):
        assert verify_svix_signature(TEST_SECRET, "msg_1", "not-a-number", b"{}", "v1,abc") is False

    def test_malformed_secret_rejected(self):
        assert verify_svix_signature("whsec_not-valid-base64!!!", "msg_1", str(int(time.time())), b"{}", "v1,abc") is False


# ---------------------------------------------------------------------------
# End-to-end route behaviour
# ---------------------------------------------------------------------------


@pytest.fixture
def enabled_openwearables(mock_settings, monkeypatch):
    """Flip OPENWEARABLES_ENABLED on for the duration of one test.

    ``test_client`` (see conftest.py) patches ``...open_wearables.webhook.settings``
    (among others) to this same ``mock_settings`` instance, so mutating it
    here — as long as this fixture is requested alongside ``test_client`` in
    the same test, which shares the one function-scoped ``mock_settings``
    instance between them — is visible inside the webhook route handler too.
    """
    monkeypatch.setattr(mock_settings, "OPENWEARABLES_ENABLED", True)
    return mock_settings


@pytest.fixture
def fake_ow_data_store(monkeypatch, tmp_dirs):
    """Point the webhook handler's lazy DataStore singleton at a throwaway DB."""
    import backend.data_sources.open_wearables.webhook as ow_webhook
    from backend.agent.data_store import DataStore

    store = DataStore(db_path=tmp_dirs["data_stores"], user_id="LiveUser")
    monkeypatch.setattr(ow_webhook, "_get_data_store", lambda: store)
    monkeypatch.setattr(ow_webhook, "_get_configured_secret", lambda: TEST_SECRET)
    return store


def _post_webhook(client, body: dict, secret: str = TEST_SECRET, msg_id: str = "msg_1", ts: str | None = None, bad_sig: bool = False):
    raw = json.dumps(body).encode("utf-8")
    timestamp = ts or str(int(time.time()))
    sig = "v1,bogus" if bad_sig else _sign(secret, msg_id, timestamp, raw)
    return client.post(
        WEBHOOK_PATH,
        content=raw,
        headers={
            "svix-id": msg_id,
            "svix-timestamp": timestamp,
            "svix-signature": sig,
            "content-type": "application/json",
        },
    )


class TestWebhookRoute:
    async def test_disabled_returns_404(self, test_client, fake_ow_data_store):
        # OPENWEARABLES_ENABLED left at its default (False).
        resp = await _post_webhook(test_client, {"type": "heart_rate.created", "data": {}})
        assert resp.status_code == 404

    async def test_valid_signature_ingests_timeseries(self, test_client, enabled_openwearables, fake_ow_data_store):
        payload = {
            "type": "heart_rate.created",
            "data": {
                "user_id": "u1",
                "provider": "garmin",
                "series_type": "heart_rate",
                "samples": [{"timestamp": "2026-08-10T08:00:00Z", "type": "heart_rate", "value": 61}],
            },
        }
        resp = await _post_webhook(test_client, payload)
        assert resp.status_code == 200

        with fake_ow_data_store.get_connection() as conn:
            row = conn.execute(
                "SELECT value FROM samples WHERE feature_type = 'heart_rate' AND timestamp = '2026-08-10T08:00:00'"
            ).fetchone()
        assert row is not None
        assert row[0] == 61

    async def test_workout_created_event_ingested(self, test_client, enabled_openwearables, fake_ow_data_store):
        payload = {
            "type": "workout.created",
            "data": {
                "id": "w1",
                "type": "cycling",
                "start_time": "2026-08-10T07:00:00Z",
                "end_time": "2026-08-10T07:30:00Z",
                "duration_seconds": 1800,
                "source": {"provider": "garmin"},
                "calories_kcal": 200,
            },
        }
        resp = await _post_webhook(test_client, payload)
        assert resp.status_code == 200
        with fake_ow_data_store.get_connection() as conn:
            row = conn.execute(
                "SELECT value FROM samples WHERE feature_type = 'workout_cycling_duration'"
            ).fetchone()
        assert row is not None
        assert row[0] == 1800

    async def test_sleep_created_event_ingested(self, test_client, enabled_openwearables, fake_ow_data_store):
        payload = {
            "type": "sleep.created",
            "data": {
                "id": "s1",
                "start_time": "2026-08-10T22:00:00Z",
                "end_time": "2026-08-11T06:00:00Z",
                "source": {"provider": "oura"},
                "duration_seconds": 28800,
                "sleep_duration_seconds": 25200,
            },
        }
        resp = await _post_webhook(test_client, payload)
        assert resp.status_code == 200
        with fake_ow_data_store.get_connection() as conn:
            row = conn.execute(
                "SELECT value FROM samples WHERE feature_type = 'sleep_asleep'"
            ).fetchone()
        assert row is not None
        assert row[0] == 25200

    async def test_invalid_signature_rejected(self, test_client, enabled_openwearables, fake_ow_data_store):
        resp = await _post_webhook(
            test_client, {"type": "heart_rate.created", "data": {}}, bad_sig=True,
        )
        assert resp.status_code == 401

    async def test_stale_timestamp_rejected(self, test_client, enabled_openwearables, fake_ow_data_store):
        stale_ts = str(int(time.time()) - 3600)
        resp = await _post_webhook(test_client, {"type": "heart_rate.created", "data": {}}, ts=stale_ts)
        assert resp.status_code == 401

    async def test_malformed_json_never_500s(self, test_client, enabled_openwearables, fake_ow_data_store):
        raw = b"{not valid json"
        msg_id = "msg_bad"
        timestamp = str(int(time.time()))
        sig = _sign(TEST_SECRET, msg_id, timestamp, raw)
        resp = await test_client.post(
            WEBHOOK_PATH,
            content=raw,
            headers={"svix-id": msg_id, "svix-timestamp": timestamp, "svix-signature": sig},
        )
        assert resp.status_code == 200

    async def test_unhandled_event_type_still_200s(self, test_client, enabled_openwearables, fake_ow_data_store):
        resp = await _post_webhook(test_client, {"type": "connection.created", "data": {}})
        assert resp.status_code == 200

    async def test_no_secret_configured_rejected(self, test_client, enabled_openwearables, tmp_dirs, monkeypatch):
        import backend.data_sources.open_wearables.webhook as ow_webhook
        from backend.agent.data_store import DataStore

        store = DataStore(db_path=tmp_dirs["data_stores"], user_id="LiveUser")
        monkeypatch.setattr(ow_webhook, "_get_data_store", lambda: store)
        monkeypatch.setattr(ow_webhook, "_get_configured_secret", lambda: None)

        resp = await _post_webhook(test_client, {"type": "heart_rate.created", "data": {}})
        assert resp.status_code == 401
