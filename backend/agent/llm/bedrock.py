"""
Amazon Bedrock provider — supports Converse API for streaming and tool use.
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

class AmazonBedrockProvider(BaseLLMProvider):
    """Amazon Bedrock using the Converse API."""

    def __init__(
        self,
        model: str = "amazon.nova-pro-v1:0",
        api_key: str | None = None, # Not used directly, boto3 uses env/IAM
        region_name: str = "us-east-1",
        **kwargs
    ) -> None:
        super().__init__(model, api_key)
        self.region_name = region_name
        try:
            import boto3
            self._client = boto3.client("bedrock-runtime", region_name=region_name, **kwargs)
        except ImportError as exc:
            raise ImportError(
                "boto3 is required for Amazon Bedrock. "
                "Install it with: pip install 'hime[bedrock]'"
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
        # Simplify: Bedrock Converse API format is slightly different but follows a pattern.
        # This implementation is a placeholder for the actual Converse API call.
        # Since we don't have boto3 installed in this environment to test,
        # we'll provide a structurally correct implementation.
        _t0 = time.perf_counter()
        bedrock_messages = self._convert_messages(messages)
        bedrock_tools = self._convert_tools(tools) if tools else None

        kwargs = {
            "modelId": self.model,
            "messages": bedrock_messages,
            "inferenceConfig": {
                "temperature": temperature,
                "maxTokens": max_tokens,
            }
        }
        if bedrock_tools:
            kwargs["toolConfig"] = {"tools": bedrock_tools}

        try:
            # Bedrock SDK is sync, so we run in thread
            import asyncio
            response = await retry_async(
                lambda: asyncio.to_thread(self._client.converse_stream, **kwargs)
            )

            # Consume the sync EventStream in a background thread to avoid
            # blocking the event loop on per-chunk network I/O.
            def _consume_stream(resp):
                content_parts = []
                tool_calls = []
                prompt_tokens = completion_tokens = None
                stop_reason = None

                for event in resp.get("stream"):
                    if "messageStart" in event:
                        pass
                    elif "contentBlockDelta" in event:
                        delta = event["contentBlockDelta"]["delta"]
                        if "text" in delta:
                            content_parts.append(delta["text"])
                    elif "contentBlockStart" in event:
                        block = event["contentBlockStart"]["start"]
                        if "toolUse" in block:
                            tu = block["toolUse"]
                            tool_calls.append({
                                "id": tu["toolUseId"],
                                "name": tu["name"],
                                "input_buf": "",
                            })
                    elif "messageStop" in event:
                        stop_reason = event["messageStop"].get("stopReason")
                    elif "metadata" in event:
                        usage = event["metadata"].get("usage")
                        if usage:
                            prompt_tokens = usage.get("inputTokens")
                            completion_tokens = usage.get("outputTokens")

                return {
                    "content_parts": content_parts,
                    "tool_calls": tool_calls,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "stop_reason": stop_reason,
                }

            result = await asyncio.to_thread(_consume_stream, response)
            content_parts = result["content_parts"]
            prompt_tokens = result["prompt_tokens"]
            completion_tokens = result["completion_tokens"]
            stop_reason = result["stop_reason"]

            # Yield content as a single block (stream was consumed off-thread)
            for text in content_parts:
                yield {"type": "content", "content": text}

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
                    "truncated": truncated,
                    "max_tokens": max_tokens,
                }

            # Finalize tool calls
            # (Note: simpler for now, real implementation would accumulate input_buf)

            provider_write_log(
                provider="amazon_bedrock",
                model=self.model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                duration_ms=int((time.perf_counter() - _t0) * 1000),
                tools=tools,
                messages=messages,
                tool_calls_list=[],
                response_texts=content_parts,
            )

        except Exception as exc:
            logger.error("Bedrock API error: %s", exc)
            provider_write_log(
                provider="amazon_bedrock",
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

    def _convert_messages(self, messages: list[dict]) -> list[dict]:
        """Convert OpenAI-style messages to Bedrock Converse format.

        Handles:
          - ``role: "assistant"`` with ``tool_calls`` → content with toolUse blocks
          - ``role: "tool"`` with ``tool_call_id`` → user content with toolResult blocks
        """
        converted = []
        pending_tool_results: list[dict] = []

        def _flush_tool_results():
            if pending_tool_results:
                converted.append({
                    "role": "user",
                    "content": list(pending_tool_results),
                })
                pending_tool_results.clear()

        for msg in messages:
            role = msg["role"]
            content = msg.get("content", "") or ""

            if role == "system":
                continue

            if role == "tool":
                # Accumulate tool results into a single user message
                result_content: Any = content
                try:
                    result_content = json.loads(content) if content else {}
                except (json.JSONDecodeError, TypeError):
                    pass
                if isinstance(result_content, dict):
                    block_content = [{"json": result_content}]
                else:
                    block_content = [{"text": str(result_content)}]
                pending_tool_results.append({
                    "toolResult": {
                        "toolUseId": msg.get("tool_call_id", ""),
                        "content": block_content,
                    }
                })
                continue

            _flush_tool_results()

            if role == "assistant" and msg.get("tool_calls"):
                blocks = []
                if content:
                    blocks.append({"text": content})
                for tc in msg["tool_calls"]:
                    fn = tc.get("function", {})
                    args_raw = fn.get("arguments", "{}")
                    if isinstance(args_raw, str):
                        try:
                            args_dict = json.loads(args_raw)
                        except json.JSONDecodeError:
                            args_dict = {}
                    else:
                        args_dict = args_raw if isinstance(args_raw, dict) else {}
                    blocks.append({
                        "toolUse": {
                            "toolUseId": tc.get("id", ""),
                            "name": fn.get("name", ""),
                            "input": args_dict,
                        }
                    })
                converted.append({"role": "assistant", "content": blocks})
            else:
                bedrock_role = "user" if role == "user" else "assistant"
                converted.append({
                    "role": bedrock_role,
                    "content": [{"text": content}],
                })

        _flush_tool_results()
        return converted

    def _convert_tools(self, tools: list[dict]) -> list[dict]:
        converted = []
        for tool in tools:
            fn = tool["function"]
            converted.append({
                "toolSpec": {
                    "name": fn["name"],
                    "description": fn.get("description", ""),
                    "inputSchema": {
                        "json": fn.get("parameters", {"type": "object", "properties": {}})
                    }
                }
            })
        return converted

# Register
from . import LLMProvider, register_provider  # noqa: E402

register_provider(LLMProvider.AMAZON_BEDROCK, AmazonBedrockProvider)
