"""
Application configuration — loaded from environment variables / .env file.

All settings are documented inline.  Boolean values can be set as
``true``/``false``/``1``/``0`` in the .env file (Pydantic handles the parsing).
"""
import logging
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """
    Central settings for the HIME platform.

    Override any setting by setting the corresponding environment variable
    or adding it to the ``.env`` file in the project root.
    """

    # ------------------------------------------------------------------ #
    # LLM provider keys
    # ------------------------------------------------------------------ #
    OPENAI_API_KEY:           str | None = None
    ANTHROPIC_API_KEY:        str | None = None
    GOOGLE_API_KEY:           str | None = None
    GEMINI_API_KEY:           str | None = None
    DEEPSEEK_API_KEY:         str | None = None
    GROQ_API_KEY:             str | None = None
    XAI_API_KEY:              str | None = None
    OPENROUTER_API_KEY:       str | None = None
    PERPLEXITY_API_KEY:       str | None = None
    MISTRAL_API_KEY:          str | None = None
    MINIMAX_API_KEY:          str | None = None

    # Google Cloud / Vertex AI
    GOOGLE_CLOUD_API_KEY:     str | None = None
    GOOGLE_CLOUD_PROJECT:     str | None = None
    GOOGLE_CLOUD_LOCATION:    str           = "us-central1"

    # AWS / Bedrock
    AWS_ACCESS_KEY_ID:        str | None = None
    AWS_SECRET_ACCESS_KEY:    str | None = None
    AWS_REGION:               str           = "us-east-1"

    # Azure OpenAI (when DEFAULT_LLM_PROVIDER=azure_openai)
    AZURE_OPENAI_ENDPOINT:    str | None = None
    AZURE_OPENAI_API_KEY:     str | None = None
    AZURE_OPENAI_API_VERSION: str           = "2024-12-01-preview"

    # vLLM local server (when DEFAULT_LLM_PROVIDER=vllm)
    VLLM_BASE_URL:            str           = "http://localhost:8421/v1"
    VLLM_MODEL:               str           = "gemma-4-26b-a4b"

    # ------------------------------------------------------------------ #
    # Agent defaults
    # ------------------------------------------------------------------ #
    DEFAULT_LLM_PROVIDER: str = "gemini"
    DEFAULT_MODEL:        str = "gemini-3.1-flash-lite-preview"
    # Optional fallback provider/model. When set, any LLM call that exhausts
    # the primary provider's capacity-error retry budget (3 consecutive
    # 503/529/overloaded responses) is transparently re-issued against this
    # backup provider. Leave empty to disable.
    FALLBACK_LLM_PROVIDER: str | None = None
    FALLBACK_LLM_MODEL:    str | None = None

    # Per-provider default model used as the dashboard placeholder when the
    # user has not chosen a specific model. These are also the values
    # ``create_provider(provider, model=None)`` will fall back to. Override
    # any of them in .env to change what the dashboard suggests.
    # Note: AZURE_OPENAI uses the Azure deployment name (not the model name).
    # vLLM is intentionally absent here — it is always resolved from VLLM_MODEL.
    DEFAULT_MODEL_GEMINI:         str = "gemini-3.1-flash-lite-preview"
    DEFAULT_MODEL_OPENAI:         str = "gpt-4.5-preview"
    DEFAULT_MODEL_AZURE_OPENAI:   str = "gpt-4.5-preview"
    DEFAULT_MODEL_ANTHROPIC:      str = "claude-opus-4-5"
    DEFAULT_MODEL_MISTRAL:        str = "mistral-large-latest"
    DEFAULT_MODEL_GROQ:           str = "llama-3.3-70b-versatile"
    DEFAULT_MODEL_DEEPSEEK:       str = "deepseek-chat"
    DEFAULT_MODEL_XAI:            str = "grok-beta"
    DEFAULT_MODEL_OPENROUTER:     str = "anthropic/claude-3.5-sonnet"
    DEFAULT_MODEL_PERPLEXITY:     str = "llama-3.1-sonar-huge-128k-pro"
    DEFAULT_MODEL_GOOGLE_VERTEX:  str = "gemini-3.1-flash-lite-preview"
    DEFAULT_MODEL_AMAZON_BEDROCK: str = "amazon.nova-pro-v1:0"
    DEFAULT_MODEL_MINIMAX:        str = "abab6.5s-chat"
    DEFAULT_MODEL_ZHIPUAI:        str = "glm-4.7-flash"

    # Reasoning effort for OpenAI / Azure OpenAI reasoning models (gpt-5,
    # gpt-5-mini, gpt-5-nano, o1, o3, o4 ...). When set, this is forwarded
    # as the ``reasoning_effort`` parameter on every chat completion call to
    # OpenAI / Azure providers. Leave empty to use the API server-side default
    # (which for GPT-5 family is ``medium`` — usually too expensive/slow for
    # agentic tool-calling loops).
    # Valid values: ``minimal`` (GPT-5 originals only), ``low``, ``medium``,
    # ``high``, ``xhigh`` (gpt-5.1-codex-max only), ``none`` (gpt-5.1+ only).
    # NOTE: ``minimal`` requires parallel tool calls to be disabled — HIME
    # automatically sets ``parallel_tool_calls=False`` in that case.
    OPENAI_REASONING_EFFORT: str | None = None
    AUTO_RESTORE_AGENT:   bool = False   # restore last agent on startup
    AGENT_MAX_ITERATIONS: int  = 100     # hard safety limit for loop iterations
    AGENT_CONTEXT_WINDOW_SIZE: int = 20  # turn groups to keep in full context (rest auto-summarized)
    AGENT_MAX_TOKENS:     int  = 0       # 0 = no limit (let vLLM use max_model_len - input_tokens)
    AGENT_MAX_CONTEXT_TOKENS: int = 180_000  # max context window budget for token-based compression
    CHAT_MAX_TURNS:       int  = 20      # hard cap for chat loop (separate from analysis budget)
    CHAT_HISTORY_SIZE:    int  = 20      # sliding window per gateway chat (messages, not turns)
    # How far back the agent reads context on startup (hours)
    AGENT_CONTEXT_WINDOW_HOURS: int = 24
    # Fact verification: separate LLM provider/model (empty = use agent's provider)
    FACT_VERIFY_LLM_PROVIDER: str = ""
    FACT_VERIFY_LLM_MODEL:    str = ""

    # ------------------------------------------------------------------ #
    # Code tool sandbox
    # ------------------------------------------------------------------ #
    # Set to true to run agent-generated Python inside a Docker container
    # with --network none and strict resource limits.
    # Requires Docker Engine / Docker Desktop to be running.
    CODE_TOOL_DOCKER_SANDBOX: bool = False
    CODE_TOOL_DOCKER_IMAGE:   str  = "python:3.12-slim"

    # ------------------------------------------------------------------ #
    # Data sources
    # ------------------------------------------------------------------ #
    DATA_SOURCE: str = "live"

    # ------------------------------------------------------------------ #
    # Localization
    # ------------------------------------------------------------------ #
    # Default language for user-facing backend messages (reply_user fallbacks,
    # gateway error toasts, card headers, etc.). The i18n module in
    # ``backend/i18n/`` loads ``locales/<lang>.json`` and uses this as the
    # fallback when no explicit ``lang=`` is passed to ``t()``. Unknown values
    # silently fall back to ``'en'`` with a warning log.
    DEFAULT_USER_LANGUAGE: str = "en"

    # ------------------------------------------------------------------ #
    # Data retention
    # ------------------------------------------------------------------ #
    # Strict rolling retention window (in days) applied to ALL health and
    # agent-memory tables by the daily retention loop in
    # ``backend/agent/retention.py``. Regardless of how long HIME has been
    # running, only the most recent ``DATA_RETENTION_DAYS`` days of data are
    # kept on disk. Set to a very large value (e.g. 36500) to effectively
    # disable pruning.
    DATA_RETENTION_DAYS: int = 30

    # ------------------------------------------------------------------ #
    # Storage paths
    # ------------------------------------------------------------------ #
    # data/  — wearable sensor data only (parquet, ingested health DBs)
    DATA_STORE_PATH:        Path = Path("./data/data_stores")
    # logs/  — all log files (backend, frontend, LLM API CSV)
    AGENT_LOGS_PATH:        Path = Path("./logs")
    # memory/ — all agent memory (SQLite DBs, state JSON, last config)
    MEMORY_DB_PATH:         Path = Path("./memory")
    AGENT_STATES_PATH:      Path = Path("./memory/agent_states")
    AGENT_LAST_CONFIG_PATH: Path = Path("./memory/agent_last_config.json")
    APP_STATE_PATH:         Path = Path("./memory/app_state.json")

    # ------------------------------------------------------------------ #
    # Networking
    # ------------------------------------------------------------------ #
    API_HOST: str   = "0.0.0.0"
    API_PORT: int   = 8000
    TIMEZONE: str   = "Europe/London"  # Default timezone for agent analysis
    CORS_ORIGINS: list[str] = [
        "http://localhost:5173",
        "http://localhost:3000",
    ]
    # Optional bearer token for API authentication. When set, all /api/*
    # routes require an Authorization: Bearer <token> header. Leave empty to
    # disable auth (fine for localhost; NOT safe for public deployments).
    API_AUTH_TOKEN: str | None = None
    # Public URLs for display in logs/scripts
    DASHBOARD_URL: str | None = None
    API_URL:       str | None = None
    WATCH_URL:     str | None = None

    # Cloudflared paths (used by hime.sh primarily, but defined here for Pydantic safety)
    TUNNEL_DIR:    str | None = None
    TUNNEL_SCRIPT: str | None = None

    # ------------------------------------------------------------------ #
    # Messaging gateways — Telegram
    # ------------------------------------------------------------------ #
    # ``CHAT_ID`` is the default Telegram chat for autonomous pushes and
    # legacy single-gateway paths. Feishu has its own ``FEISHU_DEFAULT_CHAT_ID``
    # (see below) so the two platforms never share a fallback destination.
    CHAT_ID:        str | None = None
    TELEGRAM_TOKEN: str | None = None

    # Bidirectional Telegram Gateway
    TELEGRAM_GATEWAY_ENABLED: bool = False
    TELEGRAM_POLL_TIMEOUT:    int  = 30
    TELEGRAM_WAKE_ON_MESSAGE: bool = True
    # Comma-separated chat IDs allowed to interact (empty = only CHAT_ID)
    TELEGRAM_ALLOWED_CHAT_IDS: str = ""
    TELEGRAM_GROUP_LINK: str | None = None

    # ------------------------------------------------------------------ #
    # Feishu (Lark) Gateway
    # ------------------------------------------------------------------ #
    # Bidirectional Feishu Gateway — mirrors the Telegram gateway semantics
    # (default-deny allowlist, lazy SDK import, evidence-button cards).
    FEISHU_GATEWAY_ENABLED: bool = False
    FEISHU_APP_ID:          str  = ""
    FEISHU_APP_SECRET:      str  = ""
    # Transport selector: "ws" (long-lived WebSocket via lark_oapi) or
    # "webhook" (HTTP POST mounted on the FastAPI app).
    FEISHU_TRANSPORT:       str  = "ws"
    FEISHU_WEBHOOK_PATH:    str  = "/api/feishu/webhook"
    FEISHU_VERIFICATION_TOKEN: str = ""
    FEISHU_ENCRYPT_KEY:     str  = ""
    FEISHU_DEFAULT_CHAT_ID: str  = ""
    # Comma-separated open_chat_ids allowed to interact (empty = default-deny).
    FEISHU_ALLOWED_CHAT_IDS: str = ""

    # ------------------------------------------------------------------ #
    # Skills subsystem (openclaw-compatible capability packs)
    # ------------------------------------------------------------------ #
    # os.pathsep-separated list of directories containing skill packages.
    # Each directory is scanned for ``<name>/SKILL.md`` (and, if present,
    # ``<root>/skills/<name>/SKILL.md``).  In addition to this setting,
    # the registry also consults ``~/.hime/skills`` and ``./skills`` for
    # user-local and bundled skills.  Leave blank to only use those
    # defaults.
    HIME_SKILLS_DIR: str = "./skills"

    # ------------------------------------------------------------------ #
    # Validation helpers
    # ------------------------------------------------------------------ #

    def validate_llm_keys(self) -> bool:
        """Check whether at least one LLM API key is configured.

        Returns True if a key is found, False otherwise.
        Logs a warning when no keys are set — this is intentionally *not*
        a hard error so the data pipeline can still run without an agent.
        """
        keys = [
            self.OPENAI_API_KEY,
            self.ANTHROPIC_API_KEY,
            self.GEMINI_API_KEY,
            self.GOOGLE_API_KEY,
            self.DEEPSEEK_API_KEY,
            self.GROQ_API_KEY,
            self.XAI_API_KEY,
            self.OPENROUTER_API_KEY,
            self.PERPLEXITY_API_KEY,
            self.MISTRAL_API_KEY,
            self.MINIMAX_API_KEY,
            self.AZURE_OPENAI_API_KEY,
            self.AWS_ACCESS_KEY_ID,
        ]
        if not any(keys):
            logger.warning(
                "No LLM API keys configured. Agent features will not work. "
                "Set at least one key in .env (e.g. GEMINI_API_KEY, OPENAI_API_KEY)."
            )
            return False
        return True

    # ------------------------------------------------------------------ #
    # Pydantic settings
    # ------------------------------------------------------------------ #
    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
        extra="ignore",
    )


# Module-level singleton
settings = Settings()
