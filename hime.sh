#!/bin/bash
# ──────────────────────────────────────────────────────────────────
# hime.sh — unified CLI for the Hime platform
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
# stop — kill backend (port 8000) + frontend (port 5173)
# ══════════════════════════════════════════════════════════════════
cmd_stop() {
    info "Stopping services..."

    # Port-based kill (Backend: 8000, Frontend: 5173, Watch: 8765)
    # Use -sTCP:LISTEN to only kill processes *listening* on these ports,
    # not reverse-proxy clients (e.g. cloudflared) that connect to them.
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

    ok "All services stopped."
}

# ══════════════════════════════════════════════════════════════════
# start — launch backend + frontend
# ══════════════════════════════════════════════════════════════════
cmd_start() {
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

    echo -e "${BOLD}🚀 Starting Hime${NC}"
    echo "═══════════════════════════════════════"

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
    echo -e "${BOLD}🎉 Hime is ready!${NC}"
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
# restart [--clean] — stop + (optionally clean cache) + start
# ══════════════════════════════════════════════════════════════════
cmd_restart() {
    local clean=false
    for arg in "$@"; do
        case "$arg" in
            --clean|-c) clean=true ;;
        esac
    done

    cmd_stop
    sleep 2

    # Double-check port 8000
    pids=$(lsof -t -i:8000 2>/dev/null || true)
    if [ -n "$pids" ]; then
        warn "Port 8000 still occupied, force-killing..."
        for pid in $pids; do
            kill -9 "$pid" 2>/dev/null || true
        done
        sleep 1
    fi

    if $clean; then
        info "Cleaning Python cache..."
        find . -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
        find . -name "*.pyc" -delete 2>/dev/null || true
        ok "Cache cleaned."
    fi

    cmd_start
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

    echo -e "${BOLD}${RED}☢️  Hime Factory Reset${NC}"
    echo "═══════════════════════════════════════"
    if ! $skip_confirm; then
        warn "This will PERMANENTLY DELETE:"
        echo "  - All Agent Memory (Chat history, learned facts, token usage)"
        echo "  - All Ingested Data (Wearable health databases)"
        echo "  - All Personalised Pages (data/personalised_pages/)"
        echo "  - All System Logs"
        echo "  - Your learned User Profile (prompts/user.md)"
        echo ""
        echo -en "${YELLOW}Are you absolutely sure? (y/N) ${NC}"
        read -r -n 1 reply
        echo
        [[ ! "$reply" =~ ^[Yy]$ ]] && { echo "Aborted."; exit 0; }
    fi

    # 1. Stop all services
    cmd_stop

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
    
    # Also clear the original Live source (WatchExporter DB)
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
# logs — tail backend log
# ══════════════════════════════════════════════════════════════════
cmd_logs() {
    local target="${1:-all}"
    
    if [ "$target" == "all" ]; then
        info "Tailing all logs (backend, frontend, watch)..."
        # Check which logs actually exist to avoid errors
        local logs_to_tail=""
        for log_name in backend frontend watch; do
            if [ -f "logs/${log_name}.log" ]; then
                logs_to_tail="$logs_to_tail logs/${log_name}.log"
            fi
        done
        
        if [ -z "$logs_to_tail" ]; then
            fail "No log files found in logs/ directory."
        fi
        
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
# status — show what's running
# ══════════════════════════════════════════════════════════════════
cmd_status() {
    echo -e "${BOLD}Hime Status${NC}"
    echo "═══════════════════════════════════════"

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

    # Directory sizes
    echo ""
    info "Storage:"
    [ -d "logs" ]            && echo "  logs/     $(du -sh logs 2>/dev/null | cut -f1)"
    [ -d "memory" ]          && echo "  memory/   $(du -sh memory 2>/dev/null | cut -f1)"
    [ -d "data/data_stores" ] && echo "  data/     $(du -sh data/data_stores 2>/dev/null | cut -f1)"
}

# ══════════════════════════════════════════════════════════════════
# help
# ══════════════════════════════════════════════════════════════════
cmd_help() {
    cat << 'HELP'
Hime — unified platform CLI

Usage: ./hime.sh <command> [options]

Commands:
  start              Start all services (Backend, Frontend, Watch Exporter) in background.
  stop               Stop all running services.
  restart [--clean]  Stop everything and start fresh.
  status             Show running status and storage usage.
  logs [service]     Follow logs. Defaults to all services (backend, frontend, and watch).
  reset [--yes]      Delete agent memory and ingested data.
  forget [--yes]     Selective erasure: clear chat history but KEEP health data.
  help               Show this help message.

Options:
  --clean,  -c       Clear Python cache (for restart).
  --yes,    -y       Skip confirmation (for reset).

Log Directories:
  logs/backend.log     FastAPI backend logs
  logs/frontend.log    Vite development server logs
  logs/watch.log       Watch Exporter (Server/server.py) logs
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
