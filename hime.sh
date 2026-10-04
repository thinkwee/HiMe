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
# Load environment variables from a .env file.
#
# Read the whole line and split on the FIRST '=' manually. Using
# `IFS='=' read` strips trailing IFS chars and would corrupt base64
# values ending in '=' (e.g. a 32-byte API token).
#
# Quote handling must come BEFORE comment stripping: setup.sh single-quotes
# any value containing special characters, so a secret like 'abc#def' would
# otherwise be truncated to `abc` — a silent, near-undebuggable auth failure
# that also diverges from how docker compose reads the same file.
_load_env_file() {
    local file="$1" line key value quote rest
    [ -f "$file" ] || return 0
    while IFS= read -r line || [ -n "$line" ]; do
        # Skip comments and empty lines
        [[ $line =~ ^[[:space:]]*# ]] && continue
        [[ -z $line ]] && continue
        [[ $line != *=* ]] && continue
        key="${line%%=*}"
        value="${line#*=}"
        value="${value#"${value%%[![:space:]]*}"}" # trim leading whitespace
        case "$value" in
            \'*|\"*)
                # Quoted: take everything up to the LAST matching quote,
                # which drops any trailing comment but keeps '#' inside.
                quote="${value:0:1}"
                rest="${value#?}"
                value="${rest%"$quote"*}"
                ;;
            *)
                # Unquoted: an inline comment only counts when preceded by
                # whitespace, so values such as ab#cd survive intact.
                case "$value" in
                    *[[:space:]]#*) value="${value%%[[:space:]]#*}" ;;
                esac
                value="${value%"${value##*[![:space:]]}"}" # trim trailing whitespace
                ;;
        esac
        export "$key=$value"
    done < "$file"
}

_load_env_file .env

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

    # Process-name kill (safety net). Patterns are either anchored to this
    # project root or to a HiMe-specific entry point. Bare patterns like
    # "uvicorn", "vite" or "multiprocessing.*" are deliberately NOT used:
    # they would SIGKILL unrelated dev servers and Python jobs elsewhere on
    # the machine (the vite child process carries the project path, so
    # "${PROJECT_ROOT}/frontend" already covers the frontend).
    pkill -9 -f "${PROJECT_ROOT}/backend" 2>/dev/null || true
    pkill -9 -f "python3 -m backend.main" 2>/dev/null || true
    pkill -9 -f "python.*backend.main"    2>/dev/null || true
    pkill -9 -f "${PROJECT_ROOT}/frontend" 2>/dev/null || true
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
        # `${arr[@]+…}` keeps an empty array from tripping `set -u` on bash
        # 3.2 (macOS /bin/bash) — otherwise the trap aborts here and the
        # pkill fallback below never runs.
        for pid in ${_start_pids[@]+"${_start_pids[@]}"}; do
            kill "$pid" 2>/dev/null || true
        done
        pkill -9 -f "python3 -m backend.main" 2>/dev/null || true
        pkill -9 -f "${PROJECT_ROOT}/frontend" 2>/dev/null || true
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

    # Source .env again just in case it was created.
    _load_env_file .env

    command -v python3 &>/dev/null || fail "Python 3 not found."

    if ! python3 -c "import uvicorn" &>/dev/null; then
        info "Installing backend dependencies..."
        # `python3 -m pip` guarantees we install into the SAME interpreter
        # that runs the backend; a bare `pip` may belong to another one.
        if ! python3 -m pip install -r backend/requirements.txt; then
            echo ""
            warn "pip install failed."
            warn "On Homebrew Python 3.12+ / Debian 12+ pip refuses to install into"
            warn "the system interpreter (PEP 668 'externally-managed-environment')."
            warn "Create and activate a virtualenv, then retry:"
            echo "    python3 -m venv .venv && source .venv/bin/activate"
            echo "    python3 -m pip install -r backend/requirements.txt"
            fail "Backend dependencies not installed."
        fi
    fi

    # ── Prepare logs/ ────────────────────────────────────────────
    mkdir -p logs
    [ -f "logs/backend.log" ] && mv logs/backend.log logs/backend.log.prev
    [ -f "logs/watch.log" ] && mv logs/watch.log logs/watch.log.prev

    # ── Start Watch Exporter ─────────────────────────────────────
    info "Starting Watch Exporter (8765)..."
    PYTHONUNBUFFERED=1 nohup python3 ios/Server/server.py --port 8765 > logs/watch.log 2>&1 &
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
    PYTHONUNBUFFERED=1 nohup python3 -m backend.main > logs/backend.log 2>&1 &
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
    if [ -n "${DASHBOARD_URL:-}" ]; then
        echo "   External Dashboard: $DASHBOARD_URL"
        echo "   External API:       ${API_URL:-n/a}"
        echo "   External Watch:     ${WATCH_URL:-n/a}"
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
    pkill -9 -f "${PROJECT_ROOT}/frontend" 2>/dev/null || true
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
# wearables — optional open-wearables (OW) integration [EXPERIMENTAL].
# Subcommands: setup | start | stop | status | bootstrap | seed | help
#
# Entirely opt-in: none of these run automatically and none of the commands
# above touch external/, docker-compose.openwearables.yml, or the
# OPENWEARABLES_* vars in .env — a user who never types "wearables" sees zero
# change in behavior. See docs/OPEN_WEARABLES.md for the full guide.
# ══════════════════════════════════════════════════════════════════
OW_DIR="external/open-wearables"
OW_REPO_URL="https://github.com/the-momentum/open-wearables"
OW_PINNED_SHA="44a268be623e81995e896b05ed93a56411ddf807"
OW_COMPOSE_FILES=(-f docker-compose.yml -f docker-compose.openwearables.yml)
OW_SERVICES=(openwearables-db openwearables-redis openwearables-svix openwearables-app openwearables-celery-worker openwearables-celery-beat)
OW_APP_URL="http://localhost:8010"
# OPENWEARABLES_BASE_URL value for each HiMe run mode (see _hime_mode above):
# docker mode shares a Docker network with openwearables-app, so the
# in-network DNS name resolves; native mode runs HiMe's backend on the host,
# which can only reach the container via its published port.
OW_BASE_URL_DOCKER="http://openwearables-app:8000"
OW_BASE_URL_NATIVE="${OW_APP_URL}"

cmd_wearables() {
    local sub="${1:-help}"
    shift 2>/dev/null || true
    case "$sub" in
        setup)              _wearables_setup "$@" ;;
        start)              _wearables_start "$@" ;;
        stop)               _wearables_stop "$@" ;;
        status)             _wearables_status "$@" ;;
        bootstrap)          _wearables_bootstrap "$@" ;;
        seed)               _wearables_seed "$@" ;;
        help|--help|-h|"")  _wearables_help ;;
        *) fail "Unknown wearables subcommand: $sub. Run './hime.sh wearables help' for usage." ;;
    esac
}

_wearables_require_docker() {
    command -v docker >/dev/null 2>&1 \
        || fail "Docker is not installed. The open-wearables integration requires Docker."
    docker info >/dev/null 2>&1 \
        || fail "Docker daemon is not running. Start Docker Desktop first."
}

_wearables_gen_secret() {
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -hex 32
    else
        # `od` is in coreutils and present everywhere; `xxd` ships with
        # vim-common and is commonly absent on minimal Linux images.
        head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'
        printf '\n'
    fi
}

# Set KEY=VALUE in FILE, preserving every other line. Appends if KEY is absent.
_wearables_set_env_var() {
    local file="$1" key="$2" value="$3"
    if [ -f "$file" ] && grep -q "^${key}=" "$file"; then
        sed -i.bak "s|^${key}=.*|${key}=${value}|" "$file" && rm -f "${file}.bak"
    else
        printf '%s=%s\n' "$key" "$value" >> "$file"
    fi
}

_wearables_ow_env_get() {
    local key="$1" file="$OW_DIR/backend/config/.env"
    [ -f "$file" ] || return 1
    grep -E "^${key}=" "$file" | tail -1 | cut -d= -f2-
}

_wearables_help() {
    cat << 'HELP'
HiMe — open-wearables (OW) integration (experimental, opt-in)

Usage: ./hime.sh wearables <subcommand>

Subcommands:
  setup       Clone open-wearables (pinned commit) into ./external/open-wearables
              and generate its backend .env. Safe to re-run (idempotent).
  start       Bring up the OW service stack (db, redis, svix, app, celery).
  stop        Tear down the OW service stack only (HiMe core is untouched).
  status      Show OW container status.
  bootstrap   Headless: log in as the OW admin, mint an API key, and write
              OPENWEARABLES_API_KEY / OPENWEARABLES_ENABLED=true /
              OPENWEARABLES_BASE_URL into .env (base URL matched to HiMe's
              docker/native run mode; pass --docker or --native to override
              detection).
  seed [id]   Generate synthetic wearable data in OW for end-to-end testing
              without real devices (default preset: active_athlete).
  help        Show this help message.

Typical first run:
  ./hime.sh wearables setup && ./hime.sh wearables start && ./hime.sh wearables bootstrap
  ./hime.sh restart --rebuild   # pick up OPENWEARABLES_* in .env

See docs/OPEN_WEARABLES.md for the full guide.
HELP
}

# ══════════════════════════════════════════════════════════════════
# wearables setup — clone the pinned OW checkout + generate its backend .env.
# ══════════════════════════════════════════════════════════════════
_wearables_setup() {
    echo -e "${BOLD}Setting up open-wearables${NC}"
    echo "═══════════════════════════════════════"
    command -v git >/dev/null 2>&1 || fail "git is required for './hime.sh wearables setup'."

    mkdir -p external

    if [ ! -d "$OW_DIR/.git" ]; then
        info "Cloning open-wearables..."
        git clone "$OW_REPO_URL" "$OW_DIR" || fail "Failed to clone $OW_REPO_URL."
    fi

    info "Checking out pinned commit ${OW_PINNED_SHA}..."
    if ! git -C "$OW_DIR" checkout --detach "$OW_PINNED_SHA" 2>/dev/null; then
        # Full clones normally already contain the pinned SHA; only hit the
        # network again if it's genuinely missing (e.g. a shallow checkout).
        git -C "$OW_DIR" fetch --depth 1 origin "$OW_PINNED_SHA" 2>/dev/null \
            || git -C "$OW_DIR" fetch origin \
            || fail "Failed to fetch open-wearables from $OW_REPO_URL."
        git -C "$OW_DIR" checkout --detach "$OW_PINNED_SHA" \
            || fail "Failed to check out pinned commit $OW_PINNED_SHA."
    fi
    ok "open-wearables checked out at ${OW_PINNED_SHA:0:12} (detached HEAD)."

    # Generate the OW backend .env (idempotent: leave an existing one alone).
    local ow_env_dir="$OW_DIR/backend/config"
    local ow_env="$ow_env_dir/.env"
    if [ -f "$ow_env" ]; then
        warn "OW env already exists at $ow_env — leaving it untouched."
        warn "Delete it and re-run 'wearables setup' to regenerate."
    else
        [ -f "$ow_env_dir/.env.example" ] \
            || fail "$ow_env_dir/.env.example not found — is the checkout intact?"
        cp "$ow_env_dir/.env.example" "$ow_env"

        # NOTE: avoid ".local"/".test"/".internal"/etc. — pydantic's email
        # validator rejects RFC 2606 / ICANN special-use TLDs outright, which
        # crash-loops seed_admin.py on every app start ("not a valid email
        # address: ... special-use or reserved name").
        local admin_email="admin@openwearables-hime.com"
        local admin_password secret_key
        admin_password="$(_wearables_gen_secret | cut -c1-24)"
        secret_key="$(_wearables_gen_secret)"

        _wearables_set_env_var "$ow_env" ADMIN_EMAIL "$admin_email"
        _wearables_set_env_var "$ow_env" ADMIN_PASSWORD "$admin_password"
        _wearables_set_env_var "$ow_env" SECRET_KEY "$secret_key"
        # Off by default upstream; HiMe's webhook flow needs it on.
        _wearables_set_env_var "$ow_env" OUTGOING_WEBHOOKS_ENABLED true
        # Match the service names used in docker-compose.openwearables.yml
        # (OW's own .env.example assumes its own compose file's names).
        _wearables_set_env_var "$ow_env" DB_HOST openwearables-db
        _wearables_set_env_var "$ow_env" REDIS_HOST openwearables-redis
        _wearables_set_env_var "$ow_env" SVIX_SERVER_URL "http://openwearables-svix:8071"
        # Browser-facing OAuth redirects must hit the published host port,
        # not the container-internal 8000.
        _wearables_set_env_var "$ow_env" API_BASE_URL "${OW_APP_URL}"

        ok "Generated $ow_env"
        echo ""
        echo "   OW admin email:    $admin_email"
        echo "   OW admin password: $admin_password"
        echo "   (this is the OW dashboard/API login, separate from HiMe's own auth;"
        echo "    saved in $ow_env, printed here because it's otherwise unrecoverable)"
    fi

    # Keep the checkout out of git.
    if [ -f .gitignore ] && ! grep -qxF 'external/' .gitignore; then
        printf '\n# open-wearables checkout (managed by ./hime.sh wearables setup)\nexternal/\n' >> .gitignore
        ok "Added external/ to .gitignore"
    fi

    echo ""
    ok "Setup complete. Next: './hime.sh wearables start' then './hime.sh wearables bootstrap'."
}

# ══════════════════════════════════════════════════════════════════
# wearables start|stop|status — compose up/down/ps scoped to OW_SERVICES only.
# HiMe's own backend/frontend/watch services are never touched by these.
# ══════════════════════════════════════════════════════════════════
_wearables_start() {
    _wearables_require_docker
    [ -f docker-compose.openwearables.yml ] \
        || fail "docker-compose.openwearables.yml missing. Are you in the HiMe project root?"
    [ -d "$OW_DIR/backend" ] \
        || fail "open-wearables checkout not found. Run './hime.sh wearables setup' first."
    [ -f "$OW_DIR/backend/config/.env" ] \
        || fail "OW backend .env not found. Run './hime.sh wearables setup' first."

    echo -e "${BOLD}Starting open-wearables${NC} (first run builds from source — can take a few minutes)"
    echo "═══════════════════════════════════════"
    docker compose "${OW_COMPOSE_FILES[@]}" up -d "${OW_SERVICES[@]}" \
        || fail "Failed to start the open-wearables stack. Check the output above."
    ok "open-wearables stack starting."
    echo ""
    echo "   OW API:  ${OW_APP_URL}  (docs: ${OW_APP_URL}/docs)"
    echo ""
    ok "Use './hime.sh wearables status' to check health, then './hime.sh wearables bootstrap'."
}

_wearables_stop() {
    _wearables_require_docker
    info "Stopping open-wearables (HiMe's own backend/frontend/watch are untouched)..."
    docker compose "${OW_COMPOSE_FILES[@]}" down "${OW_SERVICES[@]}" >/dev/null 2>&1 || true
    ok "open-wearables stack stopped."
}

_wearables_status() {
    _wearables_require_docker
    echo -e "${BOLD}open-wearables status${NC}"
    echo "═══════════════════════════════════════"
    docker compose "${OW_COMPOSE_FILES[@]}" ps "${OW_SERVICES[@]}"
}

# ══════════════════════════════════════════════════════════════════
# wearables bootstrap — headless login -> API key -> write it into HiMe's .env
# ══════════════════════════════════════════════════════════════════
_wearables_bootstrap() {
    _wearables_require_docker
    command -v curl >/dev/null 2>&1 || fail "curl is required for './hime.sh wearables bootstrap'."
    command -v python3 >/dev/null 2>&1 || fail "python3 is required for './hime.sh wearables bootstrap' (JSON parsing)."

    local ow_env="$OW_DIR/backend/config/.env"
    [ -f "$ow_env" ] || fail "OW backend .env not found. Run './hime.sh wearables setup' first."

    local admin_email admin_password
    admin_email="$(_wearables_ow_env_get ADMIN_EMAIL)"
    admin_password="$(_wearables_ow_env_get ADMIN_PASSWORD)"
    [ -n "$admin_email" ] && [ -n "$admin_password" ] \
        || fail "ADMIN_EMAIL/ADMIN_PASSWORD not set in $ow_env."

    info "Waiting for openwearables-app to become healthy (${OW_APP_URL})..."
    local tries=0
    while ! curl -s -o /dev/null -w '%{http_code}' "${OW_APP_URL}/docs" 2>/dev/null | grep -q '^2'; do
        sleep 2
        tries=$((tries + 1))
        if [ $tries -ge 60 ]; then
            fail "openwearables-app did not become healthy after 120s. Check: docker compose -f docker-compose.yml -f docker-compose.openwearables.yml logs openwearables-app"
        fi
    done
    ok "openwearables-app is up."

    info "Logging in as OW admin ($admin_email)..."
    local login_resp jwt
    login_resp="$(curl -s -X POST "${OW_APP_URL}/api/v1/auth/login" \
        -H 'Content-Type: application/x-www-form-urlencoded' \
        --data-urlencode "username=${admin_email}" \
        --data-urlencode "password=${admin_password}")"
    jwt="$(printf '%s' "$login_resp" | python3 "$PROJECT_ROOT/docker/openwearables/json_field.py" access_token)"
    [ -n "$jwt" ] || fail "Login failed. Response: $login_resp"
    ok "Logged in."

    info "Creating an API key..."
    local key_resp api_key
    key_resp="$(curl -s -X POST "${OW_APP_URL}/api/v1/developer/api-keys" \
        -H "Authorization: Bearer ${jwt}" \
        -H 'Content-Type: application/json' \
        -d '{"name":"hime-integration"}')"
    api_key="$(printf '%s' "$key_resp" | python3 "$PROJECT_ROOT/docker/openwearables/json_field.py" id)"
    [ -n "$api_key" ] || fail "API key creation failed. Response: $key_resp"
    ok "API key created (${api_key:0:10}...)."

    if [ ! -f .env ]; then
        [ -f .env.example ] || fail ".env.example missing; cannot bootstrap HiMe's .env."
        cp .env.example .env
        warn ".env not found, created from .env.example."
    fi
    _wearables_set_env_var .env OPENWEARABLES_API_KEY "$api_key"
    _wearables_set_env_var .env OPENWEARABLES_ENABLED true

    # Point HiMe's backend at the right open-wearables address: the
    # compose-internal hostname only resolves when HiMe's own backend is
    # ALSO running in Docker (shared network with openwearables-app); a
    # native backend must use the published host port instead. Reuses the
    # same docker/native detection as start/stop/restart/logs/status (see
    # _hime_mode above); --docker/--native on this invocation still wins.
    local hime_mode ow_base_url
    hime_mode="$(_hime_mode "$@")"
    if [ "$hime_mode" = docker ]; then
        ow_base_url="$OW_BASE_URL_DOCKER"
    else
        ow_base_url="$OW_BASE_URL_NATIVE"
    fi
    _wearables_set_env_var .env OPENWEARABLES_BASE_URL "$ow_base_url"
    ok "Wrote OPENWEARABLES_API_KEY, OPENWEARABLES_ENABLED=true, and OPENWEARABLES_BASE_URL=$ow_base_url to .env"

    echo ""
    echo "   Detected HiMe run mode: $hime_mode"
    echo "   OPENWEARABLES_BASE_URL has two valid values depending on how HiMe's"
    echo "   own backend runs (not how open-wearables runs — that's always Docker):"
    echo "     - $OW_BASE_URL_DOCKER  (docker mode: HiMe backend is a container on"
    echo "       the same compose network as openwearables-app)"
    echo "     - $OW_BASE_URL_NATIVE          (native mode: HiMe backend runs on the"
    echo "       host and can only reach openwearables-app via its published port)"
    echo "   If you switch HiMe between docker/native later, re-run"
    echo "   './hime.sh wearables bootstrap' (or edit OPENWEARABLES_BASE_URL in .env"
    echo "   by hand) so it keeps matching."

    echo ""
    echo "Next steps:"
    echo "  1. Restart HiMe so the backend picks up the new .env values:"
    echo "       ./hime.sh restart --rebuild   (docker mode)"
    echo "       ./hime.sh restart             (native mode)"
    echo "  2. Connect a real provider from the HiMe Devices page, or run"
    echo "     './hime.sh wearables seed' to generate synthetic data for testing"
    echo "     without real devices."
}

# ══════════════════════════════════════════════════════════════════
# wearables seed [preset-id] — dispatch a synthetic-data seed job in OW.
# ══════════════════════════════════════════════════════════════════
_wearables_seed() {
    _wearables_require_docker
    command -v curl >/dev/null 2>&1 || fail "curl is required for './hime.sh wearables seed'."
    command -v python3 >/dev/null 2>&1 || fail "python3 is required for './hime.sh wearables seed' (JSON handling)."

    local preset="${1:-active_athlete}"
    local ow_env="$OW_DIR/backend/config/.env"
    [ -f "$ow_env" ] || fail "OW backend .env not found. Run './hime.sh wearables setup' first."

    local admin_email admin_password
    admin_email="$(_wearables_ow_env_get ADMIN_EMAIL)"
    admin_password="$(_wearables_ow_env_get ADMIN_PASSWORD)"
    [ -n "$admin_email" ] && [ -n "$admin_password" ] \
        || fail "ADMIN_EMAIL/ADMIN_PASSWORD not set in $ow_env."

    info "Logging in as OW admin..."
    local login_resp jwt
    login_resp="$(curl -s -X POST "${OW_APP_URL}/api/v1/auth/login" \
        -H 'Content-Type: application/x-www-form-urlencoded' \
        --data-urlencode "username=${admin_email}" \
        --data-urlencode "password=${admin_password}")"
    jwt="$(printf '%s' "$login_resp" | python3 "$PROJECT_ROOT/docker/openwearables/json_field.py" access_token)"
    [ -n "$jwt" ] || fail "Login failed. Response: $login_resp. Is './hime.sh wearables start' running?"

    info "Fetching seed presets..."
    local presets_resp payload
    presets_resp="$(curl -s "${OW_APP_URL}/api/v1/settings/seed/presets" -H "Authorization: Bearer ${jwt}")"
    payload="$(printf '%s' "$presets_resp" | python3 "$PROJECT_ROOT/docker/openwearables/seed_payload.py" "$preset")" \
        || fail "Could not resolve seed preset '$preset'. See ${OW_APP_URL}/api/v1/settings/seed/presets for valid ids."

    info "Dispatching seed generation (preset: $preset)..."
    local seed_resp
    seed_resp="$(curl -s -X POST "${OW_APP_URL}/api/v1/settings/seed" \
        -H "Authorization: Bearer ${jwt}" \
        -H 'Content-Type: application/json' \
        -d "$payload")"
    echo "$seed_resp"
    ok "Seed task dispatched."
    echo ""
    echo "This creates a brand-new synthetic OW user with generated data — it does"
    echo "NOT require a real HiMe-connected user to exist first. The task runs"
    echo "asynchronously; follow progress with:"
    echo "  docker compose -f docker-compose.yml -f docker-compose.openwearables.yml logs -f openwearables-celery-worker"
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
  wearables <sub>     Optional open-wearables integration (experimental, opt-in).
                      Run './hime.sh wearables help' for its subcommands, or
                      see docs/OPEN_WEARABLES.md.
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
    wearables) cmd_wearables "$@" ;;
    logs)    cmd_logs "$@" ;;
    status)  cmd_status "$@" ;;
    help|--help|-h) cmd_help ;;
    *)       fail "Unknown command: $command. Run './hime.sh help' for usage." ;;
esac
