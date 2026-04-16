"""
LLM provider fallback chain — automatic model degradation on failure.

When the primary provider is overloaded or unavailable, automatically
switches to the next provider in the chain. Configured via environment
variable FALLBACK_LLM_PROVIDERS.

Usage:
    config = FallbackConfig.from_env()
    provider = FallbackLLMProvider(config)
    async for chunk in provider.complete(messages, tools):
        ...
"""
from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from . import BaseLLMProvider

logger = logging.getLogger(__name__)


@dataclass
class FallbackConfig:
    """Provider fallback chain configuration.

    primary: (provider_name, model_name) — main provider
    fallbacks: list of (provider_name, model_name) in priority order
    """
    primary: tuple[str, str] = ("gemini", "")
    fallbacks: list[tuple[str, str]] = field(default_factory=list)

    @classmethod
    def from_env(cls) -> FallbackConfig:
        """Build fallback chain from environment variables.

        Reads:
          DEFAULT_LLM_PROVIDER / DEFAULT_MODEL — primary provider
          FALLBACK_LLM_PROVIDERS — comma-separated "provider:model" pairs
        """
        primary_provider = os.getenv("DEFAULT_LLM_PROVIDER", "gemini")
        primary_model = os.getenv("DEFAULT_MODEL", "")

        fallback_str = os.getenv("FALLBACK_LLM_PROVIDERS", "")
        fallbacks: list[tuple[str, str]] = []
        if fallback_str:
            for item in fallback_str.split(","):
                item = item.strip()
                if not item:
                    continue
                parts = item.split(":", 1)
                provider = parts[0].strip()
                model = parts[1].strip() if len(parts) > 1 else ""
                if provider:
                    fallbacks.append((provider, model))

        return cls(primary=(primary_provider, primary_model), fallbacks=fallbacks)

    @property
    def has_fallbacks(self) -> bool:
        return len(self.fallbacks) > 0


class FallbackLLMProvider(BaseLLMProvider):
    """LLM provider wrapper with automatic fallback on failure.

    Wraps an existing provider and falls back to alternatives when
    the primary is overloaded (529) or rate-limited (429).
    """

    def __init__(
        self,
        primary: BaseLLMProvider,
        fallback_configs: list[tuple[str, str]],
    ) -> None:
        super().__init__(model=primary.model, api_key=primary.api_key)
        self._primary = primary
        self._fallback_configs = fallback_configs
        self._active_provider = primary
        self._fallback_index = -1  # -1 = using primary
        self._consecutive_failures = 0
        self._max_failures_before_fallback = 3

    def _create_fallback_provider(self, provider_name: str, model: str) -> BaseLLMProvider | None:
        """Try to create a fallback provider. Returns None on failure."""
        try:
            from ..llm_providers import create_provider
            return create_provider(provider_name, model)
        except Exception as e:
            logger.warning("Failed to create fallback provider %s/%s: %s", provider_name, model, e)
            return None

    def activate_next_fallback(self) -> bool:
        """Switch to the next fallback provider.

        Returns True if a fallback was activated, False if exhausted.
        """
        while True:
            self._fallback_index += 1
            if self._fallback_index >= len(self._fallback_configs):
                return False

            provider_name, model = self._fallback_configs[self._fallback_index]
            provider = self._create_fallback_provider(provider_name, model)
            if provider:
                logger.warning(
                    "Switching to fallback provider: %s/%s",
                    provider_name, model or "(default)",
                )
                self._active_provider = provider
                self._consecutive_failures = 0
                return True

    def reset_to_primary(self) -> None:
        """Reset back to the primary provider."""
        self._active_provider = self._primary
        self._fallback_index = -1
        self._consecutive_failures = 0

    async def complete(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        stream: bool = True,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Complete with automatic fallback on provider failure."""
        try:
            async for chunk in self._active_provider.complete(
                messages, tools, stream, temperature, max_tokens,
            ):
                yield chunk
            # Success — reset failure counter
            self._consecutive_failures = 0
        except Exception as e:
            from ..errors import ErrorCategory, classify_error
            agent_error = classify_error(e, "llm_complete")

            # Only trigger fallback on capacity/rate limit errors
            if agent_error.category in (ErrorCategory.CAPACITY, ErrorCategory.RATE_LIMITED):
                self._consecutive_failures += 1
                if (
                    self._consecutive_failures >= self._max_failures_before_fallback
                    and self._fallback_configs
                ):
                    if self.activate_next_fallback():
                        logger.warning("Retrying with fallback provider after %d failures", self._consecutive_failures)
                        async for chunk in self._active_provider.complete(
                            messages, tools, stream, temperature, max_tokens,
                        ):
                            yield chunk
                        return
            raise

    def supports_tools(self) -> bool:
        return self._active_provider.supports_tools()

    @property
    def active_provider_name(self) -> str:
        """Name of the currently active provider (for logging)."""
        return type(self._active_provider).__name__
