# open-wearables Integration

> **Status: experimental, optional, off by default.** Nothing described here
> runs unless you explicitly run `./hime.sh wearables setup` and set
> `OPENWEARABLES_ENABLED=true`. A default HiMe install is unaffected.

[open-wearables](https://github.com/the-momentum/open-wearables) (OW) is a
self-hosted, open-source platform that normalizes data from wearable vendors
— Garmin, Polar, Whoop, Oura, Strava, Fitbit, Ultrahuman, Suunto, Sensor Bio,
and Google Health — behind one unified API. This integration runs OW as a
sidecar next to HiMe so you can pull data from any of those providers without
HiMe having to speak each vendor's API and OAuth dialect directly.

If you only use Apple Watch / iPhone via HiMe's built-in Watch Exporter, you
don't need this — it's for people who also (or instead) use a Garmin, Oura
ring, Whoop band, etc.

## Architecture

```
┌─────────────┐          ┌───────────────────────────────────────────┐
│  Wearable    │  OAuth   │ open-wearables (self-hosted, this repo's   │
│  vendor APIs │◄────────►│ ./external/open-wearables checkout)        │
│ Garmin/Oura/ │  pull &  │                                             │
│ Whoop/etc.   │  push    │  openwearables-app  (FastAPI, :8010→:8000) │
└─────────────┘          │  openwearables-celery-worker / -beat        │
                          │  openwearables-db (Postgres) / -redis      │
                          │  openwearables-svix (outgoing webhooks)    │
                          └───────────────┬─────────────────┬─────────┘
                                           │ poll REST API   │ webhook (optional)
                                           ▼                 ▼
                          ┌───────────────────────────────────────────┐
                          │ HiMe backend (docker-compose.yml, :8000)   │
                          │  GET  OPENWEARABLES_BASE_URL/...           │
                          │  POST /api/integrations/openwearables/     │
                          │       webhook                              │
                          │  → normalizes into HiMe's unified          │
                          │    `samples` store alongside Watch data    │
                          └───────────────────────────────────────────┘
```

HiMe's backend polls the OW API for new samples (`OPENWEARABLES_BASE_URL`,
default `http://openwearables-app:8000` — the two containers share a Docker
network) and, when `OUTGOING_WEBHOOKS_ENABLED=true` in OW, can also receive
near-real-time push notifications at
`http://backend:8000/api/integrations/openwearables/webhook`. Either path
lands the same normalized samples in HiMe's store, so the rest of the agent
(chat, analysis, reports) doesn't need to know which path the data took.

This overlay builds OW **from source** — the project publishes no container
images — into a pinned local checkout at `./external/open-wearables`.

## Sync behavior

HiMe polls OW in two tiers:

- **Fast poll** — every `OPENWEARABLES_POLL_INTERVAL` seconds (default
  300s), per category (timeseries / workouts / sleep), using a persisted
  high-water-mark cursor keyed on each sample's own *event* time (a few
  minutes of overlap, not wall-clock "now"). The cursor only advances when
  the fetch came back complete; a transport error, non-2xx, or hitting the
  pagination safety cap holds the cursor back (or, if some pages did come
  back before the cap, advances only to the last sample actually fetched)
  so a bad poll can never silently skip data.
- **Reconciliation** — every `OPENWEARABLES_RECONCILE_INTERVAL` seconds
  (default 3600s), independent of the fast-poll cursors: re-fetches and
  re-ingests the trailing `OPENWEARABLES_RECONCILE_WINDOW_HOURS` (default
  48h) for every category. This is what catches data whose *sync* time
  lags far behind its *event* time — e.g. Oura uploading a whole night's
  sleep the next morning, well outside the fast poll's small overlap
  window. Re-ingestion is idempotent (upsert on `(timestamp, feature_type)`),
  so the overlap with the fast poll costs nothing. If OW exposes its
  sync-run status endpoint, HiMe also triggers reconciliation immediately
  when a sync run completes, instead of waiting for the timer.

Granular samples are always authoritative over a provider's pre-aggregated
daily totals (`is_daily_total`): a daily total is only stored when no
granular sample for that feature/provider/day has been seen, and gets
deleted automatically once granular data for that day arrives.

Webhook delivery (when registered — see below) supplements the poller with
near-real-time pushes; either path lands in the same table, and re-ingesting
overlapping data through both is harmless for the same reason.

## Quickstart

Requires Docker (the OW stack only runs in Docker; there is no native mode
for it). HiMe itself can be running in either docker or native mode.

```bash
./hime.sh wearables setup       # clone OW (pinned commit) + generate its .env
./hime.sh wearables start       # build & start db/redis/svix/app/celery
./hime.sh wearables bootstrap   # headless: create an OW API key, write it to .env
```

`bootstrap` writes `OPENWEARABLES_API_KEY=...`, `OPENWEARABLES_ENABLED=true`,
and `OPENWEARABLES_BASE_URL=...` into HiMe's own `.env` (creating it from
`.env.example` first if you haven't run `./setup.sh` yet). The base URL is
matched to how HiMe's own backend is running, detected the same way as
`./hime.sh start`/`restart` (`HIME_RUN_MODE` in `.env`, or an explicit
`--docker`/`--native` flag on the `bootstrap` command): docker mode gets the
compose-internal `http://openwearables-app:8000` (both containers share a
Docker network); native mode gets the published `http://localhost:8010`,
since a host-run backend can't resolve compose service names. Re-run
`bootstrap` (or edit `OPENWEARABLES_BASE_URL` by hand) if you switch HiMe
between docker and native later. Then restart HiMe so the backend picks the
values up:

```bash
./hime.sh restart --rebuild   # docker mode
./hime.sh restart             # native mode
```

Check it's wired up:

```bash
curl http://localhost:8000/api/integrations/openwearables/status
```

To generate synthetic data and exercise the pipeline without owning any of
the supported devices:

```bash
./hime.sh wearables seed                 # preset: active_athlete
./hime.sh wearables seed deep_deficit    # or another preset id
```

List presets yourself at `http://localhost:8010/api/v1/settings/seed/presets`
(requires the bearer token from `wearables bootstrap`, or log into
`http://localhost:8010/docs` with the admin credentials `wearables setup`
printed).

Note that seeding creates its **own synthetic open-wearables user**, separate
from the user HiMe's poller auto-creates — so seeded data is not picked up by
HiMe automatically. To route it through the full HiMe pipeline for testing,
point the poller at the seeded user: replace the `ow_user_id` stored under
`open_wearables` in `memory/app_state.json` with the seeded user's id (find it
via `GET http://localhost:8010/api/v1/users` with your API key) and restart
HiMe. Seed presets also generate data anchored months in the past, so raise
`OPENWEARABLES_BACKFILL_DAYS` accordingly or the initial backfill window will
miss it.

Tear down just the OW containers (HiMe's own backend/frontend/watch are
never touched by any `wearables` command):

```bash
./hime.sh wearables stop
```

## Connecting a real provider

Once `OPENWEARABLES_ENABLED=true` and HiMe has restarted, connect an account
either:

- From the HiMe **Devices** page in the dashboard/app, or
- Directly: `POST /api/integrations/openwearables/connect/{provider}`

Both drive an OAuth flow against the vendor, brokered by OW, and OW then
starts pulling (and/or pushing via webhook) that account's data.

**Garmin is the one exception**: Garmin's OAuth flow requires a **publicly
reachable HTTPS callback URL** — there is no pull-only / local-only mode for
it, unlike every other supported provider. To connect Garmin locally you need
a tunnel (e.g. [ngrok](https://ngrok.com)) pointed at
`openwearables-app:8000` (or the published `localhost:8010`), with the
tunnel's HTTPS URL set as `API_BASE_URL` and `GARMIN_CLIENT_ID`/
`GARMIN_CLIENT_SECRET` configured for that callback in the Garmin Developer
Portal, before you start the connect flow. All other providers (Polar,
Whoop, Oura, Strava, Fitbit, Ultrahuman, Suunto, Sensor Bio, Google Health)
work over plain `http://localhost:8010` with no tunnel required.

## Resource footprint

Running the full OW sidecar (db, redis, svix, app, 2x celery — flower and
OW's own frontend are omitted by default) adds roughly **350–550 MB** of RAM
on top of HiMe's own stack, plus disk for the Postgres/Redis volumes
(`openwearables-postgres-data`, `openwearables-redis-data` — separate from
HiMe's own `watch-data` volume) and the ~1–2GB source checkout under
`./external/open-wearables`.

Optional extras, disabled by default (uncomment in
`docker-compose.openwearables.yml` to enable):

- **flower** (`openwearables-flower`, port 5555) — Celery task monitoring UI.
- **OW's own frontend** (`openwearables-frontend`, port 3000) — a
  general-purpose dashboard/portal for OW itself, separate from HiMe's UI.

## Version pinning policy

The checkout is pinned to a specific open-wearables commit SHA (see
`OW_PINNED_SHA` near the top of `hime.sh`) rather than tracking a branch —
OW has no release/tag scheme this integration can pin to instead, and a
moving target would make this integration's behavior unreproducible across
installs.

To upgrade: pick a newer commit from the
[open-wearables commit history](https://github.com/the-momentum/open-wearables/commits/main),
review what changed (schema/env-var changes matter most — check
`backend/config/.env.example` and `backend/migrations/` for anything that
doesn't automatically fit this overlay's assumptions), update `OW_PINNED_SHA`
in `hime.sh`, then:

```bash
./hime.sh wearables setup    # re-checks-out at the new SHA (existing .env is kept)
./hime.sh wearables start    # rebuilds images against the new source
```

## Troubleshooting

**Svix health.** `openwearables-svix` must be healthy before webhook
registration succeeds (`app.sh` retries 3 times on startup, then gives up
until the next restart):

```bash
docker compose -f docker-compose.yml -f docker-compose.openwearables.yml \
    logs openwearables-svix
docker compose -f docker-compose.yml -f docker-compose.openwearables.yml \
    exec openwearables-svix svix-server healthcheck http://localhost:8071
```

**Celery / sync jobs.** Historical and periodic syncs run on the worker and
beat containers, not the API container:

```bash
docker compose -f docker-compose.yml -f docker-compose.openwearables.yml \
    logs -f openwearables-celery-worker openwearables-celery-beat
```

**End-to-end status.** Once HiMe's backend connector is enabled, check what
it thinks the integration state is:

```bash
curl http://localhost:8000/api/integrations/openwearables/status
```

**OW itself unreachable.** Confirm the container is healthy and the API
responds directly, bypassing HiMe:

```bash
./hime.sh wearables status
curl http://localhost:8010/docs
```

**Starting over.** Delete the generated env and rerun setup:

```bash
rm external/open-wearables/backend/config/.env
./hime.sh wearables setup
```

To remove the integration entirely: `./hime.sh wearables stop`, then
`rm -rf external/open-wearables`, then unset `OPENWEARABLES_ENABLED` (or set
it to `false`) in `.env`.
