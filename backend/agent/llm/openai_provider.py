"""
OpenAI and vLLM providers.

Supported models: gpt-4.5-preview and later (full GPT-5 family — gpt-5,
gpt-5-mini, gpt-5-nano, gpt-5.2, gpt-5.4, gpt-5.4-mini, gpt-5.4-nano,
gpt-5.3-codex, etc.). Older models (gpt-3.5, gpt-4-turbo) are not maintained.

Streaming tool-call handling:
  OpenAI sends tool arguments across multiple *delta* chunks, each identified by
  an ``index`` field.  We accumulate partial argument strings per-index and only
  emit the complete tool call once the stream finishes.
  (Reference: https://platform.openai.com/docs/guides/function-calling)

Reasoning policy:
  GPT-5 family and o-series reasoning models accept a ``reasoning_effort``
  parameter that controls how many hidden reasoning tokens the model burns
  before answering. The API server-side default for these models is
  ``medium``, which is usually overkill (slow + expensive) for agentic
  tool-calling loops. Set ``OPENAI_REASONING_EFFORT`` in .env to override
  globally — recommended value for tool-calling agents is ``minimal``
  (GPT-5 originals only) or ``low``.

  Caveat: when ``reasoning_effort=minimal`` is in effect, the OpenAI API
  rejects parallel tool calls. We auto-disable them in that case.
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import AsyncIterator
from typing import Any

from . import (
    BaseLLMProvider,
    provider_write_log,
    retry_async,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# OpenAI provider
# ---------------------------------------------------------------------------

class OpenAIProvider(BaseLLMProvider):
    """
    OpenAI GPT-4.5+ and GPT-5 with streaming tool calls.

    Non-streaming mode is used internally; we consume the entire response and
    yield chunks to stay compatible with the agent loop interface.  This avoids
    the complexity of partial-streaming while keeping the generator interface intact.
    """

    # Models known to support tool calling (open-ended prefix matching)
    _SUPPORTED_PREFIXES = ("gpt-4.5", "gpt-5", "o3", "o4", "chatgpt-4o")

    # Flag for subclasses to indicate they are running against a vLLM server.
    # Used to apply vLLM-specific workarounds (e.g. GLM thinking disable).
    _is_vllm: bool = False

    # Flag for the DeepSeek V4 backend. DeepSeek shares the OpenAI
    # ChatCompletions surface but takes a distinct thinking-mode toggle
    # (``extra_body.thinking``) and a different ``reasoning_effort`` value
    # set (high/max only). Set to True in ``DeepSeekProvider``.
    _is_deepseek: bool = False

    def __init__(
        self,
        model: str = "gpt-5.4-mini",
        api_key: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(model, api_key)
        self._extra = kwargs  # forwarded to AsyncOpenAI constructor
        try:
            from openai import AsyncOpenAI  # type: ignore
            self._client = AsyncOpenAI(api_key=api_key, **kwargs)
        except ImportError as exc:
            raise ImportError(
                "openai package required. Install: pip install openai>=1.50"
            ) from exc

    def supports_tools(self) -> bool:
        return True

    async def complete(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        stream: bool = True,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        _t0 = time.perf_counter()
        try:
            kwargs: dict[str, Any] = {
                "model": self.model,
                "messages": messages,
                "temperature": temperature,
                "stream": True,  # always stream at API level for consistent handling
                "stream_options": {"include_usage": True},
            }
            # Only set max_completion_tokens if explicitly specified.
            # When omitted, vLLM uses (max_model_len - input_tokens) which is
            # optimal — no artificial output cap that truncates tool arguments.
            if max_tokens is not None:
                kwargs["max_completion_tokens"] = max_tokens
            if tools:
                kwargs["tools"] = tools
                # "auto" lets the model naturally stop by not calling tools
                # (Claude Code pattern). The framework handles text-only
                # responses via auto-reply. "required" caused infinite loops
                # because models couldn't stop without calling finish_chat.
                kwargs["tool_choice"] = "auto"

            # Disable thinking/reasoning for vLLM-served models to reduce latency
            # in agentic tool-calling loops.  The vLLM server-side reasoning parser
            # (glm45 / qwen3) still extracts <think> blocks if thinking is on.
            if self._is_vllm:
                model_lower = self.model.lower()
                if "glm" in model_lower or "qwen" in model_lower or "gemma" in model_lower:
                    kwargs.setdefault("extra_body", {})
                    kwargs["extra_body"]["chat_template_kwargs"] = {
                        "enable_thinking": False,
                    }
            elif self._is_deepseek:
                # DeepSeek V4 (flash/pro): dual-mode reasoning is toggled via
                # ``extra_body.thinking`` and ``reasoning_effort`` accepts only
                # high/max (low/medium silently map to "high" server-side).
                # Do NOT forward OPENAI_REASONING_EFFORT here — the OpenAI-only
                # value "minimal" is rejected by DeepSeek with HTTP 400.
                from backend.config import settings
                thinking = (settings.DEEPSEEK_THINKING or "").strip().lower()
                if thinking in ("enabled", "disabled"):
                    kwargs.setdefault("extra_body", {})
                    kwargs["extra_body"]["thinking"] = {"type": thinking}
                ds_effort = (settings.DEEPSEEK_REASONING_EFFORT or "").strip()
                if ds_effort:
                    kwargs["reasoning_effort"] = ds_effort
            else:
                # Cloud OpenAI / Azure OpenAI: forward the configured reasoning
                # effort to GPT-5 / o-series reasoning models. Skipped for vLLM
                # because the local server has its own reasoning controls above.
                from backend.config import settings
                effort = (settings.OPENAI_REASONING_EFFORT or "").strip()
                if effort:
                    kwargs["reasoning_effort"] = effort
                    # GPT-5 / o-series reasoning models only accept the default
                    # temperature (1) — passing any other value yields HTTP 400.
                    # Drop the caller's temperature so the API uses its default.
                    kwargs.pop("temperature", None)
                    # The OpenAI API rejects parallel tool calls when reasoning
                    # effort is "minimal". Disable them transparently so users
                    # don't have to remember this constraint.
                    if effort.lower() == "minimal":
                        kwargs["parallel_tool_calls"] = False

            async def _call():
                return self._client.chat.completions.create(**kwargs)

            try:
                stream_ctx = await retry_async(_call)
            except Exception as ctx_err:
                # Auto-reduce max_tokens when context window is exceeded
                err_str = str(ctx_err)
                if "too large" in err_str and "input tokens" in err_str:
                    import re
                    m = re.search(r"context length is (\d+).*?(\d+) input tokens", err_str)
                    if m:
                        ctx_limit = int(m.group(1))
                        input_tokens = int(m.group(2))
                        safe_max = max(ctx_limit - input_tokens - 64, 256)
                        logger.warning(
                            "Context window exceeded: %d input + %d max_tokens > %d limit. "
                            "Retrying with max_tokens=%d",
                            input_tokens, max_tokens, ctx_limit, safe_max,
                        )
                        kwargs["max_completion_tokens"] = safe_max
                        max_tokens = safe_max
                        stream_ctx = await retry_async(_call)
                    else:
                        raise
                else:
                    raise

            # --- Accumulation buffers ---
            # {index: {"id": str, "name": str, "arguments": str}}
            tool_accum: dict[int, dict[str, str]] = {}
            content_parts: list[str] = []
            thought_parts: list[str] = []
            prompt_tokens = completion_tokens = thoughts_tokens = None
            finish_reason: str | None = None
            cache_read_tokens: int | None = None

            async for chunk in await stream_ctx:
                # Usage information (only present in the last chunk when stream_options requested)
                if chunk.usage:
                    prompt_tokens     = chunk.usage.prompt_tokens
                    completion_tokens = chunk.usage.completion_tokens
                    # OpenAI/vLLM reasoning models: completion_tokens_details.reasoning_tokens
                    details = getattr(chunk.usage, "completion_tokens_details", None)
                    if details:
                        thoughts_tokens = getattr(details, "reasoning_tokens", None)
                        if thoughts_tokens is None and isinstance(details, dict):
                            thoughts_tokens = details.get("reasoning_tokens")
                    # Prompt cache hits — OpenAI / vLLM expose this as
                    # ``prompt_tokens_details.cached_tokens`` on the usage block.
                    prompt_details = getattr(chunk.usage, "prompt_tokens_details", None)
                    if prompt_details is not None:
                        cached = getattr(prompt_details, "cached_tokens", None)
                        if cached is None and isinstance(prompt_details, dict):
                            cached = prompt_details.get("cached_tokens")
                        if cached is not None:
                            cache_read_tokens = cached
                    # DeepSeek surfaces cache stats directly on usage as
                    # ``prompt_cache_hit_tokens`` / ``prompt_cache_miss_tokens``.
                    # Prefer this when present — it is the authoritative count
                    # for the DeepSeek backend.
                    if cache_read_tokens is None:
                        ds_hit = getattr(chunk.usage, "prompt_cache_hit_tokens", None)
                        if ds_hit is None:
                            extra_usage = getattr(chunk.usage, "model_extra", None) or {}
                            ds_hit = extra_usage.get("prompt_cache_hit_tokens")
                        if ds_hit is not None:
                            cache_read_tokens = ds_hit

                choice = chunk.choices[0] if chunk.choices else None
                if choice is None:
                    continue

                delta = choice.delta

                # Text content
                if delta.content:
                    content_parts.append(delta.content)
                    yield {"type": "content", "content": delta.content}

                # Reasoning / Thinking content (e.g. DeepSeek R1, Qwen3, GLM-4.7, OpenAI o1/o3)
                # vLLM uses reasoning_content (DeepSeek/Qwen) or reasoning (GLM-4.7) in delta
                reasoning = (
                    getattr(delta, "reasoning_content", None)
                    or getattr(delta, "reasoning", None)
                )
                if not reasoning and getattr(delta, "model_extra", None):
                    reasoning = delta.model_extra.get("reasoning_content") or delta.model_extra.get("reasoning")
                if reasoning:
                    thought_parts.append(reasoning)
                    yield {"type": "agent_thinking", "content": reasoning}

                # Tool call deltas — accumulate by index
                if delta.tool_calls:
                    for tc_delta in delta.tool_calls:
                        idx = tc_delta.index
                        if idx not in tool_accum:
                            tool_accum[idx] = {
                                "id":        tc_delta.id or "",
                                "name":      "",
                                "arguments": "",
                            }
                        if tc_delta.function:
                            if tc_delta.function.name:
                                tool_accum[idx]["name"] += tc_delta.function.name
                            if tc_delta.function.arguments:
                                tool_accum[idx]["arguments"] += tc_delta.function.arguments

                # Track finish reason for truncation detection
                if choice.finish_reason:
                    finish_reason = choice.finish_reason

                # Emit accumulated tool calls when the stream is done
                if choice.finish_reason in ("tool_calls", "stop"):
                    for tc_info in tool_accum.values():
                        try:
                            args = json.loads(tc_info["arguments"]) if tc_info["arguments"] else {}
                        except json.JSONDecodeError:
                            logger.warning(
                                "Could not parse tool arguments for %s: %s",
                                tc_info["name"], tc_info["arguments"][:200],
                            )
                            args = {"_raw": tc_info["arguments"]}
                        tc = {"id": tc_info["id"], "name": tc_info["name"], "arguments": args}
                        yield {"type": "tool_call", **tc}

                # Usage information (vLLM version) or thoughts tokens if available
                if hasattr(chunk, 'choices') and choice and choice.delta:
                    # Some models report thoughts separately
                    extra = getattr(choice.delta, "model_extra", {}) or {}
                    thoughts_tokens = extra.get("thoughts_tokens") or extra.get("reasoning_tokens")
                    if thoughts_tokens:
                        yield {"type": "token_usage", "thoughts_tokens": thoughts_tokens}

            # Fallback: estimate thoughts_tokens from reasoning text when API doesn't provide it
            # (vLLM/GLM may not include completion_tokens_details in usage)
            if thoughts_tokens is None and thought_parts:
                reasoning_text = "".join(thought_parts)
                # Rough estimate: ~4 chars/token for English, ~1.5 for CJK; use 3 as middle ground
                thoughts_tokens = max(1, len(reasoning_text) // 3)

            # Final Token Usage Yield
            if prompt_tokens is not None or completion_tokens is not None:
                truncated = (
                    finish_reason == "length"
                    or (completion_tokens is not None and max_tokens is not None and completion_tokens >= max_tokens)
                )
                yield {
                    "type": "token_usage",
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "thoughts_tokens": thoughts_tokens,
                    "cache_read_tokens": cache_read_tokens,
                    "truncated": truncated,
                    "max_tokens": max_tokens,
                }

            # Collect final response for logging
            full_response_parts = []
            if thought_parts:
                full_response_parts.append("<thought>\n" + "".join(thought_parts) + "\n</thought>")
            if content_parts:
                full_response_parts.append("".join(content_parts))

            completed_tools = []
            for tc_info in tool_accum.values():
                try:
                    args = json.loads(tc_info["arguments"]) if tc_info["arguments"] else {}
                except json.JSONDecodeError:
                    args = {}
                completed_tools.append({"name": tc_info["name"], "arguments": args})
            if completed_tools:
                full_response_parts.append("[Tool calls] " + ", ".join(t["name"] for t in completed_tools))

            provider_write_log(
                provider="openai",
                model=self.model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                thoughts_tokens=thoughts_tokens,
                cache_read_tokens=cache_read_tokens,
                duration_ms=int((time.perf_counter() - _t0) * 1000),
                tools=tools,
                messages=messages,
                tool_calls_list=completed_tools,
                response_texts=full_response_parts,
            )

        except Exception as exc:
            logger.error("OpenAI API error: %s", exc, exc_info=True)
            provider_write_log(
                provider="openai",
                model=self.model,
                prompt_tokens=None,
                completion_tokens=None,
                duration_ms=int((time.perf_counter() - _t0) * 1000),
                tools=tools,
                messages=messages,
                tool_calls_list=[],
                response_texts=[f"ERROR: {exc}"],
            )
            yield {"type": "error", "error": str(exc)}


# ---------------------------------------------------------------------------
# vLLM provider  (OpenAI-compatible local server)
# ---------------------------------------------------------------------------

class VLLMProvider(OpenAIProvider):
    """
    vLLM local inference server via the OpenAI-compatible API.
    Inherits all streaming logic from OpenAIProvider.

    vLLM server launch examples (must match the served model):

      Gemma 4 26B-A4B (25.2B MoE / 3.8B active, ~52GB VRAM)::

        CUDA_VISIBLE_DEVICES=0 vllm serve google/gemma-4-26B-A4B-it \\
          --enable-auto-tool-choice --tool-call-parser hermes \\
          --served-model-name gemma-4-26b-a4b \\
          --max-model-len 65536 --max-num-seqs 2 \\
          --gpu-memory-utilization 0.92 --trust-remote-code --port 8421

      GLM-4.7-Flash (30B MoE, ~56GB VRAM)::

        CUDA_VISIBLE_DEVICES=0 vllm serve zai-org/GLM-4.7-Flash \\
          --enable-auto-tool-choice --tool-call-parser glm47 \\
          --reasoning-parser glm45 --served-model-name glm-4.7-flash \\
          --max-model-len 16384 --max-num-seqs 64 \\
          --gpu-memory-utilization 0.92 --trust-remote-code --port 8421

      Qwen3.5-27B (27B dense, ~54GB VRAM)::

        CUDA_VISIBLE_DEVICES=0 vllm serve Qwen/Qwen3.5-27B \\
          --enable-auto-tool-choice --tool-call-parser qwen3_coder \\
          --reasoning-parser qwen3 --served-model-name qwen3.5-27b \\
          --max-model-len 16384 --max-num-seqs 32 \\
          --gpu-memory-utilization 0.92 --trust-remote-code \\
          --language-model-only --enforce-eager \\
          --gdn-prefill-backend triton --port 8421

      Qwen3.5-35B-A3B (35B MoE / 3B active, ~70GB VRAM)::

        CUDA_VISIBLE_DEVICES=0 vllm serve Qwen/Qwen3.5-35B-A3B \\
          --enable-auto-tool-choice --tool-call-parser qwen3_coder \\
          --reasoning-parser qwen3 --served-model-name qwen3.5-35b-a3b \\
          --max-model-len 32768 --max-num-seqs 2 \\
          --gpu-memory-utilization 0.92 --trust-remote-code \\
          --language-model-only --enforce-eager \\
          --gdn-prefill-backend triton --port 8421
    """

    _is_vllm: bool = True

    def __init__(
        self,
        model: str = "gemma-4-26b-a4b",
        api_key: str | None = None,
        base_url: str = "http://localhost:8421/v1",
        **kwargs,
    ) -> None:
        # Pass base_url directly to OpenAIProvider so only one client is created
        resolved_key = api_key or os.getenv("VLLM_API_KEY", "token-abc")
        super().__init__(model=model, api_key=resolved_key, base_url=base_url, **kwargs)


class GroqProvider(OpenAIProvider):
    """Groq Cloud provider."""
    def __init__(self, model: str = "llama-3.3-70b-versatile", api_key: str | None = None, **kwargs) -> None:
        super().__init__(model=model, api_key=api_key, base_url="https://api.groq.com/openai/v1", **kwargs)


class DeepSeekProvider(OpenAIProvider):
    """DeepSeek API provider — V4 lineup (April 2026).

    Supported models:
      - ``deepseek-v4-flash`` (default; 284B total / 13B active, 1M ctx)
      - ``deepseek-v4-pro``   (1.6T total / 49B active, 1M ctx, 384K max output)

    Both models support dual modes (thinking / non-thinking), tool calling,
    JSON output and prompt caching. Mode is toggled with ``DEEPSEEK_THINKING``
    in ``.env`` (``enabled`` / ``disabled``), reasoning depth with
    ``DEEPSEEK_REASONING_EFFORT`` (``high`` / ``max``).

    ``deepseek-chat`` and ``deepseek-reasoner`` route to V4-Flash and will be
    fully retired after 2026-07-24 15:59 UTC. New deployments should use the
    ``deepseek-v4-*`` model IDs directly.
    """

    _is_deepseek: bool = True

    def __init__(self, model: str = "deepseek-v4-flash", api_key: str | None = None, **kwargs) -> None:
        super().__init__(model=model, api_key=api_key, base_url="https://api.deepseek.com", **kwargs)


class XAIProvider(OpenAIProvider):
    """x.AI (Grok 4 family) provider."""
    def __init__(self, model: str = "grok-4-1-fast-reasoning", api_key: str | None = None, **kwargs) -> None:
        super().__init__(model=model, api_key=api_key, base_url="https://api.x.ai/v1", **kwargs)


class OpenRouterProvider(OpenAIProvider):
    """OpenRouter provider — model format is <vendor>/<model>."""
    def __init__(self, model: str = "anthropic/claude-sonnet-4-6", api_key: str | None = None, **kwargs) -> None:
        super().__init__(model=model, api_key=api_key, base_url="https://openrouter.ai/api/v1", **kwargs)


class PerplexityProvider(OpenAIProvider):
    """Perplexity Sonar provider (search-grounded LLM)."""
    def __init__(self, model: str = "sonar-pro", api_key: str | None = None, **kwargs) -> None:
        super().__init__(model=model, api_key=api_key, base_url="https://api.perplexity.ai", **kwargs)


class MistralProvider(OpenAIProvider):
    """Mistral AI provider (OpenAI-compatible)."""
    def __init__(self, model: str = "mistral-medium-latest", api_key: str | None = None, **kwargs) -> None:
        super().__init__(model=model, api_key=api_key, base_url="https://api.mistral.ai/v1", **kwargs)


class MinimaxProvider(OpenAIProvider):
    """MiniMax provider (OpenAI-compatible)."""
    def __init__(self, model: str = "MiniMax-M2", api_key: str | None = None, **kwargs) -> None:
        super().__init__(model=model, api_key=api_key, base_url="https://api.minimax.chat/v1", **kwargs)


# ---------------------------------------------------------------------------
# Azure OpenAI
# ---------------------------------------------------------------------------

class AzureOpenAIProvider(BaseLLMProvider):
    """Azure-hosted OpenAI — same streaming logic as OpenAIProvider."""

    def __init__(
        self,
        model: str = "gpt-5-mini",
        api_key: str | None = None,
        azure_endpoint: str | None = None,
        api_version: str = "2024-12-01-preview",
        **kwargs,
    ) -> None:
        super().__init__(model, api_key)
        self.azure_endpoint = azure_endpoint
        self.api_version    = api_version
        try:
            from openai import AsyncAzureOpenAI  # type: ignore
            self._client = AsyncAzureOpenAI(
                api_key=api_key,
                azure_endpoint=azure_endpoint or "",
                api_version=api_version,
                **kwargs,
            )
        except ImportError as exc:
            raise ImportError(
                "openai package required. Install: pip install openai>=1.50"
            ) from exc

    def supports_tools(self) -> bool:
        return True

    async def complete(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        stream: bool = True,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        # Reuse OpenAI streaming logic by delegating through a temporary wrapper
        delegate = OpenAIProvider.__new__(OpenAIProvider)
        delegate.model   = self.model
        delegate.api_key = self.api_key
        delegate._client = self._client
        async for chunk in delegate.complete(messages, tools, stream, temperature, max_tokens):
            yield chunk


# ---------------------------------------------------------------------------
# Register providers
# ---------------------------------------------------------------------------

from . import LLMProvider, register_provider  # noqa: E402

register_provider(LLMProvider.OPENAI, OpenAIProvider)
register_provider(LLMProvider.AZURE_OPENAI, AzureOpenAIProvider)
register_provider(LLMProvider.VLLM, VLLMProvider)
register_provider(LLMProvider.GROQ, GroqProvider)
register_provider(LLMProvider.DEEPSEEK, DeepSeekProvider)
register_provider(LLMProvider.XAI, XAIProvider)
register_provider(LLMProvider.OPENROUTER, OpenRouterProvider)
register_provider(LLMProvider.PERPLEXITY, PerplexityProvider)
register_provider(LLMProvider.MISTRAL, MistralProvider)
register_provider(LLMProvider.MINIMAX, MinimaxProvider)
