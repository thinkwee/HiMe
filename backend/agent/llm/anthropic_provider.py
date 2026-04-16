"""
Anthropic Claude 4.x+ provider.

Supported models: claude-opus-4, claude-sonnet-4, claude-haiku-4 and later.

Streaming tool-call handling:
  Anthropic's streaming protocol for tool use:
    1. ``content_block_start`` with ``type="tool_use"`` announces a new tool call.
    2. Subsequent ``content_block_delta`` with ``delta.type="input_json_delta"``
       carry incremental argument JSON fragments.
    3. ``content_block_stop`` signals end of this tool call block.
  We accumulate all ``input_json_delta`` strings per tool-call index and parse
  the complete JSON only at ``content_block_stop``.
  (Reference: https://docs.anthropic.com/en/docs/build-with-claude/tool-use)

Reasoning policy:
  Extended thinking is available from claude-sonnet-4 onwards.  We set
  ``budget_tokens=1024`` (minimum meaningful budget) to keep latency low.
  Agent prompts don't benefit from deep thinking — a short thinking budget
  ensures we get reasoning traces in the monitor without excessive cost.

Message format:
  Anthropic uses a different message schema from OpenAI:
    - ``role: "user"`` / ``role: "assistant"`` only
    - System prompt is a top-level ``system`` parameter
    - Tool results are ``role: "user"`` with ``type: "tool_result"`` content
  The agent loop sends tool results as plain ``role: "user"`` text, which is
  perfectly compatible with Claude's chat API.
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
    retry_async,
)

logger = logging.getLogger(__name__)


class AnthropicProvider(BaseLLMProvider):
    """Anthropic Claude 4.5+ with streaming tool use and optional extended thinking."""

    def __init__(
        self,
        model: str = "claude-opus-4-5",
        api_key: str | None = None,
        *,
        thinking_budget: int | None = 1024,
    ) -> None:
        super().__init__(model, api_key)
        self.thinking_budget = thinking_budget
        try:
            import anthropic  # type: ignore
            self._client = anthropic.AsyncAnthropic(api_key=api_key)
        except ImportError as exc:
            raise ImportError(
                "anthropic package required. Install: pip install anthropic>=0.40"
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
    ) -> AsyncIterator[dict[str, Any]]:
        _t0 = time.perf_counter()
        try:
            system_prompt, filtered_messages = self._extract_system(messages)

            request_kwargs: dict[str, Any] = {
                "model":      self.model,
                "messages":   filtered_messages,
                "max_tokens": max_tokens,
            }
            if system_prompt:
                # Mark the entire system prompt as cacheable so subsequent
                # turns / sessions hit prompt cache.  Anthropic requires
                # the list-of-blocks form to attach ``cache_control``.
                request_kwargs["system"] = [
                    {
                        "type": "text",
                        "text": system_prompt,
                        "cache_control": {"type": "ephemeral"},
                    }
                ]

            # Temperature is only valid outside extended thinking
            if self.thinking_budget is None:
                request_kwargs["temperature"] = temperature

            # Extended thinking (claude-4.x+)
            if self.thinking_budget is not None:
                request_kwargs["thinking"] = {
                    "type":         "enabled",
                    "budget_tokens": self.thinking_budget,
                }

            # Tool definitions
            if tools:
                request_kwargs["tools"] = self._convert_tools(tools)

            # Use the streaming context manager
            async def _stream_call():
                return self._client.messages.stream(**request_kwargs)

            # We use the stream context to correctly handle streaming
            resp_ctx = await retry_async(_stream_call)

            # --- Accumulation buffers ---
            # Maps block_index -> {"id": str, "name": str, "arguments_buf": str}
            tool_accum: dict[int, dict[str, str]] = {}

            response_texts: list[str] = []
            tool_calls_list: list[dict] = []
            prompt_tokens = completion_tokens = None
            cache_read_tokens: int | None = None
            cache_creation_tokens: int | None = None
            stop_reason: str | None = None

            async with resp_ctx as stream:
                async for event in stream:
                    etype = event.type

                    # --- Message start ---
                    if etype == "message_start":
                        usage = getattr(event.message, "usage", None)
                        if usage:
                            prompt_tokens = getattr(usage, "input_tokens", None)
                            # Prompt-cache stats: input_tokens excludes both
                            # cached read and cache-creation tokens, so add
                            # them in for an accurate "billed input" total.
                            cache_read_tokens = getattr(usage, "cache_read_input_tokens", None)
                            cache_creation_tokens = getattr(usage, "cache_creation_input_tokens", None)

                    # --- Usage (end of message) ---
                    elif etype == "message_delta":
                        delta = getattr(event, "delta", None)
                        if delta:
                            sr = getattr(delta, "stop_reason", None)
                            if sr:
                                stop_reason = sr
                        usage = getattr(event, "usage", None)
                        if usage:
                            completion_tokens = getattr(usage, "output_tokens", None)

                    # --- Content block start ---
                    elif etype == "content_block_start":
                        block = event.content_block
                        idx   = event.index

                        if block.type == "thinking":
                            pass  # thinking trace — deltas follow

                        elif block.type == "tool_use":
                            # Register the new tool call accumulator
                            tool_accum[idx] = {
                                "id":           block.id,
                                "name":         block.name,
                                "arguments_buf": "",
                            }

                    # --- Content block delta ---
                    elif etype == "content_block_delta":
                        delta = event.delta
                        idx   = event.index

                        if delta.type == "text_delta":
                            text = delta.text or ""
                            response_texts.append(text)
                            yield {"type": "content", "content": text}

                        elif delta.type == "thinking_delta":
                            thinking_text = delta.thinking or ""
                            yield {"type": "agent_thinking", "content": thinking_text}

                        elif delta.type == "input_json_delta":
                            # Accumulate partial JSON argument string
                            if idx in tool_accum:
                                tool_accum[idx]["arguments_buf"] += delta.partial_json or ""

                    # --- Content block stop ---
                    elif etype == "content_block_stop":
                        idx = event.index
                        if idx in tool_accum:
                            # Parse and emit the complete tool call
                            tc_info = tool_accum[idx]
                            try:
                                args = (
                                    json.loads(tc_info["arguments_buf"])
                                    if tc_info["arguments_buf"]
                                    else {}
                                )
                            except json.JSONDecodeError:
                                logger.warning(
                                    "Could not parse tool arguments for %s: %s",
                                    tc_info["name"], tc_info["arguments_buf"][:200],
                                )
                                args = {"_raw": tc_info["arguments_buf"]}
                            tc = {"id": tc_info["id"], "name": tc_info["name"], "arguments": args}
                            tool_calls_list.append(tc)
                            yield {"type": "tool_call", **tc}

            # Yield token usage with truncation flag
            if prompt_tokens is not None or completion_tokens is not None:
                truncated = (
                    stop_reason == "max_tokens"
                    or (completion_tokens is not None and completion_tokens >= max_tokens)
                )
                yield {
                    "type": "token_usage",
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "cache_read_tokens": cache_read_tokens,
                    "cache_creation_tokens": cache_creation_tokens,
                    "truncated": truncated,
                    "max_tokens": max_tokens,
                }

            provider_write_log(
                provider="anthropic",
                model=self.model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cache_read_tokens=cache_read_tokens,
                cache_creation_tokens=cache_creation_tokens,
                duration_ms=int((time.perf_counter() - _t0) * 1000),
                tools=tools,
                messages=messages,
                tool_calls_list=tool_calls_list,
                response_texts=response_texts,
            )

        except Exception as exc:
            logger.error("Anthropic API error: %s", exc, exc_info=True)
            provider_write_log(
                provider="anthropic",
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

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_system(messages: list[dict]) -> tuple[str | None, list[dict]]:
        """Separate ``role: system`` messages and convert to Anthropic native format.

        Handles:
          - ``role: "system"`` → extracted to top-level system parameter
          - ``role: "assistant"`` with ``tool_calls`` → content blocks with tool_use
          - ``role: "tool"`` with ``tool_call_id`` → user message with tool_result blocks
        """
        system_parts: list[str] = []
        filtered: list[dict]    = []

        # Collect tool results that need to be merged into a single user message
        pending_tool_results: list[dict] = []

        def _flush_tool_results():
            if pending_tool_results:
                filtered.append({
                    "role": "user",
                    "content": list(pending_tool_results),
                })
                pending_tool_results.clear()

        for msg in messages:
            role = msg["role"]

            if role == "system":
                system_parts.append(msg.get("content") or "")
                continue

            if role == "tool":
                # Accumulate tool results — they must be in a single user message
                pending_tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": msg.get("tool_call_id", ""),
                    "content": msg.get("content", ""),
                })
                continue

            # Flush any pending tool results before a non-tool message
            _flush_tool_results()

            if role == "assistant" and msg.get("tool_calls"):
                # Convert OpenAI-style tool_calls to Anthropic content blocks
                content_blocks = []
                text = msg.get("content")
                if text:
                    content_blocks.append({"type": "text", "text": text})
                for tc in msg["tool_calls"]:
                    fn = tc.get("function", {})
                    args = fn.get("arguments", "{}")
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except json.JSONDecodeError:
                            tool_name = fn.get("name", "unknown")
                            logger.warning("Malformed tool args for %s, using {}", tool_name)
                            args = {}
                    content_blocks.append({
                        "type": "tool_use",
                        "id": tc.get("id", ""),
                        "name": fn.get("name", ""),
                        "input": args,
                    })
                filtered.append({"role": "assistant", "content": content_blocks})
            else:
                filtered.append({"role": role, "content": msg.get("content") or ""})

        _flush_tool_results()
        system = "\n".join(system_parts) if system_parts else None
        return system, filtered

    @staticmethod
    def _convert_tools(tools: list[dict]) -> list[dict]:
        """Convert OpenAI-style tool definitions to Anthropic format."""
        converted = []
        for tool in tools:
            if tool.get("type") != "function" or "function" not in tool:
                continue
            fn = tool["function"]
            converted.append(
                {
                    "name":         fn["name"],
                    "description":  fn.get("description", ""),
                    "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
                }
            )
        return converted


# ---------------------------------------------------------------------------
# Register provider
# ---------------------------------------------------------------------------

from . import LLMProvider, register_provider  # noqa: E402

register_provider(LLMProvider.ANTHROPIC, AnthropicProvider)
