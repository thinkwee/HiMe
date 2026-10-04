"""Connector for the open-wearables platform.

open-wearables (https://github.com/open-wearables/open-wearables) is an
optional, self-hosted FastAPI service that unifies Garmin / Oura / Whoop /
Polar / ... wearable data behind one REST API + outgoing webhooks. This
package funnels that data into HiMe's own ``samples`` table
(:class:`backend.agent.data_store.DataStore`) so it shows up next to native
Apple Watch data on the dashboard and in agent context.

Modules:
    client   -- async HTTP client for the open-wearables REST API.
    features -- OW_FEATURE_SPEC (display specs for OW-only metrics) and
                SERIES_TYPE_MAP (OW series type -> HiMe feature_type, for
                metrics HiMe already tracks natively).
    mapper   -- pure functions: OW payload -> DataStore.ingest_batch() rows.
    poller   -- background asyncio loop: REST pull + cursor tracking.
    webhook  -- inbound Svix-signed webhook receiver (push path).
    routes   -- status/providers/connect/sync endpoints for the frontend.

Entirely inert when ``OPENWEARABLES_ENABLED=False`` (the default): no
background task is started, no network call is made, and the webhook/status
routes report ``enabled: false`` without touching the network.
"""
