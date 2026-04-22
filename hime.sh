#!/bin/bash
# ──────────────────────────────────────────────────────────────────
# hime.sh — unified CLI for the HiMe platform
#
# Usage:  ./hime.sh <command> [options]
#
# Commands:
#   start         Start backend + frontend
#   stop          Stop all services
#   restart       Stop → start (add --clean to clear Python cache)
#   reset         Delete all agent memory & ingested data (interactive)
#   logs          Tail live backend logs
#   status        Show running services
#   help          Show this help
# ──────────────────────────────────────────────────────────────────
set -euo pipefail

PROJECT_ROOT="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$PROJECT_ROOT"

# ── Configuration ──────────────────────────────────────────────
# Load environment variables from .env if it exists
if [ -f ".env" ]; then
    while IFS='=' read -r key value || [ -n "$key" ]; do
        # Skip comments and empty lines
        [[ $key =~ ^[[:space:]]*# ]] && continue
        [[ -z $key ]] && continue
        # Remove trailing comments from value and any surrounding quotes
        value="${value%%#*}"
        value="${value%"${value##*[![:space:]]}"}" # trim trailing whitespace
        value="${value#\"}" # remove leading quote
        value="${value%\"}" # remove trailing quote
        value="${value#\'}" # remove leading single quote
        value="${value%\'}" # remove trailing single quote
        export "$key=$value"
    done < .env
fi

# ── Colours ──────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()  { echo -e "${CYAN}$*${NC}"; }
ok()    { echo -e "${GREEN}✓ $*${NC}"; }
warn()  { echo -e "${YELLOW}⚠  $*${NC}"; }
fail()  { echo -e "${RED}✗ $*${NC}"; exit 1; }

# ══════════════════════════════════════════════════════════════════
# Mode detection (docker | native).
# Resolution order:
#   1. Explicit flag on the current invocation: --docker or --native
#   2. HIME_RUN_MODE=docker|native in .env (loaded above into the shell env)
#   3. Auto-detect: any container (running OR stopped) for this compose
#      project → docker; else native.
# setup.sh writes HIME_RUN_MODE into .env so case 2 handles 99% of runs;
# cases 1 and 3 are fallbacks for manual overrides and legacy setups.
# ══════════════════════════════════════════════════════════════════
_hime_mode() {
    local arg
    for arg in "$@"; do
        case "$arg" in
            --docker) echo docker; return 0 ;;
            --native) echo native; return 0 ;;
        esac
    done
    case "${HIME_RUN_MODE:-}" in
        docker|native) echo "$HIME_RUN_MODE"; return 0 ;;
    esac
    if command -v docker >/dev/null 2>&1 \
       && [ -n "$(docker compose ps -a -q 2>/dev/null)" ]; then
        echo docker; return 0
    fi
    echo native
}

# ══════════════════════════════════════════════════════════════════
# Shared helpers (both modes)
# ══════════════════════════════════════════════════════════════════

# Print storage usage for the three host-mounted dirs plus any Docker named
# volumes owned by this project. The host dirs are bind-mounts so they
# reflect the same state the containers see; the named volume (watch-data)
# lives outside the repo and is invisible to `du` on the host, so we query
# Docker for its size separately — otherwise watch.db growth hides here
# and reset bugs like the watch-data ghost-data issue become invisible.
_show_storage() {
    echo ""
    info "Storage:"
    [ -d "logs" ]             && echo "  logs/            $(du -sh logs 2>/dev/null | cut -f1)"
    [ -d "memory" ]           && echo "  memory/          $(du -sh memory 2>/dev/null | cut -f1)"
    [ -d "data/data_stores" ] && echo "  data/data_stores $(du -sh data/data_stores 2>/dev/null | cut -f1)"

    # Docker named volumes — filter to this project's prefix. `docker system
    # df -v` prints a VOLUME NAME section; we grab rows whose name starts
    # with ``hime_`` and print NAME + SIZE (last column).
    if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
        local vol_rows
        vol_rows="$(docker system df -v 2>/dev/null \
            | awk '/^VOLUME NAME/{flag=1; next} flag && NF==0{flag=0} flag && $1 ~ /^hime_/ {print "  docker:"$1"  "$NF}')"
        if [ -n "$vol_rows" ]; then
            echo "$vol_rows"
        fi
    fi
}

# Refuse to start if HiMe's ports are held by something we don't control.
# Called from both _docker_start and _native_start on cold start; a running
# HiMe stack is handled by the caller before we get here.
_require_ports_free() {
    command -v lsof >/dev/null 2>&1 || return 0   # best-effort only
    local held=() p holders
    for p in 5173 8000 8765; do
        if lsof -nP -iTCP:"$p" -sTCP:LISTEN >/dev/null 2>&1; then
            holders="$(
                lsof -nP -iTCP:"$p" -sTCP:LISTEN 2>/dev/null | awk '
                    NR>1 && !seen[$2]++ { if (out!="") out=out", "; out=out $1" (PID "$2")" }
                    END { print out }
                '
            )"
            held+=("$p -> ${holders:-unknown}")
        fi
    done
    [ ${#held[@]} -eq 0 ] && return 0
    echo -e "${RED}✗ HiMe ports in use:${NC}"
    local item
    for item in "${held[@]}"; do
        echo "    $item"
    done
    echo ""
    echo "  Run './hime.sh stop' to clear them (safe; idempotent), then retry."
    exit 1
}

# ══════════════════════════════════════════════════════════════════
# stop — dual-mode, always runs BOTH paths (idempotent safety net).
# Rationale: if the user switched modes, a stale Docker stack + native
# processes can coexist. Running both teardown paths always leaves the
# machine in a clean state.
# ══════════════════════════════════════════════════════════════════
cmd_stop() {
    info "Stopping services..."

    # Docker path — no-op if no containers exist for this project.
    if command -v docker >/dev/null 2>&1 \
       && [ -n "$(docker compose ps -a -q 2>/dev/null)" ]; then
        docker compose down >/dev/null 2>&1 || true
    fi

    # Native path — port-based + process-name kill.
    _native_kill_processes

    ok "All services stopped."
}

_native_kill_processes() {
    # Port-based kill (Backend: 8000, Frontend: 5173, Watch: 8765)
    # Use -sTCP:LISTEN to only kill processes *listening* on these ports,
    # not reverse-proxy clients (e.g. cloudflared) that connect to them.
    local port pid pids
    for port in 8000 5173 8765; do
        pids=$(lsof -t -i:$port -sTCP:LISTEN 2>/dev/null || true)
        if [ -n "$pids" ]; then
            for pid in $pids; do
                kill -9 "$pid" 2>/dev/null || true
            done
        fi
    done

    # Process-name kill (safety net) — match by project root to avoid killing
    # unrelated processes, and cover all Python entry-point variants.
    pkill -9 -f "${PROJECT_ROOT}/backend" 2>/dev/null || true
    pkill -9 -f "python3 -m backend.main" 2>/dev/null || true
    pkill -9 -f "python.*backend.main"    2>/dev/null || true
    pkill -9 -f "uvicorn"                 2>/dev/null || true
    pkill -9 -f "${PROJECT_ROOT}/frontend" 2>/dev/null || true
    pkill -9 -f "vite"                    2>/dev/null || true
    pkill -9 -f "multiprocessing.spawn"   2>/dev/null || true
    pkill -9 -f "multiprocessing.resource_tracker" 2>/dev/null || true
    pkill -9 -f "${PROJECT_ROOT}/ios/Server/server.py" 2>/dev/null || true
    pkill -9 -f "ios/Server/server.py"            2>/dev/null || true
}

# ══════════════════════════════════════════════════════════════════
# start — launch backend + frontend (mode dispatcher)
# ══════════════════════════════════════════════════════════════════
cmd_start() {
    case "$(_hime_mode "$@")" in
        docker) _docker_start "$@" ;;
        *)      _native_start "$@" ;;
    esac
}

_docker_start() {
    echo -e "${BOLD}🚀 Starting HiMe${NC} (docker)"
    echo "═══════════════════════════════════════"

    command -v docker >/dev/null 2>&1 \
        || fail "Docker is not installed. Install Docker Desktop first."
    docker info >/dev/null 2>&1 \
        || fail "Docker daemon is not running. Start Docker Desktop first."
    [ -f docker-compose.yml ] \
        || fail "docker-compose.yml missing. Are you in the HiMe project root?"

    # If the compose stack already has containers, `up -d` is an idempotent
    # reconcile (starts whatever's stopped; no-op when all running). Only run
    # the port preflight on true cold start.
    if [ -z "$(docker compose ps -a -q 2>/dev/null)" ]; then
        _require_ports_free
        info "Building images (first run may take a few minutes)..."
        docker compose up --build -d
    else
        info "Reconciling compose stack..."
        docker compose up -d
    fi

    ok "Docker stack running."
    echo ""
    if [ -n "${DASHBOARD_URL:-}" ]; then
        echo "   External Dashboard: $DASHBOARD_URL"
        echo "   External API:       ${API_URL:-n/a}"
        echo "   External Watch:     ${WATCH_URL:-n/a}"
    else
        echo "   Local UI:           http://localhost:5173"
        echo "   Local API:          http://localhost:8000"
    fi
    echo ""
    ok "Use './hime.sh logs' to follow or './hime.sh status' to check."
}

_native_start() {
    local detached=true
    local _start_pids=()

    _cleanup_on_fail() {
        echo ""
        warn "Startup failed, cleaning up child processes..."
        for pid in "${_start_pids[@]}"; do
            kill "$pid" 2>/dev/null || true
        done
        pkill -9 -f "python3 -m backend.main" 2>/dev/null || true
        pkill -9 -f "vite" 2>/dev/null || true
        pkill -9 -f "ios/Server/server.py" 2>/dev/null || true
    }
    trap _cleanup_on_fail EXIT

    echo -e "${BOLD}🚀 Starting HiMe${NC} (native)"
    echo "═══════════════════════════════════════"

    # Port preflight — refuse to start on top of another HiMe / squatter.
    _require_ports_free

    # ── Pre-flight checks ────────────────────────────────────────
    if [ ! -f ".env" ]; then
        if [ -f ".env.example" ]; then
            warn ".env not found, creating from .env.example..."
            cp .env.example .env
            warn "Edit .env to add your API keys!"
        else
            fail ".env missing and no .env.example found."
        fi
    fi

    # Source .env again just in case it was created
    while IFS='=' read -r key value || [ -n "$key" ]; do
        [[ $key =~ ^[[:space:]]*# ]] && continue
        [[ -z $key ]] && continue
        value="${value%%#*}"
        value="${value%"${value##*[![:space:]]}"}"
        value="${value#\"}"; value="${value%\"}"
        value="${value#\'}"; value="${value%\'}"
        export "$key=$value"
    done < .env

    command -v python3 &>/dev/null || fail "Python 3 not found."

    if ! python3 -c "import uvicorn" &>/dev/null; then
        info "Installing backend dependencies..."
        pip install -r backend/requirements.txt
    fi

    # ── Prepare logs/ ────────────────────────────────────────────
    mkdir -p logs
    [ -f "logs/backend.log" ] && mv logs/backend.log logs/backend.log.prev
    [ -f "logs/watch.log" ] && mv logs/watch.log logs/watch.log.prev

    # ── Start Watch Exporter ─────────────────────────────────────
    info "Starting Watch Exporter (8765)..."
    PYTHONUNBUFFERED=1 python3 ios/Server/server.py --port 8765 > logs/watch.log 2>&1 &
    WATCH_PID=$!
    _start_pids+=("$WATCH_PID")

    # Wait for Watch Exporter to be ready before starting backend
    local tries=0
    while ! curl -s http://localhost:8765/ping > /dev/null 2>&1; do
        sleep 1
        tries=$((tries + 1))
        if [ $tries -ge 15 ]; then
            warn "Watch Exporter slow to start, continuing anyway..."
            break
        fi
    done
    if [ $tries -lt 15 ]; then
        ok "Watch Exporter ready (PID $WATCH_PID)"
    fi

    # ── Start backend ────────────────────────────────────────────
    info "Starting backend..."
    PYTHONUNBUFFERED=1 python3 -m backend.main > logs/backend.log 2>&1 &
    BACKEND_PID=$!
    _start_pids+=("$BACKEND_PID")
    
    # In background mode, we just wait for health

    local tries=0
    while ! curl -s http://localhost:8000/health > /dev/null 2>&1; do
        sleep 1
        tries=$((tries + 1))
        if [ $tries -ge 60 ]; then
            fail "Backend failed to start (60s timeout). Check logs/backend.log"
        fi
    done
    ok "Backend running — http://localhost:8000  (PID $BACKEND_PID)"

    # ── Sync auth token to frontend ─────────────────────────────
    # So users only need to set API_AUTH_TOKEN in .env once.
    # Uses sed to update in-place, preserving other settings (e.g. VITE_ALLOWED_HOSTS).
    local fe_env="frontend/.env.local"
    if [ -n "${API_AUTH_TOKEN:-}" ]; then
        if [ -f "$fe_env" ] && grep -q '^VITE_API_AUTH_TOKEN=' "$fe_env"; then
            sed -i.bak "s|^VITE_API_AUTH_TOKEN=.*|VITE_API_AUTH_TOKEN=${API_AUTH_TOKEN}|" "$fe_env" && rm -f "$fe_env.bak"
        else
            echo "VITE_API_AUTH_TOKEN=${API_AUTH_TOKEN}" >> "$fe_env"
        fi
    else
        # Remove stale token line if API_AUTH_TOKEN was cleared
        [ -f "$fe_env" ] && sed -i.bak '/^VITE_API_AUTH_TOKEN=/d' "$fe_env" && rm -f "$fe_env.bak"
    fi

    # ── Start frontend ───────────────────────────────────────────
    FRONTEND_PID=""
    if [ -d "frontend" ]; then
        info "Starting frontend..."
        if $detached; then
            cd frontend
            [ ! -d "node_modules" ] && npm install --silent
            nohup npm run dev > ../logs/frontend.log 2>&1 &
            FRONTEND_PID=$!
            cd ..
        else
            (
                cd frontend
                [ ! -d "node_modules" ] && npm install --silent
                npm run dev > ../logs/frontend.log 2>&1
            ) &
            FRONTEND_PID=$!
        fi
        _start_pids+=("$FRONTEND_PID")
        ok "Frontend unit started (PID $FRONTEND_PID)"

        # Wait for frontend to be ready
        local tries=0
        while ! curl -s -o /dev/null http://localhost:5173/ 2>/dev/null; do
            sleep 1
            tries=$((tries + 1))
            if [ $tries -ge 30 ]; then
                warn "Frontend slow to start (30s), continuing anyway..."
                break
            fi
        done
        if [ $tries -lt 30 ]; then
            ok "Frontend ready on http://localhost:5173"
        fi
    else
        warn "frontend/ not found, skipping."
    fi

    # ── Ready — remove failure trap ─────────────────────────────
    trap - EXIT
    echo ""
    echo -e "${BOLD}🎉 HiMe is ready!${NC}"
    if [ -n "$DASHBOARD_URL" ]; then
        echo "   External Dashboard: $DASHBOARD_URL"
        echo "   External API:       $API_URL"
        echo "   External Watch:     $WATCH_URL"
    else
       echo "   Local UI:           http://localhost:5173"
       echo "   Local API:          http://localhost:8000"
    fi
    echo ""

    ok "Services running in background. Use './hime.sh logs' to follow or './hime.sh status' to check."
}

_cleanup_start() {
    echo ""
    info "Stopping services..."
    local bpid="$1" fpid="$2" tpid="$3" wpid="$4"
    [ -n "$tpid" ] && kill -9 "$tpid" 2>/dev/null || true
    for pid in $fpid $bpid $wpid; do
        [ -n "$pid" ] && { pkill -9 -P "$pid" 2>/dev/null || true; kill -9 "$pid" 2>/dev/null || true; }
    done
    pkill -9 -f "python3 -m backend.main" 2>/dev/null || true
    pkill -9 -f "vite" 2>/dev/null || true
    pkill -9 -f "ios/Server/server.py" 2>/dev/null || true
    ok "All services stopped."
    exit 0
}

# ══════════════════════════════════════════════════════════════════
# restart — dual-mode. Flags:
#   --rebuild    docker-only: also rebuild images (for code/Dockerfile changes
#                or frontend-facing VITE_* env vars that get baked at build)
#   --clean|-c   native-only: wipe __pycache__/*.pyc between stop and start
#
# Docker mode uses `up -d --force-recreate` so .env changes always take
# effect (vs. plain `docker compose restart` which reuses the old container
# env). Native mode picks up .env naturally because each process reads it
# on startup.
# ══════════════════════════════════════════════════════════════════
cmd_restart() {
    local rebuild=false clean=false arg
    for arg in "$@"; do
        case "$arg" in
            --rebuild)  rebuild=true ;;
            --clean|-c) clean=true   ;;
        esac
    done
    case "$(_hime_mode "$@")" in
        docker) _docker_restart "$rebuild" ;;
        *)      _native_restart "$clean"   ;;
    esac
}

_docker_restart() {
    local rebuild="$1"
    command -v docker >/dev/null 2>&1 \
        || fail "Docker is not installed."
    if [ "$rebuild" = true ]; then
        info "Restarting Docker stack with rebuild (code/Dockerfile changes)..."
        docker compose up -d --build --force-recreate
    else
        info "Restarting Docker stack (.env changes are picked up)..."
        docker compose up -d --force-recreate
    fi
    ok "Docker stack restarted."
}

_native_restart() {
    local clean="$1"
    cmd_stop
    sleep 2

    # Double-check port 8000 before starting again
    local pids
    pids=$(lsof -t -i:8000 2>/dev/null || true)
    if [ -n "$pids" ]; then
        warn "Port 8000 still occupied, force-killing..."
        local pid
        for pid in $pids; do
            kill -9 "$pid" 2>/dev/null || true
        done
        sleep 1
    fi

    if [ "$clean" = true ]; then
        info "Cleaning Python cache..."
        find . -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
        find . -name "*.pyc" -delete 2>/dev/null || true
        ok "Cache cleaned."
    fi

    _native_start
}

# ══════════════════════════════════════════════════════════════════
# reset [--yes] — delete agent memory + ingested data
# ══════════════════════════════════════════════════════════════════
cmd_reset() {
    local skip_confirm=false
    for arg in "$@"; do
        case "$arg" in
            --yes|-y) skip_confirm=true ;;
        esac
    done

    echo -e "${BOLD}${RED}☢️  HiMe Factory Reset${NC}"
    echo "═══════════════════════════════════════"
    if ! $skip_confirm; then
        warn "This will PERMANENTLY DELETE:"
        echo "  - All Agent Memory (Chat history, learned facts, token usage)"
        echo "  - All Ingested Data (Wearable health databases)"
        echo "  - All Personalised Pages (data/personalised_pages/)"
        echo "  - All System Logs"
        echo "  - Your learned User Profile (prompts/user.md)"
        echo "  - Docker named volumes (watch-data: raw WatchExporter DB)"
        echo ""
        echo -en "${YELLOW}Are you absolutely sure? (y/N) ${NC}"
        read -r -n 1 reply
        echo
        [[ ! "$reply" =~ ^[Yy]$ ]] && { echo "Aborted."; exit 0; }
    fi

    # 1. Stop all services AND remove Docker named volumes.
    #    `cmd_stop` alone runs `docker compose down`, which leaves named
    #    volumes (e.g. watch-data holding watch.db) intact — those then get
    #    replayed into data/data_stores on next start, producing duplicated
    #    "ghost" data. `down --volumes` removes compose-declared named
    #    volumes but leaves host bind mounts (./data, ./memory, …) alone,
    #    which is what we want.
    info "Stopping services and removing Docker volumes..."
    if command -v docker >/dev/null 2>&1 \
       && [ -n "$(docker compose ps -a -q 2>/dev/null)" ]; then
        docker compose down --volumes --remove-orphans >/dev/null 2>&1 || true
    fi
    # Belt-and-suspenders: if containers were already removed earlier, the
    # named volume may be orphaned and `compose down -v` won't find it.
    # Remove it explicitly by name. Harmless no-op if already gone.
    if command -v docker >/dev/null 2>&1; then
        docker volume rm hime_watch-data >/dev/null 2>&1 || true
    fi
    _native_kill_processes
    ok "Services stopped, Docker volumes removed."

    # 2. Agent memory & configuration
    info "Clearing agent memory (memory/)..."
    rm -rf memory/*
    mkdir -p memory/agent_states

    # Clean legacy root DBs if present
    rm -f memory_db health_db 2>/dev/null || true
    ok "Memory cleared."

    # 3. Ingested data
    info "Clearing health data stores (data/data_stores/)..."
    rm -rf data/data_stores/*
    mkdir -p data/data_stores

    # Also clear the host-side Live source (native mode writes here; in
    # docker mode the real watch.db lives in the hime_watch-data volume
    # handled above).
    info "Clearing Live Watch database (ios/Server/watch.db)..."
    rm -f ios/Server/watch.db* 2>/dev/null || true

    # Clear any legacy or miscellaneous memory DBs
    rm -rf data/memory_dbs/* 2>/dev/null || true

    ok "Data stores cleared."

    # 4. Agent-created apps (preserve _shared UI library)
    info "Clearing personalised pages (data/personalised_pages/)..."
    find data/personalised_pages -mindepth 1 -maxdepth 1 ! -name '_shared' -exec rm -rf {} +
    mkdir -p data/personalised_pages/_shared
    ok "Personalised pages cleared."

    # 5. Logs
    info "Clearing logs..."
    rm -rf logs/*
    mkdir -p logs
    # Legacy logs dir
    rm -rf data/agent_logs/* 2>/dev/null || true
    ok "Logs cleared."

    # 6. User Profile
    info "Resetting personal user profile..."
    cat > prompts/user.md << 'EOF'
# User Profile

> This file is written and maintained by the agent itself.
> It captures preferences, habits, and communication style learned from
> conversations with the user over Telegram.
> Use the `update_user_profile` tool to update this file.

<!-- Agent: append your observations below this line. -->
EOF
    ok "Profile reset."

    echo ""
    echo -e "${BOLD}${GREEN}✅ System is now in a factory-fresh state.${NC}"
    echo "Run './hime.sh start' to begin a new session."
}


# ══════════════════════════════════════════════════════════════════
# forget [--yes] — Selective erasure: Only clear chat history & activity
# ══════════════════════════════════════════════════════════════════
cmd_forget() {
    local skip_confirm=false
    for arg in "$@"; do
        case "$arg" in
            --yes|-y) skip_confirm=true ;;
        esac
    done

    echo -e "${BOLD}${CYAN}🧠 Selective Memory Forget${NC}"
    echo "═══════════════════════════════════════"
    if ! $skip_confirm; then
        warn "This will PERMANENTLY DELETE:"
        echo "  - All Agent Chat History (Telegram conversations)"
        echo "  - All Loop & Turn History (Action traces, internal thoughts)"
        echo "  - All Generated Reports (Stored in DB & shown on dashboard)"
        echo "  - All System Logs (logs/*.log)"
        echo "  - All Personalised Pages (data/personalised_pages/)"
        echo ""
        echo "Data that will be PRESERVED:"
        echo "  - All Ingested Health Data (data/data_stores/)"
        echo "  - Live Watch Database (ios/Server/watch.db)"
        echo "  - Your learned User Profile (prompts/user.md)"
        echo "  - Agent's learned experience (prompts/experience.md)"
        echo ""
        echo -en "${YELLOW}Are you sure you want the agent to forget? (y/N) ${NC}"
        read -r -n 1 reply
        echo
        [[ ! "$reply" =~ ^[Yy]$ ]] && { echo "Aborted."; exit 0; }
    fi

    # 1. Stop all services
    cmd_stop

    # 2. Agent memory (History only)
    info "Clearing agent conversation states (memory/agent_states/)..."
    rm -rf memory/agent_states/*
    mkdir -p memory/agent_states
    
    info "Clearing agent activity logs and reports (memory/*.db)..."
    rm -f memory/*.db 2>/dev/null || true
    
    # Also clear session/app state to ensure a fresh session
    rm -f memory/*.json 2>/dev/null || true
    
    # Clean root level legacy DBs if present
    rm -f memory.db memory_db health_db 2>/dev/null || true

    # 3. Agent-created apps
    info "Clearing personalised pages (data/personalised_pages/)..."
    find data/personalised_pages -mindepth 1 -maxdepth 1 ! -name '_shared' -exec rm -rf {} +
    mkdir -p data/personalised_pages/_shared

    # 4. Logs
    info "Clearing server logs..."
    rm -rf logs/*
    mkdir -p logs
    
    ok "Agent selective amnesia complete."
    echo ""
    echo -e "${BOLD}${GREEN}✅ Forget complete.${NC}"
    echo "Run './hime.sh start' to begin a new session."
}

# ══════════════════════════════════════════════════════════════════
# logs [service] — tail backend/frontend/watch logs (mode dispatcher)
# ══════════════════════════════════════════════════════════════════
cmd_logs() {
    # Strip mode flags from the service positional arg.
    local target="" arg
    for arg in "$@"; do
        case "$arg" in
            --docker|--native) ;;
            *) [ -z "$target" ] && target="$arg" ;;
        esac
    done
    case "$(_hime_mode "$@")" in
        docker) _docker_logs "${target:-all}" ;;
        *)      _native_logs "${target:-all}" ;;
    esac
}

_docker_logs() {
    local target="$1"
    command -v docker >/dev/null 2>&1 \
        || fail "Docker is not installed."
    if [ "$target" = all ]; then
        echo -e "${CYAN}Tailing docker compose logs (Ctrl+C to exit)${NC}"
        docker compose logs -f --tail=100
    else
        # Map convenience names to compose service names (they happen to match).
        echo -e "${CYAN}Tailing docker compose logs for '$target' (Ctrl+C to exit)${NC}"
        docker compose logs -f --tail=100 "$target"
    fi
}

_native_logs() {
    local target="$1"
    if [ "$target" = all ]; then
        info "Tailing all logs (backend, frontend, watch)..."
        local logs_to_tail="" log_name
        for log_name in backend frontend watch; do
            [ -f "logs/${log_name}.log" ] && logs_to_tail="$logs_to_tail logs/${log_name}.log"
        done
        [ -z "$logs_to_tail" ] && fail "No log files found in logs/ directory."
        tail -f $logs_to_tail
    else
        local logfile="logs/${target}.log"
        if [ ! -f "$logfile" ]; then
            warn "Log file '$logfile' not found."
            echo "Available logs:"
            ls -1 logs/*.log 2>/dev/null | sed 's/logs\///; s/\.log//' || echo "  (None)"
            exit 1
        fi
        echo -e "${CYAN}Tailing $logfile (Ctrl+C to exit)${NC}"
        tail -f "$logfile"
    fi
}

# ══════════════════════════════════════════════════════════════════
# status — mode dispatcher. Storage block is printed once, after whichever
# per-mode status block ran.
# ══════════════════════════════════════════════════════════════════
cmd_status() {
    local mode
    mode="$(_hime_mode "$@")"
    echo -e "${BOLD}HiMe Status${NC} (${mode})"
    echo "═══════════════════════════════════════"
    case "$mode" in
        docker) _docker_status ;;
        *)      _native_status ;;
    esac
    _show_storage
}

_docker_status() {
    if ! command -v docker >/dev/null 2>&1; then
        echo -e "  Docker   — ${RED}not installed${NC}"
        return
    fi
    info "Containers:"
    local out
    out="$(docker compose ps 2>/dev/null || true)"
    if [ -z "$out" ] || [ "$(echo "$out" | wc -l)" -le 1 ]; then
        echo -e "  ${RED}No containers for this compose project.${NC}"
        echo "  Run './hime.sh start' to bring the stack up."
    else
        echo "$out" | sed 's/^/  /'
    fi

    # Live health probes on the published ports — catches the case where a
    # container is "Up" per compose but the service inside is crashed.
    echo ""
    info "Health:"
    if curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/health 2>/dev/null | grep -q '^2'; then
        ok "Backend  /health OK       (:8000)"
    else
        echo -e "  Backend  ${RED}/health unreachable${NC}  (:8000)"
    fi
    if curl -s -o /dev/null http://localhost:5173/ 2>/dev/null; then
        ok "Frontend reachable        (:5173)"
    else
        echo -e "  Frontend ${RED}unreachable${NC}          (:5173)"
    fi
    if curl -s -o /dev/null http://localhost:8765/ping 2>/dev/null; then
        ok "Watch    /ping OK         (:8765)"
    else
        echo -e "  Watch    ${RED}/ping unreachable${NC}    (:8765)"
    fi
}

_native_status() {
    # Backend
    local bpids=$(lsof -t -i:8000 2>/dev/null || true)
    if [ -n "$bpids" ]; then
        ok "Backend  — running (PIDs: $(echo $bpids | tr '\n' ' '))"
        if curl -s http://localhost:8000/health > /dev/null 2>&1; then
            ok "           /health endpoint OK"
        else
            warn "           /health endpoint unreachable"
        fi
    else
        echo -e "  Backend  — ${RED}not running${NC}"
    fi

    # Frontend
    local fpids=$(lsof -t -i:5173 2>/dev/null || true)
    if [ -n "$fpids" ]; then
        ok "Frontend — running (PIDs: $(echo $fpids | tr '\n' ' '))"
    else
        echo -e "  Frontend — ${RED}not running${NC}"
    fi

    # Watch Exporter
    local wpids=$(lsof -t -i:8765 2>/dev/null || true)
    if [ -n "$wpids" ]; then
        ok "Watch Ex — running (PIDs: $(echo $wpids | tr '\n' ' '), Port: 8765)"
    else
        echo -e "  Watch Ex — ${RED}not running${NC} (Port 8765)"
    fi
}

# ══════════════════════════════════════════════════════════════════
# help
# ══════════════════════════════════════════════════════════════════
cmd_help() {
    cat << 'HELP'
HiMe — unified platform CLI

Usage: ./hime.sh <command> [options]

Run mode is picked up from HIME_RUN_MODE in .env (set by setup.sh). The
commands below auto-dispatch to the Docker or native implementation; pass
--docker or --native to any command to force a specific mode for this call.

Commands:
  start               Start all services (Backend, Frontend, Watch Exporter).
  stop                Stop all running services (both modes, idempotent).
  restart [--rebuild] Restart the stack; in docker mode this is equivalent to
                      `docker compose up -d --force-recreate`, which always
                      picks up .env changes.
  restart [--clean]   (native only) also wipe __pycache__/*.pyc.
  status              Show running status and storage usage.
  logs [service]      Follow logs. Defaults to all services.
                      Services: backend | frontend | watch | all
  reset [--yes]       Delete agent memory and ingested data.
  forget [--yes]      Selective erasure: clear chat history but KEEP health data.
  help                Show this help message.

Flags:
  --docker            Force docker-mode dispatch for this invocation.
  --native            Force native-mode dispatch for this invocation.
  --rebuild           (docker restart) also rebuild images. Use this when
                      you changed code, Dockerfile, or a VITE_* env var that
                      gets baked into the frontend bundle.
  --clean, -c         (native restart) clear Python cache.
  --yes,   -y         Skip confirmation (for reset / forget).

Log locations:
  native mode         logs/backend.log, logs/frontend.log, logs/watch.log
  docker mode         docker compose logs (per-service)
HELP
}

# ══════════════════════════════════════════════════════════════════
# Dispatch
# ══════════════════════════════════════════════════════════════════
command="${1:-help}"
shift 2>/dev/null || true

case "$command" in
    start)   cmd_start "$@" ;;
    stop)    cmd_stop "$@" ;;
    restart) cmd_restart "$@" ;;
    reset)   cmd_reset "$@" ;;
    forget)  cmd_forget "$@" ;;
    logs)    cmd_logs "$@" ;;
    status)  cmd_status "$@" ;;
    help|--help|-h) cmd_help ;;
    *)       fail "Unknown command: $command. Run './hime.sh help' for usage." ;;
esac
