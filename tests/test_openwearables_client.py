"""
Tests for backend.data_sources.open_wearables.client.OpenWearablesClient.

Uses httpx.MockTransport (already available via the pinned httpx dependency,
no respx needed) to fake the open-wearables server — covers cursor
pagination for /timeseries and /events/workouts, and the "never raise"
contract when the server is unreachable or returns an error status.
"""
from __future__ import annotations

import httpx

from backend.data_sources.open_wearables.client import OpenWearablesClient


def _patch_async_client(monkeypatch, handler) -> None:
    """Redirect every httpx.AsyncClient() the client constructs onto a MockTransport."""
    transport = httpx.MockTransport(handler)

    class _FakeAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(
        "backend.data_sources.open_wearables.client.httpx.AsyncClient", _FakeAsyncClient,
    )


class TestTimeseriesPagination:
    async def test_follows_next_cursor_until_exhausted(self, monkeypatch):
        pages = [
            {
                "data": [{"type": "heart_rate", "value": 60, "timestamp": "2026-08-10T08:00:00Z"}],
                "pagination": {"next_cursor": "page-2", "has_more": True},
                "metadata": {},
            },
            {
                "data": [{"type": "heart_rate", "value": 61, "timestamp": "2026-08-10T08:05:00Z"}],
                "pagination": {"next_cursor": None, "has_more": False},
                "metadata": {},
            },
        ]
        seen_cursors: list[str | None] = []

        def handler(request: httpx.Request) -> httpx.Response:
            cursor = request.url.params.get("cursor")
            seen_cursors.append(cursor)
            idx = 0 if cursor is None else 1
            return httpx.Response(200, json=pages[idx])

        _patch_async_client(monkeypatch, handler)

        client = OpenWearablesClient(base_url="http://ow.test", api_key="test-key")
        result = await client.get_timeseries(
            "user-1", [], "2026-08-01T00:00:00Z", "2026-08-10T00:00:00Z",
        )

        assert result.complete is True
        assert len(result.items) == 2
        assert [s["value"] for s in result.items] == [60, 61]
        assert seen_cursors == [None, "page-2"]

    async def test_stops_on_empty_page(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": [], "pagination": {"next_cursor": None}, "metadata": {}})

        _patch_async_client(monkeypatch, handler)
        client = OpenWearablesClient(base_url="http://ow.test", api_key="k")
        result = await client.get_timeseries("u", [], "2026-08-01T00:00:00Z", "2026-08-10T00:00:00Z")
        assert result.items == []
        assert result.complete is True

    async def test_max_pages_cap_marks_incomplete(self, monkeypatch):
        """Hitting the pagination safety cap must surface as an incomplete
        fetch — not silently pretend the window was fully covered (F3)."""
        call_count = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            call_count["n"] += 1
            cursor = f"page-{call_count['n'] + 1}"
            return httpx.Response(200, json={
                "data": [{"type": "heart_rate", "value": call_count["n"], "timestamp": "2026-08-10T08:00:00Z"}],
                "pagination": {"next_cursor": cursor},
                "metadata": {},
            })

        _patch_async_client(monkeypatch, handler)
        client = OpenWearablesClient(base_url="http://ow.test", api_key="k")
        result = await client.get_timeseries(
            "u", [], "2026-08-01T00:00:00Z", "2026-08-10T00:00:00Z", max_pages=3,
        )
        assert result.complete is False
        assert len(result.items) == 3  # whatever was fetched before the cap, not discarded

    async def test_error_mid_pagination_returns_partial_incomplete(self, monkeypatch):
        """A transport error partway through pagination must not discard the
        pages already fetched, but must mark the result incomplete (F2)."""
        call_count = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            call_count["n"] += 1
            if call_count["n"] == 1:
                return httpx.Response(200, json={
                    "data": [{"type": "heart_rate", "value": 60, "timestamp": "2026-08-10T08:00:00Z"}],
                    "pagination": {"next_cursor": "page-2"},
                    "metadata": {},
                })
            return httpx.Response(500, json={"detail": "boom"})

        _patch_async_client(monkeypatch, handler)
        client = OpenWearablesClient(base_url="http://ow.test", api_key="k")
        result = await client.get_timeseries("u", [], "2026-08-01T00:00:00Z", "2026-08-10T00:00:00Z")
        assert result.complete is False
        assert len(result.items) == 1

    async def test_sends_api_key_header(self, monkeypatch):
        received_headers = {}

        def handler(request: httpx.Request) -> httpx.Response:
            received_headers.update(request.headers)
            return httpx.Response(200, json={"data": [], "pagination": {"next_cursor": None}, "metadata": {}})

        _patch_async_client(monkeypatch, handler)
        client = OpenWearablesClient(base_url="http://ow.test", api_key="super-secret")
        await client.get_timeseries("u", [], "2026-08-01T00:00:00Z", "2026-08-10T00:00:00Z")
        assert received_headers.get("x-open-wearables-api-key") == "super-secret"


class TestWorkoutsPagination:
    async def test_follows_pagination_cursor(self, monkeypatch):
        pages = [
            {"data": [{"id": "w1", "type": "running"}], "pagination": {"next_cursor": "c2"}, "metadata": {}},
            {"data": [{"id": "w2", "type": "cycling"}], "pagination": {"next_cursor": None}, "metadata": {}},
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            cursor = request.url.params.get("cursor")
            return httpx.Response(200, json=pages[0 if cursor is None else 1])

        _patch_async_client(monkeypatch, handler)
        client = OpenWearablesClient(base_url="http://ow.test", api_key="k")
        result = await client.list_workouts("u", "2026-08-01T00:00:00Z", "2026-08-10T00:00:00Z")
        assert [w["id"] for w in result.items] == ["w1", "w2"]
        assert result.complete is True


class TestSleepPagination:
    async def test_follows_pagination_cursor(self, monkeypatch):
        pages = [
            {"data": [{"id": "s1"}], "pagination": {"next_cursor": "c2"}, "metadata": {}},
            {"data": [{"id": "s2"}], "pagination": {"next_cursor": None}, "metadata": {}},
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            cursor = request.url.params.get("cursor")
            return httpx.Response(200, json=pages[0 if cursor is None else 1])

        _patch_async_client(monkeypatch, handler)
        client = OpenWearablesClient(base_url="http://ow.test", api_key="k")
        result = await client.list_sleep_sessions("u", "2026-08-01T00:00:00Z", "2026-08-10T00:00:00Z")
        assert [s["id"] for s in result.items] == ["s1", "s2"]
        assert result.complete is True


class TestResilience:
    """OW being down/misconfigured must never raise through to the caller."""

    async def test_connection_error_returns_empty_incomplete(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        _patch_async_client(monkeypatch, handler)
        client = OpenWearablesClient(base_url="http://ow.test", api_key="k")
        result = await client.get_timeseries("u", [], "2026-08-01T00:00:00Z", "2026-08-10T00:00:00Z")
        assert result.items == []
        assert result.complete is False

    async def test_http_error_status_returns_none_for_single_resource(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"detail": "boom"})

        _patch_async_client(monkeypatch, handler)
        client = OpenWearablesClient(base_url="http://ow.test", api_key="k")
        result = await client.find_user(external_user_id="LiveUser")
        assert result is None

    async def test_health_true_on_any_response(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"detail": "unauthorized"})

        _patch_async_client(monkeypatch, handler)
        client = OpenWearablesClient(base_url="http://ow.test", api_key="k")
        assert await client.health() is True

    async def test_health_false_on_connection_error(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down", request=request)

        _patch_async_client(monkeypatch, handler)
        client = OpenWearablesClient(base_url="http://ow.test", api_key="k")
        assert await client.health() is False


class TestUserResolution:
    async def test_find_or_create_returns_existing_user(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "GET"
            return httpx.Response(200, json={"items": [{"id": "existing-uuid"}], "total": 1, "page": 1, "limit": 20})

        _patch_async_client(monkeypatch, handler)
        client = OpenWearablesClient(base_url="http://ow.test", api_key="k")
        user_id = await client.find_or_create_user("LiveUser")
        assert user_id == "existing-uuid"

    async def test_find_or_create_creates_when_missing(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(200, json={"items": [], "total": 0, "page": 1, "limit": 20})
            return httpx.Response(201, json={"id": "new-uuid"})

        _patch_async_client(monkeypatch, handler)
        client = OpenWearablesClient(base_url="http://ow.test", api_key="k")
        user_id = await client.find_or_create_user("LiveUser")
        assert user_id == "new-uuid"
