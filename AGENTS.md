# AGENTS.md

A concise guide for AI coding agents (and new human contributors) working on HiMe. Read this first; it will save you from re-deriving the design from scratch.

For *setup* and *troubleshooting* details, see [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md). This file is about how the system is shaped and how to extend it without fighting the grain.

---

## What HiMe is

A self-hosted, local-first health AI platform. An Apple Watch + iPhone continuously stream HealthKit samples into a SQLite store; an autonomous agent (one of 15+ LLM providers) analyses the data on cron schedules and event triggers, and delivers reports back to the user via Telegram or Feishu. A React dashboard and iOS companion app surface state and allow manual interaction.

Tagline: *Say Hi to Healthy Me*.

---

## The three Agent Design Principles

Every backend change should respect these. The PR template enforces it.

1. **Single-agent, sequential execution — no LLM lock contention.** One `AutonomousHealthAgent` instance processes all work items one at a time. Do not introduce parallel agents, thread pools that share the LLM, or background tasks that call the model concurrently. If you need concurrency, it lives in ingestion, transport, or I/O — never in the reasoning loop.

2. **Event-driven work via priority queues (chat > trigger > cron).** The agent's `run_forever()` loop drains three queues in order: user chat messages first, then evaluated triggers, then scheduled analyses. New kinds of work should become a new queue consumer with an explicit priority, not a new loop.

3. **Layered prompts assembled from `prompts/` at runtime.** The system prompt is not hardcoded — it is composed from `soul.md` (identity), `rules_chat.md` (behaviour), `data_schema.md`, `memory_guide.md`, and user-specific `user.md`/`experience.md` that the agent appends to over time. New prompt content belongs as a new markdown file in `prompts/`, loaded via `prompt_loader.py` and referenced from `context_manager.py`.

---

## Repository layout

```
backend/          FastAPI server + agent engine (Python 3.10+)
  main.py           App + lifespan + cron scheduler
  config.py         Pydantic settings (from .env)
  agent/            Single-agent core
    autonomous_agent.py   State + run_forever() event loop
    agent_loops.py        Analysis + chat loop implementations
    agent_tools.py        Per-mode tool sets (analysis/chat/quick)
    agent_prompts.py      Layered prompt assembly
    llm_providers.py      Provider factory (15+ providers)
    llm/                  Per-provider modules (anthropic, openai, gemini, bedrock, zhipuai, ...)
    tools/                BaseTool subclasses (sql, code, reply_user, push_report, create_page, read_skill, update_md)
    skills/               Skill discovery + loading
    memory_manager.py     SQLite wrapper for reports, activity_log, scheduled_tasks, trigger_rules
    data_store.py         Health data ingestion SQLite
    context_manager.py    Runtime prompt + memory assembly
    trigger_evaluator.py  Event-triggered analysis
    fact_verifier.py      Evidence check on outbound messages
    retention.py          Daily rolling-window data pruning
  api/              FastAPI routers (agent, tasks, data, pages, prompts, skills, stream)
  data_readers/     Health data ingestion (Apple Watch DB, feature type defs)
  messaging/        Platform-agnostic gateway abstraction (registry, inbox, command parser)
  telegram/         Telegram gateway implementation
  feishu/           Feishu (Lark) gateway implementation
  services/         WebSocket streaming, connection manager
  i18n/locales/     en.json + zh.json (backend strings)
frontend/         React 18 + Vite + Tailwind SPA
  src/pages         Route pages (Dashboard, Reports, Tasks, Settings, Onboarding)
  src/components    Reusable UI
  src/i18n          react-i18next locales (en, zh)
ios/              Native Swift/SwiftUI
  hime/hime/        iPhone app (HealthKit, WebSocket, chat UI, cat mascot)
  hime/himewatch/   watchOS companion
  hime/HimeWidgets  iPhone widgets
  hime/HimeWatchWidgets  watchOS widgets
  Server/           WatchExporter (aiohttp :8765 — parses Watch.db to backend)
prompts/          Layered prompt markdown (soul, experience, user, rules_chat, ...)
skills/           User-authored analysis playbooks (markdown + YAML frontmatter)
docker/           Docker build context
data/, memory/, logs/   Runtime state (all gitignored)
tests/            pytest suite (13 test files)
hime.sh           Unified dev CLI (start, stop, restart, logs, status, reset)
setup.sh          Interactive first-run wizard (.env generation, docker build)
```

---

## How things flow

```
Apple Watch → iOS App → WatchExporter (:8765) → backend.data_store (SQLite)
                                                          │
                                   ┌──────────────────────┴────────────────────┐
                                   │            AutonomousHealthAgent          │
                                   │  run_forever():                           │
                                   │   1. chat queue (InboxQueue, any gateway) │
                                   │   2. trigger queue (TriggerEvaluator)     │
                                   │   3. cron queue (scheduled_tasks)         │
                                   │                                           │
                                   │  per work item → agent_loops.*()          │
                                   │    → LLM call (provider from llm/)        │
                                   │    → tool_calls → tools/*.execute()       │
                                   │    → FactVerifier on outbound reply       │
                                   │    → GatewayRegistry.send()               │
                                   └───────────────────────────────────────────┘
                                                          │
                     ┌────────────────────────────────────┼─────────────────┐
                     ▼                                    ▼                 ▼
                  React SPA                     Telegram / Feishu       iOS App
                  (dashboard,                   (chat + push reports)   (live status,
                   reports, config)                                      cat state)
```

---

## Common tasks

### Add a new tool

1. Create `backend/agent/tools/your_tool.py` subclassing `BaseTool` from `backend/agent/tools/base.py`. Override `name`, `get_definition() -> dict` (OpenAI function-calling schema), and `async execute(**kwargs) -> dict`.
2. Add the JSON schema entry to `backend/agent/tools/tools.json` so multi-provider call sites pick it up.
3. Register in `backend/agent/tools/registry.py` (`ToolRegistry.with_default_tools`).
4. If the tool is mode-specific (analysis / chat / quick), add it to the relevant set in `backend/agent/agent_tools.py`.
5. Write a test in `tests/` against a fake DataStore + memory DB.

The agent picks it up on next start. No prompt edits required unless you want to advertise it.

### Add a new LLM provider

1. Add `backend/agent/llm/your_provider.py` implementing `BaseLLMProvider` (see `backend/agent/llm_providers.py` for the abstract).
2. Decorate with `@register_provider("your_provider")`.
3. Add API-key resolution in `backend/agent/llm/__init__.py` (`get_env_api_key`).
4. Add the env var + default model to `.env.example` and `config.py`.
5. Test chat completion with one tool call against the real provider before merging.

Document provider quirks (XML tool calls, non-standard streaming, thinking-budget clamps) in a module docstring — they will bite the next person otherwise.

### Add a new skill

Skills are user-facing analysis playbooks, not code. Create `skills/your_skill.md`:

```markdown
---
description: One-line summary the agent reads to decide if this is relevant
---
# Steps
1. Use sql_tool to fetch ...
2. Use code_tool to analyse ...
3. Use reply_user or push_report ...
```

Rules: name must match `^[a-z0-9_-]+$`, file ≤ 256 KB, first-root-wins across `./skills/` and bundled defaults. The agent loads it on demand via `ReadSkillTool`.

### Add a new prompt layer

Drop the markdown in `prompts/`, then wire it into `context_manager.py` so it is appended to the system prompt at the right position. Keep additive; never inline user-specific content into the bundled files — that's what the agent-maintained `user.md` / `experience.md` are for.

### Add a new personalised page (manually)

Pages are normally generated by the agent via `create_page_tool`, but you can drop a static one in `data/personalised_pages/<page_id>/`:

- `index.html` — vanilla-JS SPA. Fetch from `/api/personalised-pages/<page_id>/data`.
- `route.py` — must define `route_handler(request) -> dict`. Helpers (`query_health`, `query_memory`, `write_memory`, `ensure_table`) are auto-injected.

Agent-generated `route.py` is validated against a denylist; generated HTML is served under a strict CSP. Don't loosen this without thinking hard about data exfiltration.

---

## Conventions

- **Python**: ruff-managed, line length 100, 4-space indent, type hints on all new functions/classes. Async/await everywhere in the backend — use `asyncio.to_thread()` for blocking SQLite / pandas work.
- **Frontend**: plain JSX (no TypeScript yet), 2-space indent, single quotes, no semicolons, Prettier-managed.
- **Swift**: standard SwiftUI conventions; avoid force-unwraps unless an invariant makes it safe.
- **Logging** (never `print()`). Configured in `backend/logging_config.py`. The agent logs every tool call, LLM round-trip, and supervisor restart to `logs/backend.log`.
- **Time** is always tz-aware in user-facing logic. SQLite columns are stored as UTC ISO strings (`strftime('%Y-%m-%dT%H:%M:%S','now')`). Cron expressions in `scheduled_tasks` and prompt timestamps are interpreted in `settings.TIMEZONE` (any IANA name, default `UTC`) — never the container's system clock. Use `backend/utils.py` helpers (`now_utc`, `now_local`, `parse_db_iso_utc`, `app_timezone`) instead of bare `datetime.now()` for new scheduling/display code.
- **Commit messages**: short imperative-mood subjects, lowercase, no trailing period. Body optional but encouraged for non-trivial changes — explain *why*.
- **Tests**: pytest + pytest-asyncio (auto mode). Fixtures in `tests/conftest.py` give you mock settings, temp DBs, a fake LLM provider, and a FastAPI test client. No test is allowed to make a real LLM call.

---

## Hard rules (don't break these)

- **`.env` is gitignored.** Never commit real keys. `.env.example` is the tracked template; keep it in sync when you add a new setting.
- **`ios/hime/Config.xcconfig` is gitignored.** Contains the developer's Apple Team ID + bundle prefix. Use `Config.xcconfig.template` as the reference; never inline a real Team ID into `project.pbxproj`. `project.pbxproj` deliberately omits `DEVELOPMENT_TEAM` so xcconfig can drive it via `baseConfigurationReference` — do **not** change the Team in Xcode's *Signing & Capabilities* UI (Xcode will write the Team ID back into pbxproj); edit `Config.xcconfig` instead.
- **i18n is dual-maintained.** Any user-facing string added to the backend must go in both `backend/i18n/locales/en.json` and `zh.json`. Frontend equivalent: `frontend/src/i18n/`.
- **Secrets never enter tests.** `conftest.py` sets `GEMINI_API_KEY=test-placeholder`. If your test needs to exercise a provider, mock it.
- **Runtime state is gitignored.** `logs/`, `memory/`, `data/` — keep it that way. Seed data goes in `tests/` fixtures, not checked-in SQLite files.
- **No tool retry on failure.** If a tool call raises, the agent extracts the error text as an auto-reply and moves on. Don't add retry loops inside tools — it breaks the cost model and hides bugs.
- **LLM fallback is the only retry.** Three consecutive 503/529/overloaded responses swap to `FALLBACK_LLM_PROVIDER`. That path lives in `llm_providers.py`. Don't reimplement it elsewhere.

---

## Running the suite

```bash
python -m pytest tests/ -x -q         # Backend tests
cd frontend && npm run build          # Frontend smoke build
ruff check backend/ tests/            # Lint (must be clean)
```

CI (`.github/workflows/ci.yml`) runs all three on every PR against Python 3.10 / 3.11 / 3.12 and Node 20.

---

## When in doubt

- **Setup / install problems** → `docs/DEVELOPMENT.md`, `docs/INSTALL.md`
- **Production deployment** → `docs/DEPLOYMENT.md`
- **All config options** → `.env.example` (it is the canonical list)
- **Security scope + disclosure** → `SECURITY.md`
- **Runtime logs** → `logs/backend.log` (tool calls, LLM round-trips, errors — everything)
