"""
LLM providers — public API.

This module re-exports everything from the ``agent.llm`` sub-package and
provides the ``create_provider()`` factory function.

Usage::

    from backend.agent.llm_providers import create_provider, LLMProvider
    llm = create_provider("gemini", api_key=...)

Supported providers and recommended models
------------------------------------------
- ``gemini``         → gemini-3.1-flash-lite-preview (default)
- ``google_vertex``  → same as gemini, via Vertex AI
- ``openai``         → gpt-4.5-preview (default)
- ``azure_openai``   → same as openai, on Azure
- ``anthropic``      → claude-opus-4-5 (default)
- ``mistral``        → mistral-large-latest
- ``groq``           → llama-3.3-70b-versatile
- ``deepseek``       → deepseek-chat (V3)
- ``xai``            → grok-beta
- ``openrouter``     → anthropic/claude-3.5-sonnet
- ``perplexity``     → llama-3.1-sonar-huge-128k-pro
- ``amazon_bedrock`  → amazon.nova-pro-v1:0
- ``vllm``           → gemma-4-26b-a4b (default; any model served by a local vLLM server)
"""
from __future__ import annotations

import backend.agent.llm.anthropic_provider as _anthropic  # noqa: F401
import backend.agent.llm.bedrock as _bedrock  # noqa: F401
import backend.agent.llm.gemini as _gemini  # noqa: F401
import backend.agent.llm.openai_provider as _openai  # noqa: F401
import backend.agent.llm.zhipuai_provider as _zhipuai  # noqa: F401
from backend.config import settings

from .llm import (
    BaseLLMProvider,
    LLMProvider,
    get_env_api_key,
    get_provider_class,
)

# ---------------------------------------------------------------------------
# Default model per provider
# ---------------------------------------------------------------------------
# Sourced from settings (backend/config.py), which loads from .env. Override
# any provider's default by setting DEFAULT_MODEL_<PROVIDER> in .env. The vLLM
# entry is pulled from VLLM_MODEL because it depends on whatever model the
# user happens to be serving locally.
_DEFAULT_MODELS: dict[LLMProvider, str] = {
    LLMProvider.GEMINI:         settings.DEFAULT_MODEL_GEMINI,
    LLMProvider.OPENAI:         settings.DEFAULT_MODEL_OPENAI,
    LLMProvider.AZURE_OPENAI:   settings.DEFAULT_MODEL_AZURE_OPENAI,
    LLMProvider.ANTHROPIC:      settings.DEFAULT_MODEL_ANTHROPIC,
    LLMProvider.MISTRAL:        settings.DEFAULT_MODEL_MISTRAL,
    LLMProvider.GROQ:           settings.DEFAULT_MODEL_GROQ,
    LLMProvider.DEEPSEEK:       settings.DEFAULT_MODEL_DEEPSEEK,
    LLMProvider.XAI:            settings.DEFAULT_MODEL_XAI,
    LLMProvider.OPENROUTER:     settings.DEFAULT_MODEL_OPENROUTER,
    LLMProvider.PERPLEXITY:     settings.DEFAULT_MODEL_PERPLEXITY,
    LLMProvider.GOOGLE_VERTEX:  settings.DEFAULT_MODEL_GOOGLE_VERTEX,
    LLMProvider.AMAZON_BEDROCK: settings.DEFAULT_MODEL_AMAZON_BEDROCK,
    LLMProvider.MINIMAX:        settings.DEFAULT_MODEL_MINIMAX,
    LLMProvider.ZHIPUAI:        settings.DEFAULT_MODEL_ZHIPUAI,
    LLMProvider.VLLM:           settings.VLLM_MODEL,
}


def create_provider(
    provider_name: str,
    model: str | None = None,
    api_key: str | None = None,
    **kwargs,
) -> BaseLLMProvider:
    """
    Instantiate and return the requested LLM provider.
    (Registry-based implementation inspired by pi-mono/packages/ai)

    Parameters
    ----------
    provider_name:
        One of ``"gemini"``, ``"openai"``, ``"azure_openai"``, ``"anthropic"``,
        ``"mistral"``, ``"groq"``, ``"deepseek"``, ``"xai"``, ``"openrouter"``,
        ``"perplexity"``, ``"google_vertex"``, ``"amazon_bedrock"``, ``"vllm"``.
    model:
        Override the default model for this provider.
    api_key:
        Provider API key (resolved from ENV if not passed).
    **kwargs:
        Provider-specific extras, e.g. ``azure_endpoint``, ``base_url``,
        ``api_version``, ``thinking_budget``.
    """
    try:
        prov = LLMProvider(provider_name.lower())
    except ValueError:
        valid = [p.value for p in LLMProvider]
        raise ValueError(
            f"Unknown LLM provider: '{provider_name}'. Supported: {valid}"
        ) from None

    # Resolve model
    resolved_model = model or _DEFAULT_MODELS[prov]

    # Resolve API key from environment if not provided (except for vLLM which has a default)
    if api_key is None and prov != LLMProvider.VLLM:
        api_key = get_env_api_key(prov)

    # Resolve provider class from registry
    provider_cls = get_provider_class(prov)
    if not provider_cls:
        raise RuntimeError(f"Provider class for '{prov}' not registered. Check imports.")

    # Special handling for thinking_budget (Gemini/Anthropic)
    if prov in (LLMProvider.GEMINI, LLMProvider.ANTHROPIC):
        thinking_budget = kwargs.pop("thinking_budget", 1024)
        return provider_cls(model=resolved_model, api_key=api_key, thinking_budget=thinking_budget, **kwargs)

    # Inject defaults from settings where specific environment configurations apply
    if prov == LLMProvider.VLLM:
        if api_key is None:
            api_key = "token-abc"
        kwargs.setdefault("base_url", settings.VLLM_BASE_URL)
    elif prov == LLMProvider.AZURE_OPENAI:
        kwargs.setdefault("azure_endpoint", settings.AZURE_OPENAI_ENDPOINT)
        kwargs.setdefault("api_version", settings.AZURE_OPENAI_API_VERSION)
    elif prov == LLMProvider.AMAZON_BEDROCK:
        kwargs.setdefault("region_name", settings.AWS_REGION)
    elif prov == LLMProvider.GOOGLE_VERTEX:
        kwargs.setdefault("project", settings.GOOGLE_CLOUD_PROJECT)
        kwargs.setdefault("location", settings.GOOGLE_CLOUD_LOCATION)

    return provider_cls(model=resolved_model, api_key=api_key, **kwargs)


__all__ = [
    "create_provider",
    "LLMProvider",
    "BaseLLMProvider",
    "get_env_api_key",
    "get_provider_class",
]
