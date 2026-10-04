# HiMe Developer Guide

This document is for people who want to **modify** HiMe — fix bugs, add tools, ship new LLM providers, or build new features. If you just want to *run* HiMe, see the [README](../README.md).

---

## Repository layout

```
hime/
├── backend/                  Python FastAPI server
│   ├── main.py                  app + lifespan + cron scheduler
│   ├── config.py                Pydantic settings (.env)
│   ├── agent/                   single-agent core, tools, providers
│   │   ├── autonomous_agent.py  state + run_forever() event loop
│   │   ├── agent_loops.py       analysis + chat implementations
│   │   ├── agent_tools.py       per-mode tool sets
│   │   ├── agent_prompts.py     layered prompt assembly
│   │   ├── llm_providers.py     provider factory (15)
│   │   ├── llm/                 individual provider modules
│   │   ├── tools/               BaseTool subclasses
│   │   ├── trigger_evaluator.py event-driven analysis triggers
│   │   ├── fact_verifier.py     evidence trail for outbound messages
│   │   └── persistence.py       agent state save/restore
│   ├── api/                     FastAPI routers
│   ├── data_readers/            live + digested data readers
│   ├── messaging/               platform-agnostic BaseGateway / registry / inbox
│   ├── ios_gateway/             native in-app iOS channel (WebSocket downlink + APNs)
│   ├── telegram/                Telegram gateway implementation
│   ├── feishu/                  Feishu (Lark) gateway implementation
│   ├── weixin/                  WeChat (Weixin ClawBot / iLink) gateway implementation
│   ├── api/event_hub.py         agent event fan-out (multi WebSocket subscribers)
│   └── services/                streaming, connection manager
├── frontend/                 React 18 + Vite SPA
├── ios/                      iPhone + watchOS apps + WatchExporter
│   ├── hime/hime/                  iPhone SwiftUI app
│   ├── hime/himewatch/            watchOS SwiftUI app
│   └── Server/                     WatchExporter (port 8765)
├── prompts/                  layered prompt files (soul, rules_chat, sub_analysis, plan_designer, …)
├── tests/                    pytest tests
├── docs/                     this folder
├── hime.sh                   unified dev CLI
├── docker-compose.yml        production-style stack
└── pyproject.toml            ruff + pytest config
```

---

## Local environment setup

### Backend (Python)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
pip install ruff pytest pytest-asyncio   # dev tools
```

Then run the API server:

```bash
python -m backend.main
```

Set `HIME_DEV_RELOAD=true` in your environment to enable Uvicorn auto-reload.

> `hime.sh start` does the same things plus brings up the frontend and watch exporter. Use it for end-to-end work; use `python -m backend.main` when you only care about the API.

### Frontend (React + Vite)

```bash
cd frontend
npm install
npm run dev
```

The Vite dev server proxies `/api/*` to `http://localhost:8000` (configurable via `VITE_API_TARGET`).

### iOS / watchOS

1. Open `ios/hime/hime.xcodeproj` in Xcode 16+.
2. Copy the build-config template once:
   ```bash
   cp ios/hime/Config.xcconfig.template ios/hime/Config.xcconfig
   ```
   Then edit `ios/hime/Config.xcconfig` and set:
   ```
   DEVELOPMENT_TEAM = <your-apple-team-id>
   BUNDLE_ID_PREFIX = com.example
   ```
   `Config.xcconfig` is **gitignored** so your personal team ID never lands in version control.
3. The bundle identifiers `$(BUNDLE_ID_PREFIX).hime` and `$(BUNDLE_ID_PREFIX).hime.watchkitapp` are derived from this prefix automatically.
4. ⌘R to build and run.

> **Don't change the Team in Xcode's *Signing & Capabilities* UI.** `project.pbxproj` does not carry a `DEVELOPMENT_TEAM` value on purpose — Xcode reads it from your `Config.xcconfig` via `baseConfigurationReference`. If you click the Team dropdown in Xcode, it will write your real Team ID back into `project.pbxproj`, which defeats the whole point. Edit `Config.xcconfig` instead. If Xcode ever rewrites the pbxproj, revert that hunk before committing.

### WatchExporter

The WatchExporter is a tiny aiohttp server in `ios/Server/server.py`. You usually don't need to touch it — `hime.sh start` (and the Docker `watch` service) bring it up automatically. Run it standalone with:

```bash
python ios/Server/server.py
```

---

## Architecture overview

HiMe is a **single-agent, event-driven** system. One agent processes work items sequentially from priority queues — no dual loops, no LLM lock contention.

```
┌─────────────┐  WatchConnectivity  ┌─────────────┐  WS / HTTP  ┌─────────────────────┐
│ Apple Watch │ ─────────────────▶  │  iOS App    │ ──────────▶ │  WatchExporter      │
│  (HealthKit)│ ◀─────────────────  │  (SwiftUI)  │  samples    │  (aiohttp :8765)    │
└─────────────┘  cat-state updates  └──────┬──────┘             └─────────┬───────────┘
                                           │ chat + agent stream           │ WatchDBReader
                                           │ (POST /chat, WS /stream)      ▼
┌─────────────┐    REST + WebSocket    ┌──────────────────────────────────────────────┐
│  React SPA  │ ◀─────────────────────▶│  FastAPI Backend (:8000)                     │
│  (Vite      │   dashboard / monitor  │                                              │
│   :5173)    │   reports / config     │   ┌────────────────────────────────────┐     │
└─────────────┘                        │   │  AutonomousHealthAgent             │     │
                                       │   │  • Chat queue (shared InboxQueue)  │     │
┌─────────────┐     Bot APIs           │   │  • Analysis queue (cron + trigger) │     │
│  Telegram / │ ◀─────────────────────▶│   │  • Chat: orchestrator + subs       │     │
│  Feishu /   │   optional IM chat     │   └────────────────────────────────────┘     │
│  WeChat     │                        │   EventHub (fan-out agent events to N WS)    │
└─────────────┘                        │   Cron / TriggerEvaluator / FactVerifier     │
                                       │   Background data ingestion (independent)    │
                                       └──────────────────────────────────────────────┘
```

**In-app iOS chat** (default, `IOS_GATEWAY_ENABLED=true`): the iPhone app sends messages with `POST /api/agent/chat` and receives streaming replies on `WS /api/stream/agent/LiveUser?client=ios`. Outbound agent text is emitted as `chat_reply` / `chat_image` events on that socket; when the app is backgrounded (socket closed), optional APNs alerts (`APNS_*` + `aioapns`) nudge the user. Conversation scrollback is stored in the memory DB (`chat_history` table, key `ios:LiveUser`) and loaded via `GET /api/agent/chat-history`.

The agent is built on three design principles: (1) single-agent sequential execution with no lock contention, (2) event-driven work via priority queues (chat > trigger > cron), and (3) layered prompts assembled from `prompts/` files at runtime. For provider-specific quirks, see the individual modules under `backend/agent/llm/`.

---

## How to add a new tool

1. Create `backend/agent/tools/your_tool.py` subclassing `BaseTool` (`backend/agent/tools/base.py`).
   - Override `name: str`, `get_definition() -> dict`, and `async execute(**kwargs) -> dict`.
   - The `get_definition()` payload should match OpenAI/Anthropic function-calling schema.
2. Add the JSON schema to `backend/agent/tools/tools.json` so multi-provider call sites pick it up.
3. Register the tool in `backend/agent/tools/registry.py` (`ToolRegistry.with_default_tools`).
4. If the tool is mode-specific (analysis / chat / quick), add it to the relevant set in `backend/agent/agent_tools.py`.
5. Write a test in `tests/` exercising the tool against a fake DataStore + memory DB.

The agent will pick the new tool up automatically on next start; no prompt changes needed unless you want to advertise it.

## How to add a new LLM provider

1. Add a module under `backend/agent/llm/your_provider.py` implementing `BaseLLMProvider` (see `backend/agent/llm_providers.py` for the abstract class).
2. Decorate it with `@register_provider("your_provider")` so the factory finds it.
3. Add API-key resolution in `backend/agent/llm/__init__.py` (`get_env_api_key`).
4. Update `.env.example` with the new env vars. Document any unusual quirks (XML tool calls, response schema, etc.) in a docstring at the top of the provider module.
5. Test with the simplest call path: chat completion with one tool call.

## How to add a personalised page (manually)

Personalised pages are normally created by the agent at runtime via the `create_page` tool, but you can also drop a static page in `data/personalised_pages/<page_id>/`:

- `index.html` — vanilla-JS SPA. Fetches from `/api/personalised-pages/<page_id>/data`.
- `route.py` — must define `route_handler(request) -> dict`. Helpers (`query_health`, `query_memory`, `write_memory`, `ensure_table`) are auto-injected.

Then register it in `personalised_pages` table or restart the agent to rediscover.

Security model: agent-generated `route.py` is validated against a denylist of imports + sandbox-escape tokens (see `backend/agent/tools/create_page_tool.py`). Generated HTML is served with a strict CSP (`default-src 'self'`, `frame-ancestors 'none'`), so even malicious inline JS cannot exfiltrate data to a third-party origin.

---

## open-wearables integration (optional, experimental)

`backend/data_sources/open_wearables/` connects HiMe to a separately self-hosted [open-wearables](https://github.com/open-wearables/open-wearables) instance, which unifies Garmin/Oura/Whoop/Polar/... wearable data behind one API. It is entirely off by default (`OPENWEARABLES_ENABLED=false`) — when off, no background task starts and no network call is made.

**Module layout:**

| Module | Responsibility |
|--------|-----------------|
| `client.py` | Async httpx client for the open-wearables `/api/v1` REST surface (users, providers/OAuth, timeseries with cursor pagination, workouts/sleep events, sync-status, webhook endpoint CRUD). Never raises — logs and returns `None`/`[]` on any failure. The three paginated list methods (`get_timeseries`, `list_workouts`, `list_sleep_sessions`) return a `PageResult(items, complete)`: `complete=False` on a transport error/non-2xx/timeout *or* on hitting the `max_pages` safety cap, so a caller never mistakes a truncated or failed fetch for "no data" — see `poller.py`. |
| `features.py` | `SERIES_TYPE_MAP` (open-wearables `SeriesType` → existing HiMe `feature_type`, for metrics HiMe already tracks from Apple Watch) + `SERIES_TYPE_CONVERSION` (unit multipliers where OW's and HiMe's *storage* units differ, e.g. `oxygen_saturation` 0-100 → HiMe's `blood_oxygen` 0-1 fraction) + `OW_FEATURE_SPEC` (display/aggregation specs, same schema as `apple_health_features.FEATURE_SPEC`, for OW-only metrics like `garmin_body_battery` or `blood_glucose`). |
| `mapper.py` | Pure functions: OW payload → `DataStore.ingest_batch()` rows. Handles timestamp normalization (OW's offset-aware ISO strings → HiMe's naive UTC `YYYY-MM-DDTHH:MM:SS` convention, matching `backend/utils.py::ts_fmt` and `agent_lifecycle._live_ingest_loop`), sleep-session → `sleep_*` stage samples (anchored at segment/session **end** time; a staged session emits only its stage rows, never the session total alongside them — matches `prompts/data_schema.md`'s total-sleep convention), workout → `workout_<category>_{duration,distance,energy}` samples, `is_daily_total` handling (granular samples supersede a same-batch daily total for the same feature/provider/day; a surviving daily total is written at a deterministic day-anchor timestamp with a `daily_total` metadata marker), and provider filtering (drops `apple`/`apple_health` by default so the native Apple Watch pipeline stays authoritative; respects `OPENWEARABLES_PROVIDERS` otherwise). No I/O, fully unit-testable. |
| `poller.py` | Background asyncio loop, started from `main.py`'s lifespan only when enabled. Resolves/creates the OW user on first run, then runs two-tier polling: a **fast poll** every `OPENWEARABLES_POLL_INTERVAL`s using a persisted per-category event-time cursor (small overlap; advances only on a complete fetch, holds back on error, advances to the last-fetched sample's timestamp on a truncated fetch) that backfills `OPENWEARABLES_BACKFILL_DAYS` on first run, plus a **reconciliation pass** every `OPENWEARABLES_RECONCILE_INTERVAL`s that re-scans the trailing `OPENWEARABLES_RECONCILE_WINDOW_HOURS` independent of the cursors (catches data that syncs into OW hours after its own event time — idempotent upserts make the overlap free). Optionally triggers reconciliation early when `GET .../sync/runs` reports a newly-completed sync run. State lives in `app_state.json`, key `"openwearables"` (same mechanism `backend/api/config_routes.py` uses for the rest of app state). Also makes one best-effort attempt to self-register a webhook endpoint at startup. |
| `webhook.py` | `POST /api/integrations/openwearables/webhook` — verifies the inbound Svix signature by hand (HMAC-SHA256 over `{id}.{timestamp}.{raw_body}`, stdlib `hmac`/`hashlib`/`base64`, no `svix` pip dependency) against the persisted signing secret, then maps + ingests. Always answers fast and never 500s on a bad payload (auth failures are the only non-2xx). Exempted from `BearerAuthMiddleware` the same way the Feishu webhook is. |
| `routes.py` | `GET /status`, `GET /providers`, `POST /connect/{provider}`, `POST /sync` — consumed by the frontend Devices page. Ordinary `/api/*` routes, no special auth handling needed. |

**Data flow:** open-wearables REST/webhook → `mapper.py` → `DataStore.ingest_batch()` → the same `samples` table (`data/data_stores/LiveUser_data.db`) the Apple Watch live-ingest loop writes to. Dashboard, agent context, and retention all read that one table, so OW-sourced data shows up everywhere native data does, with no separate code path downstream. Webhook and poll ingestion overlap harmlessly — `ingest_batch()`'s upsert on `(timestamp, feature_type)` makes re-ingesting the same sample a no-op.

Feature display metadata is merged into the existing `GET /api/data/feature_metadata` response (`OW_FEATURE_SPEC` + `FEATURE_SPEC`), so new OW-only metrics get dashboard cards automatically.

---

## Configuration reference

All settings live in `.env`. The most important ones:

| Variable | Default | Purpose |
|----------|---------|---------|
| `DEFAULT_LLM_PROVIDER` | `gemini` | Active provider. Must match a key in `backend/agent/llm/`. |
| `DEFAULT_MODEL` | `gemini-3-flash-preview` | Model name for the active provider (must match the chosen provider). |
| `AGENT_MAX_ITERATIONS` | `100` | Hard cap on tool-calling loop iterations per analysis. |
| `AGENT_MAX_TOKENS` | `0` | Max tokens per LLM response (`0` = provider default). |
| `AGENT_CONTEXT_WINDOW_SIZE` | `20` | Sliding window of turn groups kept in full context. |
| `CHAT_MAX_TURNS` | `20` | Hard cap on chat orchestrator iterations. |
| `CODE_TOOL_DOCKER_SANDBOX` | `false` | Run agent-generated Python in a Docker container instead of in-process. |
| `CORS_ORIGINS` | `["http://localhost:5173", "http://localhost:3000"]` | Trusted browser origins. |
| `API_AUTH_TOKEN` | _(empty)_ | If set, all `/api/*` routes require `Authorization: Bearer <token>`. Leave empty for localhost. |
| `TELEGRAM_GATEWAY_ENABLED` | `false` | Turn on bidirectional Telegram chat. |
| `TELEGRAM_ALLOWED_CHAT_IDS` | _(empty)_ | Comma-separated whitelist. **Empty = deny all inbound** (default-deny). |
| `FEISHU_GATEWAY_ENABLED` | `false` | Turn on bidirectional Feishu chat. |
| `WEIXIN_GATEWAY_ENABLED` | `false` | Turn on the WeChat ClawBot gateway. Requires a one-time `python -m backend.weixin.qr_login` to issue the bot_token. |
| `WEIXIN_ALLOWED_USER_IDS` | _(empty)_ | Comma-separated whitelist. **Empty = trust the QR scanner** (allow-all is safe here because ClawBot has no shareable handle). |
| `IOS_GATEWAY_ENABLED` | `true` | Native in-app chat channel (no bot binding). Set `false` if you only use IM gateways. |
| `IOS_VISION_ENABLED` | `false` | Accept inbound images on `POST /api/agent/chat` (needs a vision-capable LLM). |
| `IOS_MAX_IMAGE_BYTES` | `5242880` | Max inbound image size (bytes) for in-app chat uploads. |
| `APNS_ENABLED` | `false` | Proactive push when the iOS app has no live stream socket. Requires `aioapns` + `.p8` key. |
| `CHAT_HISTORY_SIZE` | `20` | In-memory sliding window per chat key (LLM context). |
| `AUTO_RESTORE_AGENT` | `false` | Re-launch the last running agent on backend startup. |

For the complete list, see [`.env.example`](../.env.example).

---

## Running the test suite

```bash
python -m pytest tests/ -x -q
```

Frontend smoke build:

```bash
cd frontend && npm run build
```

Lint:

```bash
ruff check backend/ tests/
```

CI runs all three on every PR (see `.github/workflows/ci.yml`).

---

## Code style

- **Python**: 4-space indent, line length 100, ruff-managed (`pyproject.toml`). Type hints on all new functions and classes.
- **Frontend**: Plain JSX (no TypeScript yet), 2-space indent, single quotes, no semicolons, Prettier-compatible (`frontend/.prettierrc.json`).
- **Swift**: Standard SwiftUI conventions; avoid force-unwraps unless invariants are clearly documented.
- **Async I/O everywhere** in the backend. Use `asyncio.to_thread()` for blocking SQLite / pandas work.
- **Logging**, not `print()`. Logging is configured in `backend/logging_config.py`.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `./hime.sh start` says "port 8000 in use" | Old backend / something else on the port | `./hime.sh stop`, then `lsof -i :8000` to find the offender. |
| Agent starts but produces no output | Missing or invalid LLM API key | Check `logs/backend.log` for `validate_llm_keys` warnings. |
| `Telegram: rejecting message from non-whitelisted chat ...` | `CHAT_ID` and `TELEGRAM_ALLOWED_CHAT_IDS` are both empty | Add your chat ID to `.env`. |
| `401 Unauthorized` from API calls | `API_AUTH_TOKEN` is set but the SPA isn't sending it | Either unset the token (localhost dev) or configure the SPA to inject the bearer header. |
| iOS app builds but won't run on a real device | `Config.xcconfig` missing or empty `DEVELOPMENT_TEAM` | Edit `ios/hime/Config.xcconfig`. |
| Watch app shows "—" for all metrics | HealthKit permissions not granted on the watch | Re-run the iOS app and accept all HealthKit prompts. |
| iOS Chat sends but no reply streams | Agent not running or stream not connected | Start agent; confirm `WS /api/stream/agent/LiveUser?client=ios` in logs; check `API_AUTH_TOKEN` matches app Settings. |
| Duplicate bubbles after iOS reconnect | Wrong WebSocket client marker | App must use `?client=ios` (see `ChatStreamClient.swift`). |

For anything else, check `logs/backend.log` first — the agent logs every tool call, LLM round-trip, and supervisor restart.

---

## Where to ask questions

- **Bugs / feature requests**: open a GitHub issue.
- **Security disclosures**: see [`SECURITY.md`](../SECURITY.md). Do not file public issues.
- **Discussion**: GitHub Discussions (if enabled on the repo).
