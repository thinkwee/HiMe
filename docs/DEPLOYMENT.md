# HiMe Deployment Guide

This document covers deploying HiMe beyond the single-machine "Quick start" flow in the README. Three deployment shapes are described, in order of complexity:

1. **Local / LAN** — run HiMe on a desktop or home server and access it from an iPhone / Apple Watch on the same network. Zero TLS, zero public endpoint.
2. **Public internet** — expose HiMe behind a reverse proxy with TLS, authentication, and CORS, so that you can reach it from anywhere.
3. **Docker Compose production** — run the three services as long-lived containers with mounted volumes.

Each shape builds on the previous one. Pick the lowest shape that meets your needs.

## Deployment Methods

HiMe supports two deployment methods. **Choose one -- do not run both simultaneously** as they bind the same ports (8000, 5173, 8765).

- **Docker Compose** (recommended for most users): `docker compose up --build`
- **Native processes** (for development): `./hime.sh start`

> If you want to expose HiMe outside your LAN without running a reverse proxy yourself, you can use **any** tunneling solution of your choice (ngrok, tailscale, cloudflared, frp, bore, etc.). HiMe does not endorse or require any specific vendor — treat the tunnel as opaque transport in front of the reverse-proxy configuration below.

---

## 1. Local / LAN deployment

This is the target shape for most personal users. HiMe runs on one machine and your iPhone connects to it over your home Wi-Fi.

### 1.1. Choose a stable LAN address

Give the host a predictable IP. Either reserve a DHCP lease in your router or assign a static IP on the host itself. Record it somewhere; you will type it into the iPhone app.

```bash
# Example on Linux:
ip -4 addr show scope global | awk '/inet / {print $2}'
# -> 192.168.1.100/24
```

### 1.2. Open the right ports on the host firewall

HiMe uses three TCP ports on the host:

| Service        | Port | Protocol | Purpose                                   |
|----------------|------|----------|-------------------------------------------|
| Backend API    | 8000 | HTTP/WS  | FastAPI + agent WebSocket event stream    |
| Frontend SPA   | 5173 | HTTP     | Vite dev server (React dashboard)         |
| Watch Exporter | 8765 | HTTP/WS  | iPhone / Watch health-data ingestion      |

On Linux with `ufw`:

```bash
sudo ufw allow from 192.168.1.0/24 to any port 8000 proto tcp
sudo ufw allow from 192.168.1.0/24 to any port 5173 proto tcp
sudo ufw allow from 192.168.1.0/24 to any port 8765 proto tcp
```

On macOS, open **System Settings → Network → Firewall** and allow `python3` and `node` to accept incoming connections, or disable the firewall on your trusted LAN.

### 1.3. Point the iPhone app at the host

1. Build and install the iPhone app (`ios/hime/hime.xcodeproj`) to your device.
2. Open the HiMe app → **Settings** → **Server URL**.
3. Enter the host IP **without a scheme or port** (e.g. `192.168.1.100`). The app appends `:8765` for the Watch Exporter and `:8000` for the backend automatically.
4. Grant HealthKit permission when prompted.

### 1.4. mDNS (optional)

If your host publishes a `.local` name via Avahi / Bonjour, you can type that into the iPhone app instead of a raw IP (for example `homelab.local`). This is more resilient to DHCP changes. Many consumer routers block mDNS by default; if the app cannot resolve the name, fall back to the IP.

### 1.5. Verify

From a second machine on the same LAN:

```bash
curl http://192.168.1.100:8000/health
curl http://192.168.1.100:8765/ping
```

Both should respond. If either fails, re-check the firewall rules and that `./hime.sh status` shows all three services running.

---

## 2. Public internet deployment

Use this shape if you want HiMe to be reachable from outside your LAN. The only supported topology is **reverse proxy in front of the backend**, with TLS terminated at the proxy. Do **not** expose the FastAPI server directly to the public internet.

### 2.1. DNS and TLS prerequisites

- A DNS name pointing at the proxy host (for example `hime.example.com`).
- Inbound TCP 80 and 443 reachable on the proxy host.
- A TLS certificate. Both examples below use Let's Encrypt.

### 2.2. Set an API auth token

Before you open HiMe to the internet, set a long random bearer token in `.env`:

```bash
API_AUTH_TOKEN=$(openssl rand -hex 32)
```

With this set, the `BearerAuthMiddleware` in `backend/main.py` rejects every `/api/*` and `/ws/*` request that does not carry `Authorization: Bearer <token>`. Browser WebSocket clients can pass the token via `?token=...` in the query string. Save the same token in your iOS app settings and in your frontend configuration.

### 2.3. CORS

If your frontend is served from a different origin than the API (for example `app.example.com` vs `api.example.com`), set the allowed origins in `.env`:

```bash
CORS_ORIGINS='["https://app.example.com"]'
```

Use JSON array format. Example with multiple origins:
`CORS_ORIGINS='["https://app.example.com", "https://admin.example.com"]'`.
Leave this unset to use defaults for local development.

### 2.4. Example: nginx

```nginx
server {
    listen 443 ssl http2;
    server_name hime.example.com;

    ssl_certificate     /etc/letsencrypt/live/hime.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/hime.example.com/privkey.pem;

    # FastAPI + /ws/* WebSockets
    location / {
        proxy_pass         http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header   Upgrade           $http_upgrade;
        proxy_set_header   Connection        "upgrade";
        proxy_set_header   Host              $host;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 3600s;
    }

    # Watch Exporter (ingestion)
    location /ingest/ {
        proxy_pass         http://127.0.0.1:8765/;
        proxy_http_version 1.1;
        proxy_set_header   Upgrade    $http_upgrade;
        proxy_set_header   Connection "upgrade";
        proxy_set_header   Host       $host;
        client_max_body_size 50m;
    }
}

server {
    listen 80;
    server_name hime.example.com;
    return 301 https://$host$request_uri;
}
```

Obtain the certificate with certbot:

```bash
sudo certbot --nginx -d hime.example.com
```

### 2.5. Example: Caddy

Caddy handles TLS automatically via Let's Encrypt. The entire configuration fits in a few lines:

```caddy
hime.example.com {
    encode zstd gzip

    @ingest path /ingest/*
    reverse_proxy @ingest 127.0.0.1:8765

    reverse_proxy 127.0.0.1:8000
}
```

Run `sudo caddy reload` after editing `/etc/caddy/Caddyfile`. Caddy will negotiate a certificate on first request and renew it automatically.

### 2.6. Firewall

Close ports 8000, 8765, and 5173 on the public interface. Only 80 and 443 should be reachable from the internet.

---

## 3. Messaging gateway deployment notes

### 3.1. Telegram

Telegram supports two delivery modes:

- **Long polling (default)**: the backend opens an outbound HTTPS connection to `api.telegram.org` and fetches updates. **No public endpoint is required.** This is the correct choice for LAN-only or home-server deployments. It also works behind any NAT or tunnel.
- **Webhook**: Telegram pushes updates to a public HTTPS URL that you control. Requires a valid TLS certificate on a publicly reachable domain. Use this only if you are already running HiMe behind the reverse proxy described in section 2.

HiMe uses long polling by default. Unless you specifically need webhooks, leave it alone.

### 3.2. Feishu (Lark)

Feishu supports two transports for receiving events, selected via `FEISHU_TRANSPORT`:

- `ws` — long-lived WebSocket connection opened by the backend to Feishu. **No public endpoint is required.** Strongly recommended for personal deployments.
- `webhook` — Feishu posts events to a public HTTPS URL (typically `https://hime.example.com/api/feishu/webhook`). Requires the reverse-proxy setup from section 2 and the `FEISHU_VERIFICATION_TOKEN` from the Feishu developer console.

Note that the **Message Card Request URL** (used by the "Show Evidence" button) is always an HTTP callback even when the message transport is WebSocket. If you have no public URL, card actions will not work, but text chat will still function over WebSocket.

---

## 4. Docker Compose production notes

The repository ships with a `docker-compose.yml` that runs the backend, frontend, and Watch Exporter as three services. For production use:

1. **Mount persistent volumes** for `./data`, `./memory`, `./logs`, and `./prompts`. These are already declared in the compose file; do not remove them, or you will lose agent memory on every restart.
2. **Do not expose service ports directly**. Place the compose stack on an internal Docker network and put your reverse proxy (nginx, Caddy, or Traefik) in front, either as another compose service or on the host.
3. **Pin image tags** in any fork you build, rather than `:latest`, so that redeploys are reproducible.
4. **Set `API_AUTH_TOKEN` and any LLM API keys via `.env`**, not as literals in the compose file — the compose file reads from `.env` automatically.
5. **Run behind a process supervisor** if you are not using Docker's own restart policy. The `restart: unless-stopped` directive is usually enough.

### 4.1. Health checks

Compose services expose the same endpoints described in section 1.5. Point your orchestrator at:

- `GET http://backend:8000/health` — backend liveness
- `GET http://watch:8765/ping` — watch exporter liveness

---

## 5. Upgrades and backups

- **Backups**: the only irreplaceable state lives in `data/`, `memory/`, and `prompts/`. Back these up with your normal tooling (rsync, restic, borg). Health samples are refreshed from HealthKit on every iOS app launch, so losing `data/` is recoverable; losing `memory/` destroys the agent's learned history.
- **Upgrades**: `git pull && ./hime.sh restart --clean`. The `--clean` flag clears Python and node caches without touching `data/` or `memory/`.
- **Rollback**: because state lives in SQLite files with stable schemas, rolling back to a previous commit usually works without migrations. Any schema-migration steps will be called out in the release notes of the relevant GitHub release.

---

## 6. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| iOS app cannot reach backend | Firewall / wrong IP | Verify `curl http://<ip>:8765/ping` from another LAN device |
| `401 Unauthorized` from frontend | `API_AUTH_TOKEN` set but frontend missing it | Configure the token in the frontend and reload |
| Telegram bot silent | `TELEGRAM_ALLOWED_CHAT_IDS` empty | Add your chat ID to the allow-list |
| Feishu card actions do nothing | Message Card Request URL not set / not publicly reachable | Configure the card callback URL in the Feishu console |
| Agent stops after a few minutes on a laptop | OS sleep | Use `caffeinate` (macOS) or `systemd-inhibit` (Linux), or deploy on an always-on host |

For anything not covered here, see [`docs/DEVELOPMENT.md`](DEVELOPMENT.md).
