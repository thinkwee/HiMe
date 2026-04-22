# HiMe — Detailed Install Guide

The README's Quick Start covers the happy path. This document covers everything else: manual Docker setup, native dev install, public deployment, IM gateway details, iOS build from source, and customization.

## Table of Contents

- [Manual Docker setup](#manual-docker-setup)
- [Native dev install](#native-dev-install)
- [Public deployment](#public-deployment)
- [IM gateway setup](#im-gateway-setup)
  - [Telegram](#telegram)
  - [Feishu (Lark)](#feishu-lark)
- [iOS app](#ios-app)
- [Customization](#customization)
  - [Switching LLM providers](#switching-llm-providers)
  - [Fallback provider chain](#fallback-provider-chain)
  - [Reasoning effort (GPT-5 family)](#reasoning-effort-gpt-5-family)
  - [Skills](#skills)
  - [Personalised pages](#personalised-pages)
- [Troubleshooting](#troubleshooting)

## Manual Docker setup

If you want full control over the wizard's choices, edit `.env` directly:

```bash
git clone https://github.com/thinkwee/HiMe.git HiMe
cd HiMe
cp .env.example .env
# Edit .env and at minimum set DEFAULT_LLM_PROVIDER + the matching *_API_KEY,
# and the Telegram or Feishu gateway block.
docker compose up --build -d
```

Endpoints when ready:

- Dashboard: http://localhost:5173
- Backend API: http://localhost:8000
- Watch exporter: http://localhost:8765/ping

## Native dev install

For developers iterating on the code (Python venv + Vite dev server, no Docker):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
cp .env.example .env  # configure
./hime.sh start  # starts backend + frontend + watch exporter
```

`./hime.sh` is the developer's native-mode CLI; full reference: `./hime.sh help`. For backend-only work, `python -m backend.main` runs the API server in isolation; set `HIME_DEV_RELOAD=true` to enable Uvicorn auto-reload. The frontend lives in `frontend/` (`npm install && npm run dev`). See [`docs/DEVELOPMENT.md`](DEVELOPMENT.md) for full developer onboarding.

## Public deployment

Mostly defer to [`docs/DEPLOYMENT.md`](DEPLOYMENT.md), but the critical steps are:

1. Set a strong `API_AUTH_TOKEN` in `.env` (`openssl rand -hex 32`).
2. Add your public origin to `CORS_ORIGINS=`.
3. Reverse-proxy 8000 + 8765 behind nginx/Caddy with TLS — do NOT expose backend ports directly.
4. Configure the iOS app with the same `API_AUTH_TOKEN` in Settings → Auth Token.

Full nginx + Caddy examples: [`docs/DEPLOYMENT.md`](DEPLOYMENT.md).

## IM gateway setup

HiMe chats with you exclusively through an IM gateway. The web dashboard does not have a chat UI. Pick Telegram or Feishu (or set up both, but most people pick one).

### Telegram

1. Open [@BotFather](https://t.me/BotFather) → `/newbot` → follow prompts → save the bot **token**.
2. Open [@userinfobot](https://t.me/userinfobot) → `/start` → save the **chat_id** (numeric).
3. Send `/start` to your new bot once so Telegram allows the bot to message you back.
4. In `.env`:

   ```bash
   TELEGRAM_GATEWAY_ENABLED=true
   TELEGRAM_TOKEN=<bot_token>
   CHAT_ID=<chat_id>
   TELEGRAM_ALLOWED_CHAT_IDS=<chat_id>
   ```

### Feishu (Lark)

1. Visit [open.feishu.cn](https://open.feishu.cn) → "开发者后台" → "创建企业自建应用".
2. After creation: "凭证与基础信息" → save **APP_ID** (`cli_...`) and **APP_SECRET**.
3. "权限管理" → grant `im:message`, `im:message:send_as_bot`, `im:chat`, `im:chat:readonly` (and others as needed).
4. "事件订阅" → choose "长连接" (long-poll WebSocket) — no public URL needed.
5. Publish the app draft and add it to a group chat.
6. Get the **open_chat_id** (`oc_...`) by inviting the bot to a group and querying `/open-apis/im/v1/chats` with the bot token (or check the message events the bot receives).
7. In `.env`:

   ```bash
   FEISHU_GATEWAY_ENABLED=true
   FEISHU_APP_ID=cli_xxx
   FEISHU_APP_SECRET=...
   FEISHU_DEFAULT_CHAT_ID=oc_xxx
   FEISHU_ALLOWED_CHAT_IDS=oc_xxx
   FEISHU_TRANSPORT=ws
   ```

## iOS app

### Easy path

Install [HiMe on the App Store](https://apps.apple.com/app/id6762160735). Open the app → Settings → enter your Server URL.

`Server URL` accepts:

- `localhost` — iPhone simulator on the same Mac running the backend.
- `192.168.1.100` — Mac/Linux backend on the same Wi-Fi LAN.
- `homelab.local` — mDNS hostname on the same LAN (works if the host publishes via Avahi/Bonjour).
- `example.com` — your public domain (with `https://api.example.com` and `wss://watch.example.com` derived automatically; requires reverse-proxy setup from [`docs/DEPLOYMENT.md`](DEPLOYMENT.md)).

If `API_AUTH_TOKEN` is set on the server, paste the same value into Settings → Auth Token.

### Build from source

1. Open `ios/hime/hime.xcodeproj` in Xcode 16+.
2. `cp ios/hime/Config.xcconfig.template ios/hime/Config.xcconfig` and fill in `DEVELOPMENT_TEAM` (your Apple team ID) and `BUNDLE_ID_PREFIX` (e.g. `com.example`).
3. ⌘R to build and run.

## Customization

### Switching LLM providers

`.env` controls everything:

```bash
DEFAULT_LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-...
DEFAULT_MODEL=claude-sonnet-4-6   # optional override; omit to use provider mid-tier default
```

Supported providers (matches `backend/agent/llm/__init__.py`): `gemini`, `openai`, `azure_openai`, `anthropic`, `mistral`, `groq`, `deepseek`, `xai`, `openrouter`, `perplexity`, `google_vertex`, `amazon_bedrock`, `minimax`, `vllm`, `zhipuai`.

Each has a default model in `.env.example` under "Per-provider default models" — uncomment to override the default.

### Fallback provider chain

To automatically retry against a backup provider when the primary returns 503/529/overloaded three times in a row:

```bash
FALLBACK_LLM_PROVIDER=openai
FALLBACK_LLM_MODEL=gpt-5.4-mini
```

### Reasoning effort (GPT-5 family)

```bash
OPENAI_REASONING_EFFORT=low   # minimal | low | medium | high | xhigh | none
```

For agentic tool-calling, recommended `low` or `minimal`. Default `medium` is overkill for HiMe's loops.

### Skills

Skills are reusable analysis playbooks (`.md` files) under `./skills/`. The registry also auto-scans `~/.hime/skills` so you can add personal skills outside the repo. Toggle individual skills via the dashboard's Skills tab.

### Personalised pages

The agent can generate single-page apps on demand using the `create_page` tool. Pages live under `data/personalised_pages/<page_id>/` and use the bundled `HimeUI` JS component library at `data/personalised_pages/_shared/`. Spec: [`prompts/create_page_guide.md`](../prompts/create_page_guide.md).

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `setup.sh: Docker daemon not running` | Docker Desktop not started | Start Docker Desktop and re-run `./setup.sh` |
| `setup.sh` finishes but nothing responds | Containers crashed | `docker compose logs -f --tail=100` |
| iOS app shows "Cannot connect" | Wrong Server URL or firewall | `curl http://<host>:8765/ping` from another LAN device |
| `401 Unauthorized` from API | `API_AUTH_TOKEN` mismatch | Set the same value in iOS app's Settings → Auth Token |
| Telegram bot silent | `TELEGRAM_ALLOWED_CHAT_IDS` empty | Add your chat_id to the allowlist (setup.sh handles this) |
| Feishu card buttons do nothing | Feishu Card Request URL not set | Set the public callback URL in Feishu console — see [`docs/DEPLOYMENT.md`](DEPLOYMENT.md) |

For deeper debugging see logs in `./logs/backend.log` (or `docker compose logs backend`).
