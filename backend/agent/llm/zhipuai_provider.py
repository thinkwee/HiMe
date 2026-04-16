"""
ZhipuAI provider — uses the ``zai-sdk`` package (``ZhipuAiClient``)
which exposes an OpenAI-compatible ``chat.completions`` interface.
"""
from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

from . import (
    BaseLLMProvider,
    provider_write_log,
)

logger = logging.getLogger(__name__)

class ZhipuAIProvider(BaseLLMProvider):
    """ZhipuAI provider via ``zai-sdk`` (OpenAI-compatible ``chat.completions`` API)."""

    def __init__(
        self,
        model: str = "glm-4.7-flash",
        api_key: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(model, api_key)
        try:
            from zai import (
                ZhipuAiClient,  # Assuming this is the correct client name from the snippet
            )
            # The snippet showed sync usage. We'll check for async if it exists,
            # or use the sync one if that's all there is (though async is preferred).
            # For now, following the user's snippet directly.
            self._client = ZhipuAiClient(api_key=api_key)
        except ImportError as exc:
            raise ImportError(
                "zai-sdk package required. Install: pip install zai-sdk"
            ) from exc

    def supports_tools(self) -> bool:
        return True

    async def complete(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        stream: bool = True,
        temperature: float = 0.7,
        max_tokens: int = 8192,
        thinking: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """
        Complete a chat with ZhipuAI native API.

        Parameters
        ----------
        thinking:
            ``"enabled"`` to turn on chain-of-thought for GLM-4.7+ models,
            ``"disabled"`` to force it off, or ``None`` to let the model decide
            (currently defaults to disabled to save tokens).
        """
        _t0 = time.perf_counter()
        try:
            resolved_thinking = thinking if thinking in ("enabled", "disabled") else "disabled"
            kwargs: dict[str, Any] = {
                "model": self.model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "stream": True,  # Always stream for consistency
                "thinking": {
                    "type": resolved_thinking,
                },
            }
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"

            # Check if there's an async method. If not, we'll run it in a thread.
            # Most modern SDKs have .chat.completions.create as either sync or async.
            # Given the snippet, it looks sync. To keep the agent non-blocking,
            # we should ideally use an async client if it exists.

            import asyncio

            def _call_sync():
                return self._client.chat.completions.create(**kwargs)

            # ZhipuAI's response object when stream=True is an iterator.
            # If the call itself is blocking, we wrap it.
            loop = asyncio.get_running_loop()
            response_iter = await loop.run_in_executor(None, _call_sync)

            tool_accum: dict[int, dict[str, str]] = {}
            content_parts: list[str] = []
            thought_parts: list[str] = []
            prompt_tokens = completion_tokens = thoughts_tokens = None
            cache_read_tokens: int | None = None
            finish_reason: str | None = None

            # Iterate over the sync iterator in the background thread to not block the event loop
            # Or use a wrapper to make it async.

            def get_next_chunk(it):
                try:
                    return next(it)
                except StopIteration:
                    return None

            while True:
                chunk = await loop.run_in_executor(None, get_next_chunk, response_iter)
                if chunk is None:
                    break

                # Usage info might be in the chunk or separate
                if hasattr(chunk, 'usage') and chunk.usage:
                    prompt_tokens = getattr(chunk.usage, 'prompt_tokens', prompt_tokens)
                    completion_tokens = getattr(chunk.usage, 'completion_tokens', completion_tokens)
                    prompt_details = getattr(chunk.usage, 'prompt_tokens_details', None)
                    if prompt_details is not None:
                        cached = getattr(prompt_details, 'cached_tokens', None)
                        if cached is None and isinstance(prompt_details, dict):
                            cached = prompt_details.get('cached_tokens')
                        if cached is not None:
                            cache_read_tokens = cached

                if not chunk.choices:
                    continue

                choice = chunk.choices[0]
                delta = choice.delta

                # Track finish reason for truncation detection
                if getattr(choice, 'finish_reason', None):
                    finish_reason = choice.finish_reason

                # Reasoning / Thinking content
                reasoning = getattr(delta, "reasoning_content", None)
                if reasoning:
                    thought_parts.append(reasoning)
                    yield {"type": "agent_thinking", "content": reasoning}

                # Text content
                if delta.content:
                    content_parts.append(delta.content)
                    yield {"type": "content", "content": delta.content}

                # Tool call deltas — accumulate by index
                if hasattr(delta, 'tool_calls') and delta.tool_calls:
                    for tc_delta in delta.tool_calls:
                        idx = getattr(tc_delta, 'index', 0)
                        if idx not in tool_accum:
                            tool_accum[idx] = {
                                "id": getattr(tc_delta, 'id', "") or "",
                                "name": "",
                                "arguments": "",
                            }
                        func = getattr(tc_delta, 'function', None)
                        if func:
                            if func.name:
                                tool_accum[idx]["name"] += func.name
                            if func.arguments:
                                # Handle both str and dict arguments from the API
                                if isinstance(func.arguments, dict):
                                    tool_accum[idx]["arguments"] += json.dumps(func.arguments)
                                else:
                                    tool_accum[idx]["arguments"] += func.arguments

            # Yield accumulated tool calls
            completed_tools = []
            for tc_info in tool_accum.values():
                raw_args = tc_info["arguments"]
                if isinstance(raw_args, dict):
                    args = raw_args
                elif isinstance(raw_args, str) and raw_args:
                    try:
                        args = json.loads(raw_args)
                    except json.JSONDecodeError:
                        logger.warning(
                            "Could not parse tool arguments for %s: %s",
                            tc_info["name"], raw_args[:200],
                        )
                        args = {"_raw": raw_args}
                else:
                    args = {}

                tool_call = {"id": tc_info["id"], "name": tc_info["name"], "arguments": args}
                completed_tools.append(tool_call)
                yield {"type": "tool_call", **tool_call}

            # Estimate thoughts_tokens from reasoning text when API doesn't provide it
            thoughts_tokens = None
            if thought_parts:
                reasoning_text = "".join(thought_parts)
                thoughts_tokens = max(1, len(reasoning_text) // 3)

            # Final Token Usage
            if prompt_tokens is not None or completion_tokens is not None:
                truncated = (
                    finish_reason == "length"
                    or (completion_tokens is not None and completion_tokens >= max_tokens)
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

            # Log the request
            full_response_parts = []
            if thought_parts:
                full_response_parts.append("<thought>\n" + "".join(thought_parts) + "\n</thought>")
            if content_parts:
                full_response_parts.append("".join(content_parts))

            provider_write_log(
                provider="zhipuai",
                model=self.model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                duration_ms=int((time.perf_counter() - _t0) * 1000),
                tools=tools,
                messages=messages,
                tool_calls_list=completed_tools,
                response_texts=full_response_parts,
            )

        except Exception as exc:
            logger.error("ZhipuAI API error: %s", exc, exc_info=True)
            provider_write_log(
                provider="zhipuai",
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

# Register provider
from . import LLMProvider, register_provider  # noqa: E402

register_provider(LLMProvider.ZHIPUAI, ZhipuAIProvider)
