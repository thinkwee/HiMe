"""
LLM provider base + shared utilities.

Defines the contract (`BaseLLMProvider`) and all reusable helpers:
  - CSV usage logging
  - Exponential-backoff retry
  - Message / tool-call helpers
"""
from __future__ import annotations

import asyncio
import contextvars
import csv
import json
import logging
import random
import threading
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Provider enum
# ---------------------------------------------------------------------------

class LLMProvider(str, Enum):
    OPENAI         = "openai"
    AZURE_OPENAI   = "azure_openai"
    ANTHROPIC      = "anthropic"
    GEMINI         = "gemini"
    MISTRAL        = "mistral"
    GROQ           = "groq"
    DEEPSEEK       = "deepseek"
    XAI            = "xai"
    OPENROUTER     = "openrouter"
    PERPLEXITY     = "perplexity"
    GOOGLE_VERTEX  = "google_vertex"
    AMAZON_BEDROCK = "amazon_bedrock"
    MINIMAX        = "minimax"
    VLLM           = "vllm"
    ZHIPUAI        = "zhipuai"


# ---------------------------------------------------------------------------
# API Key discovery (inspired by pi-mono/packages/ai/src/env-api-keys.ts)
# ---------------------------------------------------------------------------

def get_env_api_key(provider: str | LLMProvider) -> str | None:
    """
    Get API key for provider from environment variables.
    """
    import os
    prov = provider.value if isinstance(provider, LLMProvider) else str(provider).lower()

    env_map = {
        "openai":            "OPENAI_API_KEY",
        "azure_openai":      "AZURE_OPENAI_API_KEY",
        "anthropic":         "ANTHROPIC_API_KEY",
        "gemini":            "GEMINI_API_KEY",
        "google":            "GEMINI_API_KEY",
        "groq":              "GROQ_API_KEY",
        "deepseek":          "DEEPSEEK_API_KEY",
        "xai":               "XAI_API_KEY",
        "openrouter":        "OPENROUTER_API_KEY",
        "perplexity":        "PERPLEXITY_API_KEY",
        "google_vertex":     "GOOGLE_CLOUD_API_KEY",
        "amazon_bedrock":    "AWS_SECRET_ACCESS_KEY", # Simplified, usually uses IAM
        "mistral":           "MISTRAL_API_KEY",
        "cerebras":          "CEREBRAS_API_KEY",
        "vercel_ai_gateway": "AI_GATEWAY_API_KEY",
        "zai":               "ZAI_API_KEY",
        "minimax":           "MINIMAX_API_KEY",
        "minimax_cn":        "MINIMAX_CN_API_KEY",
        "huggingface":       "HF_TOKEN",
        "zhipuai":           "ZHIPUAI_API_KEY",
        "opencode":          "OPENCODE_API_KEY",
        "kimi_coding":       "KIMI_API_KEY",
    }

    # Special cases
    if prov == "anthropic":
        return os.getenv("ANTHROPIC_OAUTH_TOKEN") or os.getenv("ANTHROPIC_API_KEY")

    env_var = env_map.get(prov)
    return os.getenv(env_var) if env_var else None


# ---------------------------------------------------------------------------
# Provider Registry
# ---------------------------------------------------------------------------

_provider_registry: dict[str, type[BaseLLMProvider]] = {}


def register_provider(name: str | LLMProvider, cls: type[BaseLLMProvider]) -> None:
    """Register a provider class."""
    key = name.value if isinstance(name, LLMProvider) else str(name).lower()
    _provider_registry[key] = cls


def get_provider_class(name: str | LLMProvider) -> type[BaseLLMProvider] | None:
    """Get a registered provider class."""
    key = name.value if isinstance(name, LLMProvider) else str(name).lower()
    return _provider_registry.get(key)


# ---------------------------------------------------------------------------
# Log context (user_id, loop, chat_id) — set by agent before LLM calls
# ---------------------------------------------------------------------------

_log_context: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "llm_log_context", default=None
)


def set_llm_log_context(
    user_id: str | None = None,
    loop: str | None = None,
    chat_id: str | None = None,
    tool_results: list[dict[str, Any]] | None = None,
) -> None:
    """Set context for the next LLM log row. Call before llm.complete()."""
    ctx: dict[str, Any] = {
        "user_id": user_id or "",
        "loop": loop or "",
        "chat_id": chat_id or "",
    }
    if tool_results is not None:
        ctx["tool_results"] = tool_results
    _log_context.set(ctx)


# ---------------------------------------------------------------------------
# CSV — LLM API usage log (full content, no truncation)
# ---------------------------------------------------------------------------

_CSV_COLUMNS = [
    "timestamp", "user_id", "loop", "chat_id",
    "provider", "model",
    "prompt_tokens", "completion_tokens", "thoughts_tokens", "response_tokens",
    "cache_read_tokens", "cache_creation_tokens", "total_tokens",
    "duration_ms",
    "tool_definitions", "input", "tool_calls", "tool_results", "response",
]
_csv_lock = threading.Lock()


def _get_log_path() -> Path:
    from ...config import settings  # local import to avoid circular deps
    settings.AGENT_LOGS_PATH.mkdir(parents=True, exist_ok=True)
    return settings.AGENT_LOGS_PATH / "llm_api.csv"



def write_usage_log(
    *,
    provider: str,
    model: str,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    thoughts_tokens: int | None = None,
    response_tokens: int | None = None,
    cache_read_tokens: int | None = None,
    cache_creation_tokens: int | None = None,
    duration_ms: int | None = None,
    tool_definitions_str: str = "",
    input_summary: str,
    tool_calls_str: str,
    response_text: str = "",
) -> None:
    """Append one row to the LLM API CSV log (thread-safe). No truncation."""
    total = ""
    if prompt_tokens is not None or completion_tokens is not None:
        total = str(
            (prompt_tokens or 0) + (completion_tokens or 0) + (thoughts_tokens or 0)
        )
    ctx = _log_context.get() or {}
    tool_results = ctx.get("tool_results", [])
    tool_results_str = json.dumps(tool_results, ensure_ascii=False, indent=0) if tool_results else ""
    row = [
        datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S'),
        ctx.get("user_id", ""),
        ctx.get("loop", ""),
        ctx.get("chat_id", ""),
        provider,
        model,
        str(prompt_tokens) if prompt_tokens is not None else "",
        str(completion_tokens) if completion_tokens is not None else "",
        str(thoughts_tokens) if thoughts_tokens is not None else "",
        str(response_tokens) if response_tokens is not None else "",
        str(cache_read_tokens) if cache_read_tokens is not None else "",
        str(cache_creation_tokens) if cache_creation_tokens is not None else "",
        total,
        str(duration_ms) if duration_ms is not None else "",
        tool_definitions_str or "",
        input_summary or "",
        tool_calls_str or "",
        tool_results_str,
        response_text or "",
    ]
    path = _get_log_path()
    with _csv_lock:
        file_exists = path.exists() and path.stat().st_size > 0
        with open(path, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, quoting=csv.QUOTE_NONNUMERIC)
            if not file_exists:
                writer.writerow(_CSV_COLUMNS)
            writer.writerow(row)


# ---------------------------------------------------------------------------
# Message / tool-call helpers
# ---------------------------------------------------------------------------

def format_input_summary(messages: list[dict]) -> str:
    """Format messages for CSV log. No truncation."""
    parts = []
    for i, m in enumerate(messages):
        content = m.get("content") or ""
        parts.append(f"[{i}] {m.get('role', '')}:\n{content}")
    return "\n---\n".join(parts)


def format_tool_definitions(tools: list[dict] | None) -> str:
    """Format tool definitions (what we send to the model) as JSON. No truncation."""
    if not tools:
        return ""
    return json.dumps(tools, ensure_ascii=False, indent=0)


def format_tool_calls(tool_calls: list[dict]) -> str:
    """Render a list of tool calls as JSON. No truncation."""
    if not tool_calls:
        return ""
    normalized = []
    for tc in tool_calls:
        args = tc.get("arguments", {})
        if isinstance(args, str):
            try:
                args = json.loads(args) if args else {}
            except json.JSONDecodeError:
                args = {"_raw": args}
        normalized.append({"name": tc.get("name", ""), "arguments": args})
    return json.dumps(normalized, ensure_ascii=False, indent=0)


# ---------------------------------------------------------------------------
# Retry with smart backoff
# ---------------------------------------------------------------------------

_RETRY_MAX_ATTEMPTS = 5
_RETRY_BASE_DELAY   = 1.0    # seconds
_RETRY_JITTER       = 0.25   # 25% jitter
_MAX_CAPACITY_RETRIES = 3    # 529/overloaded before triggering fallback


def _is_retryable(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(
        kw in msg
        for kw in (
            "503", "unavailable", "429", "rate", "quota", "overloaded",
            "500", "502", "504", "timeout", "connection", "temporarily",
            "529",
        )
    )


def _is_capacity_error(exc: Exception) -> bool:
    """Check if the error is a capacity/overload issue (529, 503).

    Detection has to handle several provider conventions:
      - SDK exceptions that expose ``status_code`` / ``status`` (OpenAI, Anthropic)
      - Gemini's google.api_core errors whose ``str(exc)`` looks like
        ``503 UNAVAILABLE. {... "high demand" ...}`` with no status attr
      - Anthropic 529 ("Overloaded")
    """
    msg = str(exc).lower()
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if status in (529, 503):
        return True
    return any(
        kw in msg
        for kw in (
            "529",
            "503",
            "overloaded",
            "capacity",
            "unavailable",
            "high demand",
            "currently experiencing",
            "service_unavailable",
        )
    )


def _is_rate_limit_error(exc: Exception) -> bool:
    """Check if the error is a rate limit (429)."""
    msg = str(exc).lower()
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if status == 429:
        return True
    return any(kw in msg for kw in ("429", "rate limit", "rate_limit"))


async def retry_async(
    coro: Callable,
    max_retries: int = _RETRY_MAX_ATTEMPTS,
    on_retry: Callable | None = None,
) -> Any:
    """Execute *coro()* with exponential back-off, jitter, and error-aware retry.

    Features over the previous version:
      - Exponential backoff with 25% random jitter (prevents thundering herd)
      - Separate counter for capacity errors (529) — triggers FallbackTriggered
        after 3 consecutive capacity failures
      - Respects Retry-After headers when available
      - Rate-limit (429) vs capacity (529) distinction
      - Optional on_retry callback for logging/notification
    """
    from ..errors import FallbackTriggered, _extract_retry_after

    last_exc: Exception | None = None
    capacity_retries = 0

    for attempt in range(max_retries):
        try:
            return await coro()
        except Exception as exc:
            last_exc = exc
            if not _is_retryable(exc) or attempt == max_retries - 1:
                raise

            # Track capacity errors separately
            if _is_capacity_error(exc):
                capacity_retries += 1
                if capacity_retries >= _MAX_CAPACITY_RETRIES:
                    raise FallbackTriggered("", "")

            # Exponential backoff with jitter
            base = _RETRY_BASE_DELAY * (2 ** attempt)
            jitter = random.uniform(0, base * _RETRY_JITTER)
            delay = base + jitter

            # Respect Retry-After header if available
            retry_after = _extract_retry_after(exc)
            if retry_after and retry_after < 60:
                delay = max(delay, retry_after)

            error_type = "capacity" if _is_capacity_error(exc) else (
                "rate_limit" if _is_rate_limit_error(exc) else "transient"
            )

            logger.warning(
                "LLM %s error (attempt %d/%d): %s — retrying in %.1fs",
                error_type, attempt + 1, max_retries, exc, delay,
            )

            if on_retry:
                try:
                    await on_retry(attempt, delay, str(exc))
                except Exception:
                    pass

            await asyncio.sleep(delay)
    raise last_exc  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Base provider
# ---------------------------------------------------------------------------

class BaseLLMProvider(ABC):
    """
    Unified async interface for all LLM backends.

    Every ``complete()`` implementation must yield dicts with a ``type`` key:
      - ``{"type": "content",        "content": str}``
      - ``{"type": "tool_call",      "name": str, "arguments": dict}``
      - ``{"type": "agent_thinking", "content": str}``   (optional, Gemini/Claude extended thinking)
      - ``{"type": "thought_signature", "signature": bytes}``  (Gemini only)
      - ``{"type": "error",          "error": str}``
    """

    def __init__(self, model: str, api_key: str | None = None) -> None:
        self.model   = model
        self.api_key = api_key

    @abstractmethod
    async def complete(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        stream: bool = True,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield response chunks. max_tokens=None lets the backend decide."""
        ...  # pragma: no cover

    @abstractmethod
    def supports_tools(self) -> bool:
        """Return whether this provider supports function/tool calling."""
        ...  # pragma: no cover


# ---------------------------------------------------------------------------
# Shared _write_log helper — used by all providers
# ---------------------------------------------------------------------------

def provider_write_log(
    *,
    provider: str,
    model: str,
    prompt_tokens,
    completion_tokens,
    thoughts_tokens=None,
    response_tokens=None,
    cache_read_tokens=None,
    cache_creation_tokens=None,
    duration_ms=None,
    tools=None,
    messages: list[dict],
    tool_calls_list: list[dict],
    response_texts: list[str],
) -> None:
    """Shared log writer for all LLM providers. Wraps write_usage_log with error handling."""
    resp_text = "\n".join(response_texts)
    if not resp_text and tool_calls_list:
        resp_text = "[Tool calls] " + ", ".join(tc.get("name", "?") for tc in tool_calls_list)
    try:
        write_usage_log(
            provider=provider,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            thoughts_tokens=thoughts_tokens,
            response_tokens=response_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_creation_tokens=cache_creation_tokens,
            duration_ms=duration_ms,
            tool_definitions_str=format_tool_definitions(tools) if tools else "",
            input_summary=format_input_summary(messages),
            tool_calls_str=format_tool_calls(tool_calls_list),
            response_text=resp_text,
        )
    except Exception as exc:
        logger.warning("Failed to write LLM usage log: %s", exc)
