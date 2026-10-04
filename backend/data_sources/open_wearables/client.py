"""Async HTTP client for the open-wearables REST API.

Every public method is defensive: timeouts, connection errors and non-2xx
responses are logged and swallowed — methods never raise. Single-resource
methods return ``None`` on failure. The three paginated list methods
(``get_timeseries``, ``list_workouts``, ``list_sleep_sessions``) return a
:class:`PageResult` instead of a bare list: ``items`` is whatever was
fetched, and ``complete`` is False on any transport error, non-2xx
response, or on hitting the ``max_pages`` safety cap — signals the poller
needs to know a cursor can't be safely advanced past (see poller.py).
open-wearables is an optional, self-hosted service; if it is down or
misconfigured that must never destabilize HiMe's own request/response
cycle or background loops.

Auth: a single static API key sent as the ``X-Open-Wearables-API-Key``
header (open-wearables' "external integration" auth path — see
``app/services/api_key_service.py`` in the open-wearables source). This
does *not* grant access to the developer-dashboard-only endpoints (JWT
login), which is why webhook endpoint self-registration in
:mod:`.poller` is best-effort and expected to fail gracefully on older/
stricter open-wearables deployments — polling covers everything either way.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from ...config import settings

logger = logging.getLogger(__name__)

_API_PREFIX = "/api/v1"


@dataclass
class PageResult:
    """Result of a paginated list fetch.

    ``complete`` is False whenever the fetch did NOT retrieve the full
    requested window: a transport error / timeout, a non-2xx response, or
    hitting the ``max_pages`` runaway-guard cap partway through. ``items``
    always carries whatever was fetched before the interruption — callers
    (the poller) must not throw partial results away, but they also must
    not treat an incomplete fetch as if it covered the whole window (see
    ``poller.py``'s cursor-advancement rules).
    """

    items: list[dict] = field(default_factory=list)
    complete: bool = True


class OpenWearablesClient:
    """Thin async wrapper around the open-wearables ``/api/v1`` surface."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 20.0,
    ):
        self.base_url = (base_url if base_url is not None else settings.OPENWEARABLES_BASE_URL).rstrip("/")
        self.api_key = api_key if api_key is not None else settings.OPENWEARABLES_API_KEY
        self._timeout = timeout

    # ------------------------------------------------------------------ #
    # Low-level request helper
    # ------------------------------------------------------------------ #

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["X-Open-Wearables-API-Key"] = self.api_key
        return headers

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response | None:
        """Issue one HTTP request. Returns the response (any status code) or
        ``None`` on a transport-level failure (timeout, DNS, connection refused)."""
        url = f"{self.base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                return await client.request(method, url, headers=self._headers(), **kwargs)
        except httpx.HTTPError as exc:
            logger.warning("open-wearables: %s %s failed: %s", method, path, exc)
            return None
        except Exception as exc:  # pragma: no cover — defensive catch-all
            logger.warning("open-wearables: %s %s unexpected error: %s", method, path, exc)
            return None

    async def _request_json(self, method: str, path: str, **kwargs: Any) -> Any | None:
        """Like :meth:`_request` but also validates status + decodes JSON."""
        resp = await self._request(method, path, **kwargs)
        if resp is None:
            return None
        if resp.status_code >= 400:
            logger.warning(
                "open-wearables: %s %s -> HTTP %d: %s",
                method, path, resp.status_code, resp.text[:500],
            )
            return None
        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError as exc:
            logger.warning("open-wearables: %s %s returned non-JSON body: %s", method, path, exc)
            return None

    # ------------------------------------------------------------------ #
    # Health
    # ------------------------------------------------------------------ #

    async def health(self) -> bool:
        """Cheap reachability probe. Any HTTP response (even 401/404) counts
        as "reachable" — we only care whether the host answers."""
        resp = await self._request("GET", f"{_API_PREFIX}/oauth/providers")
        return resp is not None

    # ------------------------------------------------------------------ #
    # Users
    # ------------------------------------------------------------------ #

    async def find_user(self, external_user_id: str | None = None, email: str | None = None) -> dict | None:
        """Look up a user by external_user_id or email. Returns the first match, or None."""
        params: dict[str, str] = {}
        if external_user_id:
            params["external_user_id"] = external_user_id
        if email:
            params["email"] = email
        if not params:
            return None
        data = await self._request_json("GET", f"{_API_PREFIX}/users", params=params)
        if not data:
            return None
        items = data.get("items") or []
        return items[0] if items else None

    async def create_user(self, external_user_id: str | None = None, email: str | None = None) -> dict | None:
        body: dict[str, Any] = {}
        if external_user_id:
            body["external_user_id"] = external_user_id
        if email:
            body["email"] = email
        return await self._request_json("POST", f"{_API_PREFIX}/users", json=body)

    async def find_or_create_user(self, external_user_id: str, email: str | None = None) -> str | None:
        """Return the open-wearables UUID for ``external_user_id``, creating the user if needed."""
        existing = await self.find_user(external_user_id=external_user_id)
        if existing is None and email:
            existing = await self.find_user(email=email)
        if existing and existing.get("id"):
            return str(existing["id"])
        created = await self.create_user(external_user_id=external_user_id, email=email)
        if created and created.get("id"):
            return str(created["id"])
        return None

    # ------------------------------------------------------------------ #
    # Providers / connections / OAuth
    # ------------------------------------------------------------------ #

    async def get_providers(self, enabled_only: bool = False, cloud_only: bool = False) -> list[dict]:
        params = {"enabled_only": enabled_only, "cloud_only": cloud_only}
        data = await self._request_json("GET", f"{_API_PREFIX}/oauth/providers", params=params)
        return data if isinstance(data, list) else []

    async def get_authorization_url(
        self, provider: str, user_id: str, redirect_uri: str | None = None,
    ) -> dict | None:
        params: dict[str, str] = {"user_id": user_id}
        if redirect_uri:
            params["redirect_uri"] = redirect_uri
        return await self._request_json(
            "GET", f"{_API_PREFIX}/oauth/{provider}/authorize", params=params,
        )

    async def list_connections(self, user_id: str) -> list[dict]:
        data = await self._request_json("GET", f"{_API_PREFIX}/users/{user_id}/connections")
        return data if isinstance(data, list) else []

    # ------------------------------------------------------------------ #
    # Sync trigger
    # ------------------------------------------------------------------ #

    async def trigger_sync(self, provider: str, user_id: str, run_async: bool = True) -> dict | None:
        return await self._request_json(
            "POST",
            f"{_API_PREFIX}/providers/{provider}/users/{user_id}/sync",
            params={"async": run_async},
        )

    # ------------------------------------------------------------------ #
    # Sync status (used by poller.py to trigger early reconciliation)
    # ------------------------------------------------------------------ #

    async def list_sync_run_summaries(self, user_id: str, limit: int = 20) -> list[dict]:
        """Aggregated per-run sync status summaries (``GET
        /users/{user_id}/sync/runs`` — ``status``, ``provider``, ``ended_at``,
        ...), newest first. Unlike the webhook-management endpoints, this one
        accepts HiMe's plain ``X-Open-Wearables-API-Key`` (no developer JWT
        needed — see ``app/api/routes/v1/sync_status.py`` in the open-wearables
        source), so it works on stock deployments. Purely a latency
        optimization for :mod:`.poller`'s reconciliation pass: on any failure
        (older OW version without this route, network error) this returns
        ``[]`` like every other defensive list method, and the caller falls
        back to timed reconciliation."""
        data = await self._request_json(
            "GET", f"{_API_PREFIX}/users/{user_id}/sync/runs", params={"limit": limit},
        )
        return data if isinstance(data, list) else []

    # ------------------------------------------------------------------ #
    # Timeseries (cursor pagination)
    # ------------------------------------------------------------------ #

    async def get_timeseries(
        self,
        user_id: str,
        types: list[str],
        start_time: str,
        end_time: str,
        limit: int = 100,
        max_pages: int = 500,
    ) -> PageResult:
        """Fetch every timeseries sample in ``[start_time, end_time)``, following
        ``next_cursor`` until exhausted. ``max_pages`` is a hard safety cap so a
        server bug (cursor that never advances) can't loop forever — hitting it
        is treated as an incomplete fetch (``PageResult.complete=False``), not a
        silent truncation, since a 7-day backfill can easily exceed it."""
        samples: list[dict] = []
        cursor: str | None = None
        complete = True
        params_base: dict[str, Any] = {
            "start_time": start_time,
            "end_time": end_time,
            "limit": limit,
        }
        if types:
            params_base["types"] = types

        for _ in range(max_pages):
            params = dict(params_base)
            if cursor:
                params["cursor"] = cursor
            data = await self._request_json(
                "GET", f"{_API_PREFIX}/users/{user_id}/timeseries", params=params,
            )
            if data is None:
                complete = False
                break
            page = data.get("data") or []
            samples.extend(page)
            pagination = data.get("pagination") or {}
            cursor = pagination.get("next_cursor")
            if not cursor or not page:
                break
        else:
            complete = False
            logger.warning(
                "open-wearables: get_timeseries user=%s hit the max_pages=%d "
                "pagination cap (%d samples fetched so far) — this is a runaway "
                "guard, not expected in normal operation; returning partial "
                "results with complete=False so the caller doesn't advance its "
                "cursor past unfetched data",
                user_id, max_pages, len(samples),
            )
        return PageResult(items=samples, complete=complete)

    # ------------------------------------------------------------------ #
    # Events (workouts / sleep)
    # ------------------------------------------------------------------ #

    async def list_workouts(
        self, user_id: str, start_date: str, end_date: str, limit: int = 50, max_pages: int = 200,
    ) -> PageResult:
        return await self._paginate_events(
            f"{_API_PREFIX}/users/{user_id}/events/workouts", start_date, end_date, limit, max_pages,
        )

    async def list_sleep_sessions(
        self, user_id: str, start_date: str, end_date: str, limit: int = 50, max_pages: int = 200,
    ) -> PageResult:
        return await self._paginate_events(
            f"{_API_PREFIX}/users/{user_id}/events/sleep", start_date, end_date, limit, max_pages,
        )

    async def _paginate_events(
        self, path: str, start_date: str, end_date: str, limit: int, max_pages: int,
    ) -> PageResult:
        records: list[dict] = []
        cursor: str | None = None
        complete = True
        for _ in range(max_pages):
            params: dict[str, Any] = {"start_date": start_date, "end_date": end_date, "limit": limit}
            if cursor:
                params["cursor"] = cursor
            data = await self._request_json("GET", path, params=params)
            if data is None:
                complete = False
                break
            page = data.get("data") or []
            records.extend(page)
            pagination = data.get("pagination") or {}
            cursor = pagination.get("next_cursor")
            if not cursor or not page:
                break
        else:
            complete = False
            logger.warning(
                "open-wearables: %s hit the max_pages=%d pagination cap "
                "(%d records fetched so far) — returning partial results with "
                "complete=False",
                path, max_pages, len(records),
            )
        return PageResult(items=records, complete=complete)

    # ------------------------------------------------------------------ #
    # Outgoing webhook endpoint management (developer-scoped; best-effort —
    # see module docstring. Requires a developer JWT on stock open-wearables
    # deployments, which HiMe does not obtain, so these commonly 401/404 and
    # the caller (poller.register_webhook_once) treats that as a soft skip.)
    # ------------------------------------------------------------------ #

    async def create_webhook_endpoint(
        self, url: str, description: str | None = None, user_id: str | None = None,
    ) -> dict | None:
        body: dict[str, Any] = {"url": url}
        if description:
            body["description"] = description
        if user_id:
            body["user_id"] = user_id
        return await self._request_json("POST", f"{_API_PREFIX}/webhooks/endpoints", json=body)

    async def list_webhook_endpoints(self) -> list[dict]:
        data = await self._request_json("GET", f"{_API_PREFIX}/webhooks/endpoints")
        return data if isinstance(data, list) else []

    async def get_webhook_endpoint_secret(self, endpoint_id: str) -> str | None:
        data = await self._request_json("GET", f"{_API_PREFIX}/webhooks/endpoints/{endpoint_id}/secret")
        return data.get("key") if data else None
