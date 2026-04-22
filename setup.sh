#!/usr/bin/env bash
# ============================================================================
# setup.sh -- HiMe one-command end-user setup (Docker stack).
# Interactive wizard: Docker prereqs -> LLM provider + key -> IM gateway ->
# write .env (chmod 600) -> docker compose build/up -> health checks.
# Run from project root. Re-runs safely (asks before overwriting .env).
# ============================================================================

set -euo pipefail

PROJECT_ROOT="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$PROJECT_ROOT"

ENV_FILE="$PROJECT_ROOT/.env"
ENV_EXAMPLE="$PROJECT_ROOT/.env.example"
COMPOSE_FILE="$PROJECT_ROOT/docker-compose.yml"

# ── Colours (respect NO_COLOR) ───────────────────────────────────────────────
if [ -n "${NO_COLOR:-}" ]; then
    RED=''; GREEN=''; YELLOW=''; CYAN=''; BOLD=''; NC=''
else
    RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'
    CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
fi

info()  { printf "${CYAN}%s${NC}\n" "$*"; }
ok()    { printf "${GREEN}* %s${NC}\n" "$*"; }
warn()  { printf "${YELLOW}! %s${NC}\n" "$*"; }
fail()  { printf "${RED}x %s${NC}\n" "$*" >&2; exit 1; }

# Wizard state — populated through the prompts.
# Parallel indexed arrays (bash 3.2-compatible; macOS ships /bin/bash 3.2 which
# lacks `declare -A`). Ordered insertion; duplicate keys updated in place.
WIZARD_KEYS=()
WIZARD_VALS=()
SETUP_STARTED_DOCKER=false       # set true once `docker compose up` is invoked

wizard_set() {
    local key="$1" val="$2" i
    for ((i=0; i<${#WIZARD_KEYS[@]}; i++)); do
        if [ "${WIZARD_KEYS[$i]}" = "$key" ]; then
            WIZARD_VALS[$i]="$val"
            return 0
        fi
    done
    WIZARD_KEYS+=("$key")
    WIZARD_VALS+=("$val")
}

wizard_get() {
    local key="$1" default="${2:-}" i
    for ((i=0; i<${#WIZARD_KEYS[@]}; i++)); do
        if [ "${WIZARD_KEYS[$i]}" = "$key" ]; then
            printf '%s' "${WIZARD_VALS[$i]}"
            return 0
        fi
    done
    printf '%s' "$default"
}

# ── Cleanup traps ────────────────────────────────────────────────────────────
_cleanup_on_abort() {
    local code=$?
    # Only act on abnormal exits (non-zero) and only if docker was started
    if [ "$code" -ne 0 ] && [ "$SETUP_STARTED_DOCKER" = true ]; then
        # Dump logs BEFORE teardown — otherwise `docker compose down` removes
        # the containers and `docker compose logs` finds nothing to show.
        warn "Aborted mid-setup. Last 50 log lines per container:"
        docker compose logs --tail=50 --no-color 2>&1 | sed 's/^/  /' || true
        warn "Tearing down partial Docker stack..."
        docker compose down >/dev/null 2>&1 || true
        printf "${YELLOW}Cleaned up partial Docker stack.${NC}\n"
    fi
}
trap _cleanup_on_abort EXIT

_on_sigint() {
    printf "\n"
    warn "Interrupted."
    if [ "$SETUP_STARTED_DOCKER" = true ]; then
        local ans
        read -r -p "Abort and cleanup? (Y/n): " ans || true
        ans="${ans:-Y}"
        if [[ ! "$ans" =~ ^[Nn]$ ]]; then
            docker compose down >/dev/null 2>&1 || true
            printf "${YELLOW}Cleaned up partial Docker stack.${NC}\n"
        fi
    fi
    exit 130
}
trap _on_sigint INT

# ── Banner ───────────────────────────────────────────────────────────────────
print_banner() {
    printf "\n${BOLD}${CYAN}"
    cat <<'BANNER'

  ██╗  ██╗ ██╗ ███╗   ███╗ ███████╗
  ██║  ██║ ╚═╝ ████╗ ████║ ██╔════╝
  ███████║ ██╗ ██╔████╔██║ █████╗
  ██╔══██║ ██║ ██║╚██╔╝██║ ██╔═══╝
  ██║  ██║ ██║ ██║ ╚═╝ ██║ ███████╗
  ╚═╝  ╚═╝ ╚═╝ ╚═╝     ╚═╝ ╚══════╝

BANNER
    printf "${NC}"
    printf "${CYAN}  One-command setup  •  Self-hosted health AI agent (Docker)${NC}\n\n"
}

# ============================================================================
# Step 0 — Pre-flight
# ============================================================================
preflight() {
    info "[0/10] Pre-flight checks..."

    # Project-root anchor: .env.example is always present in a clean checkout.
    # We don't require docker-compose.yml here — that check moves into the
    # docker-mode prereq helper so Native users aren't blocked by a missing
    # compose file.
    if [ ! -f "$ENV_EXAMPLE" ] && [ ! -f "$COMPOSE_FILE" ]; then
        fail "Run setup.sh from the HiMe project root (.env.example / docker-compose.yml missing)."
    fi

    _check_ports_free

    if [ -f "$ENV_FILE" ]; then
        local ans
        read -r -p "Found existing .env. Reconfigure? (y/N): " ans || true
        ans="${ans:-N}"
        if [[ ! "$ans" =~ ^[Yy]$ ]]; then
            info "Keeping existing .env."
            local existing_mode
            existing_mode="$(grep -E '^HIME_RUN_MODE=' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2 | tr -d '"'\''')"
            case "$existing_mode" in
                docker) info "Run 'docker compose up -d' (or './hime.sh start') to start." ;;
                native) info "Run './hime.sh start' to start." ;;
                *)      info "Run './hime.sh start' to start, or delete .env to start over." ;;
            esac
            # Disarm cleanup trap — this is a normal exit
            trap - EXIT
            exit 0
        fi
    fi
    ok "Pre-flight OK."
}

_check_docker_prereqs() {
    if [ ! -f "$COMPOSE_FILE" ]; then
        fail "docker-compose.yml not found in $PROJECT_ROOT. Run setup.sh from the HiMe project root."
    fi
    if ! command -v docker >/dev/null 2>&1; then
        fail "Docker is not installed. Install Docker Desktop: https://www.docker.com/products/docker-desktop/"
    fi
    if ! docker --version >/dev/null 2>&1; then
        fail "Docker is installed but 'docker --version' failed. Reinstall Docker Desktop."
    fi
    if ! docker compose version >/dev/null 2>&1; then
        fail "'docker compose' (v2 plugin) is missing. Update Docker Desktop or install the compose plugin."
    fi
    if ! docker info >/dev/null 2>&1; then
        fail "Docker daemon is not running. Start Docker Desktop / Docker daemon and re-run."
    fi
    ok "Docker and Docker Compose are ready."
}

_check_native_prereqs() {
    command -v python3 >/dev/null 2>&1 \
        || fail "python3 not found. Install Python 3.12+ (e.g. brew install python@3.12)."
    command -v npm >/dev/null 2>&1 \
        || fail "npm not found. Install Node.js 20+ (e.g. brew install node@20)."
    local py_major py_minor
    py_major="$(python3 -c 'import sys; print(sys.version_info[0])' 2>/dev/null || echo 0)"
    py_minor="$(python3 -c 'import sys; print(sys.version_info[1])' 2>/dev/null || echo 0)"
    if [ "$py_major" -lt 3 ] || { [ "$py_major" -eq 3 ] && [ "$py_minor" -lt 12 ]; }; then
        fail "Python 3.12+ required (found ${py_major}.${py_minor}). Upgrade Python."
    fi
    ok "python3 (${py_major}.${py_minor}) and npm are ready."
}

select_mode() {
    info "[1/10] Run mode"
    cat <<'MENU'

How do you want to run HiMe?
  1) Docker       (managed stack -- containers for backend/frontend/watch)
  2) Native       (processes on host -- needs python3.12+ and node.js 20+)
MENU
    local sel
    while true; do
        read -r -p "Selection [1]: " sel || true
        sel="${sel:-1}"
        case "$sel" in
            1) wizard_set HIME_RUN_MODE docker; break ;;
            2) wizard_set HIME_RUN_MODE native; break ;;
            *) warn "Pick 1 or 2." ;;
        esac
    done

    case "$(wizard_get HIME_RUN_MODE)" in
        docker) _check_docker_prereqs ;;
        native) _check_native_prereqs ;;
    esac
    ok "Run mode: $(wizard_get HIME_RUN_MODE)"
}

# HiMe's three published ports (see docker-compose.yml). If any of these are
# already held by another process, `docker compose up` will fail after a long
# image-build phase with a cryptic "bind: address already in use" error.
# Fail fast up front with a message that points the user at the likely cause.
_check_ports_free() {
    command -v lsof >/dev/null 2>&1 || return 0    # best-effort; skip if no lsof

    local held=() p holders
    for p in 5173 8000 8765; do
        if lsof -nP -iTCP:"$p" -sTCP:LISTEN >/dev/null 2>&1; then
            # Dedupe by PID — lsof prints one row per FD (IPv4 + IPv6 commonly).
            holders="$(
                lsof -nP -iTCP:"$p" -sTCP:LISTEN 2>/dev/null | awk '
                    NR > 1 && !seen[$2]++ {
                        if (out != "") out = out ", "
                        out = out $1 " (PID " $2 ")"
                    }
                    END { print out }
                '
            )"
            held+=("$p -> ${holders:-unknown}")
        fi
    done

    [ ${#held[@]} -eq 0 ] && return 0

    printf "${RED}x HiMe needs ports 5173/8000/8765 free, but some are in use:${NC}\n" >&2
    local item
    for item in "${held[@]}"; do
        printf "    %s\n" "$item" >&2
    done
    printf "\n" >&2
    printf "  This usually means HiMe is already running. Stop it first:\n" >&2
    printf "    ${BOLD}./hime.sh stop${NC}         # dev mode (started via 'hime.sh start')\n" >&2
    printf "    ${BOLD}docker compose down${NC}    # docker mode (previous setup.sh run)\n" >&2
    printf "\n" >&2
    printf "  Or inspect each port yourself:\n" >&2
    printf "    lsof -nP -iTCP:<port> -sTCP:LISTEN\n" >&2
    # Disarm cleanup trap — no Docker state to clean.
    trap - EXIT
    exit 1
}

# ============================================================================
# Step 1 — LLM provider
# ============================================================================
ALL_PROVIDERS="gemini openai anthropic mistral groq deepseek xai openrouter perplexity google_vertex amazon_bedrock minimax vllm azure_openai zhipuai"

select_provider() {
    info "[2/10] LLM provider"
    cat <<'MENU'

Choose your LLM provider:
  1) Gemini (Google)
  2) OpenAI
  3) Anthropic
  4) DeepSeek
  5) Azure OpenAI
  6) Other (Groq, Mistral, MiniMax, xAI, OpenRouter, Perplexity, vLLM, Bedrock, Vertex, Zhipu)
MENU
    local sel provider
    while true; do
        read -r -p "Selection [1]: " sel || true
        sel="${sel:-1}"
        case "$sel" in
            1) provider="gemini"; break ;;
            2) provider="openai"; break ;;
            3) provider="anthropic"; break ;;
            4) provider="deepseek"; break ;;
            5) provider="azure_openai"; break ;;
            6)
                read -r -p "Provider name (e.g. groq, mistral, minimax, xai, openrouter, perplexity, vllm, amazon_bedrock, google_vertex, zhipuai): " provider || true
                provider="$(printf '%s' "$provider" | tr '[:upper:]' '[:lower:]' | tr -d ' ')"
                if [[ " $ALL_PROVIDERS " == *" $provider "* ]]; then
                    break
                fi
                warn "Unknown provider '$provider'. Must be one of: $ALL_PROVIDERS"
                ;;
            *) warn "Pick 1-6." ;;
        esac
    done
    wizard_set DEFAULT_LLM_PROVIDER "$provider"
    ok "Provider: $provider"
}

# ============================================================================
# Step 2 — API key (env var depends on provider)
# ============================================================================
api_key_var_for() {
    case "$1" in
        gemini)         echo "GEMINI_API_KEY" ;;
        openai)         echo "OPENAI_API_KEY" ;;
        azure_openai)   echo "AZURE_OPENAI_API_KEY" ;;
        anthropic)      echo "ANTHROPIC_API_KEY" ;;
        deepseek)       echo "DEEPSEEK_API_KEY" ;;
        groq)           echo "GROQ_API_KEY" ;;
        mistral)        echo "MISTRAL_API_KEY" ;;
        minimax)        echo "MINIMAX_API_KEY" ;;
        xai)            echo "XAI_API_KEY" ;;
        openrouter)     echo "OPENROUTER_API_KEY" ;;
        perplexity)     echo "PERPLEXITY_API_KEY" ;;
        zhipuai)        echo "ZHIPUAI_API_KEY" ;;
        *)              echo "" ;;
    esac
}

collect_api_key() {
    info "[3/10] API key"
    local provider
    provider="$(wizard_get DEFAULT_LLM_PROVIDER)"

    case "$provider" in
        vllm|amazon_bedrock|google_vertex)
            warn "Provider '$provider' doesn't use a single bearer key."
            info "See docs/INSTALL.md after setup for IAM/ADC/local-endpoint configuration. Skipping key prompt."
            return 0
            ;;
    esac

    local var key
    var="$(api_key_var_for "$provider")"
    if [ -z "$var" ]; then
        warn "No env-var mapping for '$provider'. Skipping key prompt."
        return 0
    fi

    printf "Paste your %s API key (input hidden, Enter to skip): " "$(printf '%s' "$provider" | tr '[:lower:]' '[:upper:]')"
    read -r -s key || true
    printf "\n"
    if [ -z "$key" ]; then
        warn "Empty key — agent features won't work until you set $var in .env."
    fi
    wizard_set "$var" "$key"

    if [ "$provider" = "azure_openai" ]; then
        local endpoint
        while true; do
            read -r -p "Azure endpoint URL (e.g. https://my-resource.openai.azure.com/): " endpoint || true
            if [ -n "$endpoint" ]; then
                wizard_set AZURE_OPENAI_ENDPOINT "$endpoint"
                break
            fi
            warn "This is required."
        done
    fi
    ok "API key captured."
}

# ============================================================================
# Step 3 — Model override
# ============================================================================
# Read the per-provider default from .env.example (the value after the
# `#DEFAULT_MODEL_<PROVIDER>=` line). Provider is passed as UPPER_SNAKE.
_default_model_for_provider() {
    local p="$1"
    [ -f "$ENV_EXAMPLE" ] || return 0
    awk -v p="$p" '
        $0 ~ "^#?DEFAULT_MODEL_" p "=" {
            val = $0
            sub("^#?DEFAULT_MODEL_" p "=", "", val)
            sub(/[ \t]+#.*$/, "", val)
            sub(/[ \t]+$/, "", val)
            print val
            exit
        }
    ' "$ENV_EXAMPLE"
}

# Enumerate the small/medium/large model candidates advertised for a provider
# in .env.example. Strategy: walk back from `#DEFAULT_MODEL_<P>=` to the
# previous blank line, then from each comment line extract tokens separated
# by '|'. A line is a "model line" only when every |-separated token is a
# valid model name (alnum + [-._/:]), so descriptive headers like
# "Anthropic Claude 4.x: small | medium | large" get skipped.
_list_provider_models() {
    local p="$1"
    [ -f "$ENV_EXAMPLE" ] || return 0
    # Rule order matters: the DEFAULT_MODEL_<P>= match must run before the
    # generic /^#/ { next } catch-all, otherwise the catch-all consumes the
    # line before our block-terminator logic fires.
    awk -v p="$p" '
        $0 ~ "^#?DEFAULT_MODEL_" p "=" {
            for (i = 0; i < n; i++) {
                line = block[i]
                sub(/^#[ \t]*/, "", line)
                gsub(/\([^)]*\)/, "", line)          # strip parenthetical notes
                gsub(/[ \t]+/, " ", line)             # collapse ws
                sub(/^ +/, "", line); sub(/ +$/, "", line)
                if (line == "") continue
                m = split(line, parts, / *\| */)
                keep = 1
                for (j = 1; j <= m; j++) {
                    tok = parts[j]
                    if (tok == "" || tok ~ /[ \t]/) { keep = 0; break }
                    if (tok !~ /^[A-Za-z0-9][-A-Za-z0-9._\/:]*$/) { keep = 0; break }
                    if (tok ~ /^(small|medium|large|mid|flagship|flash|chat|reasoner|search|reasoning|legacy)$/) { keep = 0; break }
                }
                if (!keep) continue
                for (j = 1; j <= m; j++) {
                    tok = parts[j]
                    if (tok != "") print tok
                }
            }
            exit
        }
        /^$/       { n = 0; next }
        /^#/       { block[n++] = $0; next }
                   { n = 0 }
    ' "$ENV_EXAMPLE"
}

collect_model() {
    info "[4/10] Model override"
    local provider provider_upper default_model opts_raw
    provider="$(wizard_get DEFAULT_LLM_PROVIDER)"
    provider_upper="$(printf '%s' "$provider" | tr '[:lower:]' '[:upper:]')"
    default_model="$(_default_model_for_provider "$provider_upper")"
    opts_raw="$(_list_provider_models "$provider_upper")"

    # Load options into an indexed array (bash 3.2-compatible — no mapfile).
    local -a opts=()
    local line
    while IFS= read -r line; do
        [ -n "$line" ] && opts+=("$line")
    done <<EOF
$opts_raw
EOF

    printf "\n"
    if [ -n "$default_model" ]; then
        printf "  Built-in default for ${BOLD}%s${NC}: ${BOLD}%s${NC}\n" "$provider" "$default_model"
    else
        printf "  No built-in default registered for ${BOLD}%s${NC} in .env.example.\n" "$provider"
        printf "  (vLLM resolves its model from VLLM_MODEL; Bedrock/Vertex use IAM/ADC.)\n"
    fi

    if [ ${#opts[@]} -gt 0 ]; then
        printf "  Candidates advertised in .env.example:\n"
        local i marker
        for ((i=0; i<${#opts[@]}; i++)); do
            if [ "${opts[$i]}" = "$default_model" ]; then
                marker="  (default)"
            else
                marker=""
            fi
            printf "    %d) %s%s\n" $((i+1)) "${opts[$i]}" "$marker"
        done
        printf "\n"

        local choice
        read -r -p "Pick 1-${#opts[@]}, type a custom model name, or press Enter to keep the default: " choice || true

        if [ -z "$choice" ]; then
            # Explicit empty so write_env clears any prior override.
            wizard_set DEFAULT_MODEL ""
            ok "Using provider default."
            return 0
        fi

        if [[ "$choice" =~ ^[0-9]+$ ]] && [ "$choice" -ge 1 ] && [ "$choice" -le ${#opts[@]} ]; then
            local picked="${opts[$((choice-1))]}"
            if [ "$picked" = "$default_model" ]; then
                wizard_set DEFAULT_MODEL ""
                ok "Using provider default: $picked"
            else
                wizard_set DEFAULT_MODEL "$picked"
                ok "Model: $picked"
            fi
            return 0
        fi

        # Anything else → treat as literal model name.
        wizard_set DEFAULT_MODEL "$choice"
        ok "Model: $choice (custom)"
        return 0
    fi

    # No enumerated options — fall back to free text.
    local model
    read -r -p "Override model? (press Enter to keep provider default): " model || true
    if [ -n "$model" ]; then
        wizard_set DEFAULT_MODEL "$model"
        ok "Model: $model"
    else
        wizard_set DEFAULT_MODEL ""
        ok "Using provider default."
    fi
}

# ============================================================================
# Step 4 — Timezone
# ============================================================================
# Detect the system's IANA timezone (e.g. "Asia/Shanghai", "Europe/London").
# Strategies in order: systemd (Linux), /etc/timezone (Debian/Ubuntu), the
# /etc/localtime symlink (macOS + most Linux). Falls back to Europe/London
# only when every method fails (effectively never on a correctly-configured
# host).
_detect_timezone() {
    local tz link
    if command -v timedatectl >/dev/null 2>&1; then
        tz="$(timedatectl show --property=Timezone --value 2>/dev/null || true)"
        [ -n "$tz" ] && { printf '%s' "$tz"; return 0; }
    fi
    if [ -r /etc/timezone ]; then
        tz="$(tr -d '[:space:]' < /etc/timezone 2>/dev/null || true)"
        [ -n "$tz" ] && { printf '%s' "$tz"; return 0; }
    fi
    if [ -L /etc/localtime ]; then
        link="$(readlink /etc/localtime 2>/dev/null || true)"
        case "$link" in
            */zoneinfo/*) printf '%s' "${link##*/zoneinfo/}"; return 0 ;;
        esac
    fi
    printf 'Europe/London'
}

collect_timezone() {
    info "[5/10] Timezone"
    local detected tz
    detected="$(_detect_timezone)"
    read -r -p "Timezone for daily/scheduled analyses [${detected}]: " tz || true
    wizard_set TIMEZONE "${tz:-$detected}"
    ok "Timezone: $(wizard_get TIMEZONE)"
}

# Detect the Mac/Linux host's LAN IPv4 address — the one an iPhone on the same
# Wi-Fi should point at. Strategies, in order of reliability:
#   1. macOS: `ipconfig getifaddr enN` for common interfaces (en0 Wi-Fi or
#      first Ethernet, en1 the other, USB-C adapters at en2/en3).
#   2. Linux: source IP of the default route (`ip route get 1.1.1.1`) — this
#      is what outbound traffic would use, which matches what LAN clients see.
#   3. Fallbacks: `hostname -I`, then `ifconfig` grep for a non-loopback inet.
# Prints nothing (returns 1) when no LAN IP is found (e.g. host is offline).
_detect_lan_ip() {
    local ip="" iface
    if command -v ipconfig >/dev/null 2>&1; then
        for iface in en0 en1 en2 en3 en4 en5; do
            ip="$(ipconfig getifaddr "$iface" 2>/dev/null || true)"
            [ -n "$ip" ] && { printf '%s' "$ip"; return 0; }
        done
    fi
    if command -v ip >/dev/null 2>&1; then
        ip="$(ip route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}' || true)"
        [ -n "$ip" ] && { printf '%s' "$ip"; return 0; }
    fi
    if command -v hostname >/dev/null 2>&1; then
        ip="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
        [ -n "$ip" ] && { printf '%s' "$ip"; return 0; }
    fi
    if command -v ifconfig >/dev/null 2>&1; then
        ip="$(ifconfig 2>/dev/null | awk '/inet / && $2 != "127.0.0.1" {print $2; exit}' || true)"
        [ -n "$ip" ] && { printf '%s' "$ip"; return 0; }
    fi
    return 1
}

# ============================================================================
# Step 5 — IM gateway (REQUIRED)
# ============================================================================
collect_required() {
    # Prompts $1 (label) until non-empty; writes value into the caller's
    # variable named by $2. Uses printf -v instead of `local -n` for bash 3.2.
    local label="$1"
    local _out_var="$2"
    local hidden="${3:-}"
    local val=""
    while [ -z "$val" ]; do
        if [ "$hidden" = "hidden" ]; then
            printf "%s: " "$label"
            read -r -s val || true
            printf "\n"
        else
            read -r -p "$label: " val || true
        fi
        if [ -z "$val" ]; then
            warn "This field is required for chat to work."
        fi
    done
    printf -v "$_out_var" '%s' "$val"
}

select_gateway() {
    info "[6/10] IM gateway (REQUIRED)"
    cat <<'MENU'

Choose your messaging gateway (REQUIRED -- HiMe chats with you over IM):
  1) Telegram
  2) Feishu (Lark)
MENU
    local sel
    while true; do
        read -r -p "Selection [1]: " sel || true
        sel="${sel:-1}"
        case "$sel" in
            1) gateway_telegram; break ;;
            2) gateway_feishu;   break ;;
            *) warn "Pick 1 or 2." ;;
        esac
    done
}

gateway_telegram() {
    cat <<'TIP'

Tip:
  - Get your bot token from @BotFather (Telegram).
  - Get your chat_id by sending /start to @userinfobot, OR
    see docs/INSTALL.md§Telegram-setup for the full walkthrough.
  - chat_id is a signed integer:
      * private 1-to-1 chat : positive  (e.g. 123456789)
      * group                : negative  (e.g. -1234567890)
      * supergroup / channel : negative, starts with -100  (e.g. -1001234567890)
    Paste it verbatim -- INCLUDING the leading '-' for groups.
TIP
    local token chat_id
    collect_required "Telegram bot token" token hidden
    collect_required "Your Telegram chat_id (numeric; include leading '-' for groups)" chat_id

    wizard_set TELEGRAM_TOKEN "$token"
    wizard_set CHAT_ID "$chat_id"
    wizard_set TELEGRAM_ALLOWED_CHAT_IDS "$chat_id"
    wizard_set TELEGRAM_GATEWAY_ENABLED "true"
    wizard_set TELEGRAM_POLL_TIMEOUT "30"
    wizard_set FEISHU_GATEWAY_ENABLED "false"
    wizard_set __GATEWAY_LABEL__ "Telegram"
    ok "Telegram gateway configured."
}

gateway_feishu() {
    cat <<'TIP'

Tip:
  - Create a custom app in https://open.feishu.cn -> Credentials.
  - Find chat_id by inviting the bot to a group and using its
    /api/v2/chat/list API (see docs/INSTALL.md§Feishu-setup).
TIP
    local app_id app_secret chat_id
    collect_required "Feishu APP_ID (cli_xxx)" app_id
    collect_required "Feishu APP_SECRET" app_secret hidden
    collect_required "Feishu open_chat_id (oc_xxx)" chat_id

    wizard_set FEISHU_APP_ID "$app_id"
    wizard_set FEISHU_APP_SECRET "$app_secret"
    wizard_set FEISHU_DEFAULT_CHAT_ID "$chat_id"
    wizard_set FEISHU_ALLOWED_CHAT_IDS "$chat_id"
    wizard_set FEISHU_GATEWAY_ENABLED "true"
    wizard_set FEISHU_TRANSPORT "ws"
    wizard_set TELEGRAM_GATEWAY_ENABLED "false"
    wizard_set __GATEWAY_LABEL__ "Feishu"
    ok "Feishu gateway configured."
}

# ============================================================================
# Step 6 — API auth token (optional, public-facing only)
# ============================================================================
gen_token() {
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -hex 32
    else
        head -c 32 /dev/urandom | xxd -p -c 32
    fi
}

collect_auth_token() {
    info "[7/10] API auth token"
    local ans
    read -r -p "Will this server be reachable from the public internet? (y/N): " ans || true
    ans="${ans:-N}"
    if [[ "$ans" =~ ^[Yy]$ ]]; then
        local tok
        tok="$(gen_token)"
        wizard_set API_AUTH_TOKEN "$tok"
        ok "Generated bearer token: $tok"
        warn "SAVE THIS — you'll paste it into the iOS app's Auth Token field."
    else
        wizard_set API_AUTH_TOKEN ""
        info "OK — local-only mode, no auth token."
    fi
}

# ============================================================================
# Step 7 — Write .env
# ============================================================================
needs_quoting() {
    # Quote if value contains whitespace, '#', or any non-(alnum/._:/=@-) char.
    # docker compose's env_file accepts unquoted ASCII for typical secrets, but
    # spaces/specials need single-quoting to survive both .env parsing and
    # downstream shell-style consumers (e.g. hime.sh).
    case "$1" in
        *[!A-Za-z0-9._:/=@-]*) return 0 ;;
        *) return 1 ;;
    esac
}

format_value() {
    # Echo the value, single-quoted (with embedded ' -> '\'' escaping) if needed.
    local v="$1"
    if [ -z "$v" ]; then
        printf '%s' ""
        return
    fi
    if ! needs_quoting "$v"; then
        printf '%s' "$v"
        return
    fi
    local out="" i ch
    for ((i=0; i<${#v}; i++)); do
        ch="${v:i:1}"
        if [ "$ch" = "'" ]; then out+="'\\''"; else out+="$ch"; fi
    done
    printf "'%s'" "$out"
}

write_env() {
    info "[8/10] Writing .env"

    local tmp
    tmp="$(mktemp "$PROJECT_ROOT/.env.tmp.XXXXXX")"

    # Seed: prefer existing .env (user re-running wizard), else .env.example
    if [ -f "$ENV_FILE" ]; then
        cp "$ENV_FILE" "$tmp"
    elif [ -f "$ENV_EXAMPLE" ]; then
        cp "$ENV_EXAMPLE" "$tmp"
    else
        : > "$tmp"
    fi

    # In-place upsert: replace `KEY=...` if found, else collect for append-block.
    local appended_any=false
    local appended_tmp
    appended_tmp="$(mktemp)"

    local _idx key raw formatted new_line
    for ((_idx=0; _idx<${#WIZARD_KEYS[@]}; _idx++)); do
        key="${WIZARD_KEYS[$_idx]}"
        # Skip internal sentinel keys
        [[ "$key" == __* ]] && continue

        raw="${WIZARD_VALS[$_idx]}"
        formatted="$(format_value "$raw")"
        new_line="${key}=${formatted}"

        if grep -Eq "^${key}=" "$tmp"; then
            # Use a python-free, sed-safe replacement via awk to avoid escaping hell.
            local awk_tmp
            awk_tmp="$(mktemp)"
            KEY="$key" LINE="$new_line" awk '
                BEGIN { k = ENVIRON["KEY"]; line = ENVIRON["LINE"]; pat = "^" k "=" }
                {
                    if ($0 ~ pat) { print line } else { print $0 }
                }
            ' "$tmp" > "$awk_tmp"
            mv "$awk_tmp" "$tmp"
        else
            printf '%s\n' "$new_line" >> "$appended_tmp"
            appended_any=true
        fi
    done

    if [ "$appended_any" = true ]; then
        {
            printf '\n'
            printf '# ============================================================================\n'
            printf '# Settings written by setup.sh\n'
            printf '# ============================================================================\n'
            cat "$appended_tmp"
        } >> "$tmp"
    fi
    rm -f "$appended_tmp"

    mv "$tmp" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    ok ".env written (mode 600)."
}

# ============================================================================
# Step 8 — docker compose pull / build / up
# ============================================================================
docker_step() {
    local label="$1"; shift
    info ">> $label"
    if ! "$@"; then
        printf "${RED}x %s failed.${NC}\n" "$label" >&2
        # The EXIT trap (_cleanup_on_abort) dumps container logs below before
        # tearing down, so we don't suggest a separate `docker compose logs`.
        exit 1
    fi
}

start_stack() {
    info "[9/10] Building & starting Docker stack..."
    # Pull is best-effort: our locally-built images won't be in a registry,
    # but this warms shared base layers (python:3.12-slim, node:20-slim, etc.)
    docker compose pull --quiet 2>/dev/null || true

    docker_step "Building images (this can take several minutes the first time)" \
        docker compose build

    SETUP_STARTED_DOCKER=true
    docker_step "Starting containers" \
        docker compose up -d
    ok "Containers started."
}

# ============================================================================
# Step 9 — Health checks (90s budget)
# ============================================================================
wait_for() {
    local name="$1" url="$2" hdr="${3:-}" deadline=$(( $(date +%s) + 90 ))
    while [ "$(date +%s)" -lt "$deadline" ]; do
        if [ -n "$hdr" ]; then
            curl -fsS -H "$hdr" "$url" >/dev/null 2>&1 && { ok "$name ready"; return 0; }
        else
            curl -fsS "$url" >/dev/null 2>&1 && { ok "$name ready"; return 0; }
        fi
        sleep 2
    done
    printf "${RED}x %s not responding -- check 'docker compose logs %s'${NC}\n" \
        "$name" "$(printf '%s' "$name" | tr '[:upper:]' '[:lower:]')"
    return 1
}

health_checks() {
    info "[10/10] Waiting for services (up to 90s each)..."
    local auth_hdr="" tok
    tok="$(wizard_get API_AUTH_TOKEN)"
    if [ -n "$tok" ]; then
        auth_hdr="Authorization: Bearer $tok"
    fi
    wait_for "Watch"    "http://localhost:8765/ping" || true
    wait_for "Backend"  "http://localhost:8000/health" "$auth_hdr" || true
    wait_for "Frontend" "http://localhost:5173/" || true
}

# ============================================================================
# Final summary
# ============================================================================
print_summary() {
    local gw mode lan_ip lan_line
    gw="$(wizard_get __GATEWAY_LABEL__ Telegram)"
    mode="$(wizard_get HIME_RUN_MODE docker)"
    lan_ip="$(_detect_lan_ip || true)"
    if [ -n "$lan_ip" ]; then
        lan_line="Server Address: ${lan_ip}         <- this Mac's LAN IP (detected just now)"
    else
        lan_line="Server Address: 192.168.1.100    (couldn't auto-detect; run 'ipconfig getifaddr en0')"
    fi
    printf "\n"
    printf "${GREEN}${BOLD}HiMe is running.${NC} (${mode} mode)\n\n"
    cat <<EOF
  Dashboard:        http://localhost:5173
  Backend API:      http://localhost:8000
  Watch exporter:   http://localhost:8765/ping

Next steps:
  1) Open the dashboard: http://localhost:5173
     -> go to "Agent Monitor" -> click Start.
     (The provider/model selectors pre-fill from your .env -- just click Start.)
  2) Install the HiMe iOS app:
       https://apps.apple.com/app/id6762160735      (App Store)
       OR build from source -- see docs/INSTALL.md
  3) Open the iOS app -> Settings -> "Server Address".
     Enter a BARE HOST only -- the app adds scheme/port automatically
     (http on LAN, https on tunnel; ports 8000 for API, 8765 for watch):
       - iOS Simulator or Mac Catalyst (iPhone IS the host)
           Server Address: localhost
       - iPhone on the SAME Wi-Fi as the backend (most common)
           ${lan_line}
       - iPhone on mobile data / remote (public deployment)
           Server Address: example.com          (ROOT domain, no subdomain)
           Your tunnel MUST route two subdomains to the host:
             api.example.com    -> backend  (port 8000)
             watch.example.com  -> watch    (port 8765)
           See docs/DEPLOYMENT.md for the tunnel walkthrough.
  4) Send the bot a message in ${gw} to start chatting

Managing the stack (both modes):
  ./hime.sh stop           Stop all services
  ./hime.sh restart        Restart (applies .env changes)
  ./hime.sh restart --rebuild   (docker only) also rebuild images
  ./hime.sh logs           Follow logs
  ./hime.sh status         Show health + storage
  ./hime.sh reset          Factory reset (wipes data/memory/logs)
EOF
}

# ============================================================================
# Main
# ============================================================================
main() {
    print_banner
    preflight
    select_mode
    select_provider
    collect_api_key
    collect_model
    collect_timezone
    select_gateway
    collect_auth_token
    write_env

    # Step 9-10 — bring the stack up. Docker builds images and waits on
    # container health. Native hands off to `hime.sh start`, which installs
    # deps, launches processes, and does its own health probes.
    if [ "$(wizard_get HIME_RUN_MODE)" = native ]; then
        info "[9/10] Starting native stack..."
        print_summary
        ok "Handing off to './hime.sh start' ..."
        # Disarm cleanup trap — no Docker state to clean up.
        trap - EXIT
        exec "$PROJECT_ROOT/hime.sh" start
    fi

    start_stack
    health_checks
    print_summary
    # Disarm cleanup trap on success
    trap - EXIT
}

main "$@"
