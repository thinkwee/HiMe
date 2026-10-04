"""
Tests for backend.data_sources.open_wearables.routes.

Covers the ``GET /api/integrations/openwearables/providers`` handler's
``requires_public_https`` annotation, which must key off the provider
*slug* (the ``"provider"`` field on open-wearables' payload) rather than a
non-existent ``"id"`` field. See routes.py's ``get_providers`` for the
"garmin" special case this guards against regressing.
"""
from __future__ import annotations

import httpx
import pytest


def _patch_ow_providers(monkeypatch, providers: list[dict]) -> None:
    """Redirect the open-wearables client's httpx.AsyncClient onto a fake
    /oauth/providers response, mirroring the pattern in
    test_openwearables_client.py."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/oauth/providers")
        return httpx.Response(200, json=providers)

    transport = httpx.MockTransport(handler)

    class _FakeAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(
        "backend.data_sources.open_wearables.client.httpx.AsyncClient", _FakeAsyncClient,
    )


@pytest.fixture
def enabled_openwearables(mock_settings, monkeypatch):
    """Flip OPENWEARABLES_ENABLED on — same pattern as
    test_openwearables_webhook.py's fixture of the same name."""
    monkeypatch.setattr(mock_settings, "OPENWEARABLES_ENABLED", True)
    return mock_settings


class TestProvidersRequiresPublicHttps:
    async def test_garmin_flagged_via_provider_slug(
        self, test_client, enabled_openwearables, monkeypatch,
    ):
        """Real open-wearables /oauth/providers payloads have no "id" key —
        only "provider" (slug) and "name" (display). garmin's display name
        ("Garmin") happens to lowercase to its slug, which used to mask a
        latent bug (checking p.get("id") first). Assert the slug-keyed
        provider is still correctly flagged."""
        _patch_ow_providers(monkeypatch, [
            {"provider": "garmin", "name": "Garmin", "has_cloud_api": True},
        ])
        resp = await test_client.get("/api/integrations/openwearables/providers")
        assert resp.status_code == 200
        providers = resp.json()["providers"]
        assert providers[0]["requires_public_https"] is True

    async def test_slug_used_over_mismatched_display_name(
        self, test_client, enabled_openwearables, monkeypatch,
    ):
        """Regression guard: a provider whose display name does NOT reduce to
        its slug must still be matched on "provider", not "name" (and
        definitely not "id", which open-wearables never sends)."""
        _patch_ow_providers(monkeypatch, [
            {"provider": "garmin", "name": "Totally Different Display Name", "has_cloud_api": True},
            {"provider": "oura", "name": "Oura", "has_cloud_api": True},
        ])
        resp = await test_client.get("/api/integrations/openwearables/providers")
        assert resp.status_code == 200
        by_provider = {p["provider"]: p for p in resp.json()["providers"]}
        assert by_provider["garmin"]["requires_public_https"] is True
        assert by_provider["oura"]["requires_public_https"] is False

    async def test_disabled_returns_503(self, test_client):
        # OPENWEARABLES_ENABLED left at its default (False).
        resp = await test_client.get("/api/integrations/openwearables/providers")
        assert resp.status_code == 503
