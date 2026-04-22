"""
Google Gemini provider — supports function calling and extended thinking.

Supported models: gemini-2.5-flash, gemini-2.5-pro, gemini-3.x and later.

Thinking policy:
  - ``include_thoughts=True`` is always set so reasoning traces are visible in the monitor.
  - Default ``thinking_budget=0`` disables thinking on 2.5-flash for minimum
    latency in agentic tool-calling loops. Callers may override.
  - gemini-2.5-pro cannot fully disable thinking (API minimum is 128); we clamp
    budgets <128 up to 128 on that model.
  - On gemini-3.x models we use ``thinking_level="minimal"`` for efficiency.
"""
from __future__ import annotations

import asyncio
import base64
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


class GeminiProvider(BaseLLMProvider):
    """Google Gemini with native function calling and streaming thoughts."""

    def __init__(
        self,
        model: str = "gemini-2.5-flash",
        api_key: str | None = None,
        *,
        thinking_budget: int | None = 0,
    ) -> None:
        super().__init__(model, api_key)
        self.thinking_budget = thinking_budget
        try:
            from google import genai  # type: ignore
            self._client = genai.Client(api_key=api_key)
        except ImportError as exc:
            raise ImportError(
                "google-genai package required. Install: pip install google-genai"
            ) from exc

    # ------------------------------------------------------------------
    # Interface
    # ------------------------------------------------------------------

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
        from google.genai import types  # type: ignore

        _t0 = time.perf_counter()
        try:
            contents, system_instruction = self._build_contents(messages, types)

            if not contents:
                yield {"type": "error", "error": "No valid messages to send"}
                return

            config_dict: dict[str, Any] = {
                "temperature": temperature,
                "max_output_tokens": max_tokens,
            }

            # Thinking config — include thoughts for monitor visibility
            if "gemini-3" in self.model.lower():
                config_dict["thinking_config"] = types.ThinkingConfig(
                    include_thoughts=True,
                    thinking_level="minimal",
                )
            else:
                # gemini-2.5+: default budget is 0 (disables thinking on flash
                # for minimum latency). 2.5-pro cannot disable — API floor is
                # 128 — so clamp up on that model.
                tc_kwargs: dict[str, Any] = {"include_thoughts": True}
                if self.thinking_budget is not None:
                    budget = self.thinking_budget
                    if "gemini-2.5-pro" in self.model.lower() and 0 <= budget < 128:
                        budget = 128
                    tc_kwargs["thinking_budget"] = budget
                config_dict["thinking_config"] = types.ThinkingConfig(**tc_kwargs)

            if system_instruction:
                config_dict["system_instruction"] = system_instruction

            if tools:
                declarations = self._convert_tools(tools, types)
                if declarations:
                    config_dict["tools"] = [types.Tool(function_declarations=declarations)]

            config = types.GenerateContentConfig(**config_dict)

            # Gemini SDK is synchronous — run in thread pool
            # 120s timeout prevents indefinite hangs when Gemini API is overloaded
            response = await asyncio.wait_for(
                retry_async(
                    lambda: asyncio.to_thread(
                        self._client.models.generate_content,
                        model=self.model,
                        contents=contents,
                        config=config,
                    )
                ),
                timeout=120.0,
            )

            # Handle blocked prompt
            if not response.candidates:
                block = (
                    response.prompt_feedback.block_reason
                    if response.prompt_feedback
                    else "unknown"
                )
                yield {"type": "error", "error": f"Prompt blocked: {block}"}
                return

            candidate = response.candidates[0]
            content   = candidate.content
            usage     = getattr(response, "usage_metadata", None) or getattr(response, "usage", None)

            prompt_tokens, completion_tokens, thoughts_tokens = _get_usage(usage)
            cache_read_tokens = _get_cache_read(usage)
            response_tokens = None
            if completion_tokens is not None and thoughts_tokens is not None:
                response_tokens = max(0, completion_tokens - thoughts_tokens)
            elif completion_tokens is not None:
                response_tokens = completion_tokens

            response_texts: list[str] = []
            tool_calls_list: list[dict] = []

            if content and content.parts:
                logger.info("Gemini response parts: %s", [(type(p).__name__, {a: type(getattr(p, a, None)).__name__ for a in ('text', 'thought', 'thought_signature', 'function_call') if getattr(p, a, None) is not None}) for p in content.parts])
                for part in content.parts:
                    # Extended thinking trace
                    if getattr(part, "thought", False) and getattr(part, "text", None):
                        response_texts.append(f"<thought>\n{part.text}\n</thought>")
                        yield {"type": "agent_thinking", "content": part.text}

                    # Thought signature (must be forwarded back to the model)
                    if getattr(part, "thought_signature", None):
                        # Base64-encode raw bytes so the signature survives JSON serialization
                        sig = part.thought_signature
                        if isinstance(sig, bytes):
                            sig = base64.b64encode(sig).decode("ascii")
                        yield {"type": "thought_signature", "signature": sig}

                    # Text content
                    if getattr(part, "text", None) and not getattr(part, "thought", False):
                        response_texts.append(part.text)
                        yield {"type": "content", "content": part.text}

                    # Function / tool call
                    if getattr(part, "function_call", None):
                        fc   = part.function_call
                        args = dict(fc.args) if (fc.args and hasattr(fc.args, "items")) else {}
                        fc_id = getattr(fc, "id", "") or ""
                        tc   = {"id": fc_id, "name": fc.name, "arguments": args}
                        tool_calls_list.append(tc)
                        yield {"type": "tool_call", **tc}
            else:
                yield {"type": "content", "content": ""}

            if prompt_tokens is not None or completion_tokens is not None:
                # Gemini uses "MAX_TOKENS" as finish_reason when truncated
                finish_reason = getattr(candidate, "finish_reason", None)
                # finish_reason may be an enum or string
                fr_str = str(finish_reason).upper() if finish_reason else ""
                truncated = (
                    "MAX_TOKENS" in fr_str
                    or (completion_tokens is not None and max_tokens is not None and completion_tokens >= max_tokens)
                )
                yield {
                    "type": "token_usage",
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "thoughts_tokens": thoughts_tokens,
                    "response_tokens": response_tokens,
                    "cache_read_tokens": cache_read_tokens,
                    "truncated": truncated,
                    "max_tokens": max_tokens,
                }
            else:
                logger.debug("Gemini: no usage_metadata (usage=%s)", usage)

            provider_write_log(
                provider="gemini",
                model=self.model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                thoughts_tokens=thoughts_tokens,
                response_tokens=response_tokens,
                duration_ms=int((time.perf_counter() - _t0) * 1000),
                tools=tools,
                messages=messages,
                tool_calls_list=tool_calls_list,
                response_texts=response_texts,
            )

        except Exception as exc:
            logger.error("Gemini API error: %s", exc, exc_info=True)
            provider_write_log(
                provider="gemini",
                model=self.model,
                prompt_tokens=None,
                completion_tokens=None,
                thoughts_tokens=None,
                response_tokens=None,
                duration_ms=int((time.perf_counter() - _t0) * 1000),
                tools=tools,
                messages=messages,
                tool_calls_list=[],
                response_texts=[f"ERROR: {type(exc).__name__}: {exc}"],
            )
            yield {"type": "error", "error": str(exc)}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_contents(self, messages: list[dict], types) -> tuple:
        """Convert OpenAI-style messages to Gemini Contents + system instruction.

        Handles:
          - ``role: "assistant"`` with ``tool_calls`` → model parts with functionCall
          - ``role: "tool"`` with ``tool_call_id`` → user parts with functionResponse
        """
        import json as _json
        contents = []
        system_parts: list[str] = []

        # Accumulate tool results into a single user Content
        pending_fn_responses: list = []

        def _flush_fn_responses():
            if pending_fn_responses:
                contents.append(types.Content(role="user", parts=list(pending_fn_responses)))
                pending_fn_responses.clear()

        for msg in messages:
            role    = msg["role"]
            content = msg.get("content", "") or ""
            signature = msg.get("signature")

            if role == "system":
                system_parts.append(content)
                continue

            # --- role: "tool" → Gemini functionResponse ---
            if role == "tool":
                # Parse content as JSON dict if possible (Gemini requires dict response)
                resp_data: Any = {}
                if content:
                    try:
                        resp_data = _json.loads(content)
                    except (_json.JSONDecodeError, TypeError):
                        resp_data = {"result": content}
                if not isinstance(resp_data, dict):
                    resp_data = {"result": resp_data}

                fn_resp_kwargs: dict[str, Any] = {
                    "name": msg.get("_tool_name", "tool"),
                    "response": resp_data,
                }
                tc_id = msg.get("tool_call_id", "")
                if tc_id:
                    fn_resp_kwargs["id"] = tc_id
                pending_fn_responses.append(
                    types.Part(function_response=types.FunctionResponse(**fn_resp_kwargs))
                )
                continue

            # Flush pending function responses before any non-tool message
            _flush_fn_responses()

            # --- role: "assistant" with tool_calls → model functionCall parts ---
            if role == "assistant" and msg.get("tool_calls"):
                parts = []
                # Decode thought signature (must be attached to each functionCall Part)
                sig_bytes = None
                if signature:
                    sig_bytes = self._decode_signature(signature)
                if content:
                    parts.append(types.Part(text=content))
                for tc in msg["tool_calls"]:
                    fn = tc.get("function", {})
                    args_raw = fn.get("arguments", "{}")
                    if isinstance(args_raw, str):
                        try:
                            args_dict = _json.loads(args_raw)
                        except _json.JSONDecodeError:
                            args_dict = {}
                    else:
                        args_dict = args_raw if isinstance(args_raw, dict) else {}
                    fc_kwargs: dict[str, Any] = {"name": fn.get("name", ""), "args": args_dict}
                    tc_id = tc.get("id", "")
                    if tc_id:
                        fc_kwargs["id"] = tc_id
                    part_kwargs: dict[str, Any] = {"function_call": types.FunctionCall(**fc_kwargs)}
                    if sig_bytes:
                        part_kwargs["thought_signature"] = sig_bytes
                    parts.append(types.Part(**part_kwargs))
                if parts:
                    contents.append(types.Content(role="model", parts=parts))
                continue

            # --- Regular messages ---
            gemini_role = "user" if role in ("user",) else "model"
            parts = []
            if signature:
                sig_bytes = self._decode_signature(signature)
                if sig_bytes:
                    parts.append(types.Part(thought_signature=sig_bytes))
            if content:
                parts.append(types.Part(text=content))
            if parts:
                contents.append(types.Content(role=gemini_role, parts=parts))

        _flush_fn_responses()

        system_instruction = None
        if system_parts:
            system_instruction = types.Content(
                role="system",
                parts=[types.Part(text="\n".join(system_parts))],
            )
        return contents, system_instruction

    @staticmethod
    def _decode_signature(signature) -> bytes | None:
        """Decode a thought signature to bytes."""
        if isinstance(signature, bytes):
            return signature
        if isinstance(signature, str):
            try:
                return base64.b64decode(signature)
            except Exception:
                try:
                    import ast
                    val = ast.literal_eval(signature)
                    if isinstance(val, bytes):
                        return val
                except Exception:
                    logger.warning("Skipping undecodable thought_signature")
        return None

    def _convert_tools(self, tools: list[dict], types) -> list:
        """Convert OpenAI-format tool definitions to Gemini FunctionDeclaration objects."""
        declarations = []
        for tool in tools:
            if tool.get("type") != "function" or "function" not in tool:
                continue
            fn     = tool["function"]
            params = fn.get("parameters") or {"type": "object", "properties": {}}
            schema = self._convert_schema(params, types)
            declarations.append(
                types.FunctionDeclaration(
                    name=fn["name"],
                    description=fn.get("description", ""),
                    parameters=schema,
                )
            )
        return declarations or []

    def _convert_schema(self, params: dict, types) -> Any:
        """Recursively convert OpenAI JSON Schema to Gemini Schema."""
        _type_map = {
            "string": "STRING", "number": "NUMBER", "integer": "INTEGER",
            "boolean": "BOOLEAN", "array": "ARRAY", "object": "OBJECT",
        }
        gemini_type = _type_map.get((params.get("type") or "object").lower(), "STRING")
        kwargs: dict[str, Any] = {"type": gemini_type}
        if params.get("description"):
            kwargs["description"] = params["description"]
        if params.get("properties"):
            kwargs["properties"] = {
                k: self._convert_schema(v, types)
                for k, v in params["properties"].items()
                if isinstance(v, dict)
            }
        if params.get("required"):
            kwargs["required"] = params["required"]
        if params.get("enum"):
            kwargs["enum"] = params["enum"]
        if params.get("items") and isinstance(params["items"], dict):
            kwargs["items"] = self._convert_schema(params["items"], types)
        return types.Schema(**kwargs)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_attr(obj, *names):
    if obj is None:
        return None
    for name in names:
        val = getattr(obj, name, None)
        if val is not None:
            return val
    return None


def _get_cache_read(usage) -> int | None:
    """Extract cached input tokens from Gemini usage_metadata (implicit + explicit cache)."""
    if usage is None:
        return None
    return _safe_attr(usage, "cached_content_token_count", "cached_token_count")


def _get_usage(usage) -> tuple:
    """Extract (prompt_tokens, completion_tokens, thoughts_tokens) from usage_metadata."""
    if usage is None:
        return None, None, None
    prompt = _safe_attr(usage, "prompt_token_count", "prompt_tokens", "input_token_count")
    completion = _safe_attr(usage, "candidates_token_count", "completion_tokens", "output_token_count")
    thoughts = _safe_attr(usage, "thoughts_token_count", "thinking_token_count")
    return prompt, completion, thoughts




class GoogleVertexProvider(GeminiProvider):
    """Google Vertex AI (via Google GenAI SDK)."""

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        project: str | None = None,
        location: str | None = None,
        *,
        thinking_budget: int | None = 0,
    ) -> None:
        # We don't call super().__init__ because we need to pass vertexai params to the client
        BaseLLMProvider.__init__(self, model, api_key)
        self.thinking_budget = thinking_budget
        try:
            import os

            from google import genai  # type: ignore
            self._client = genai.Client(
                vertexai=True,
                project=project or os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCLOUD_PROJECT"),
                location=location or os.getenv("GOOGLE_CLOUD_LOCATION") or "us-central1",
            )
        except ImportError as exc:
            raise ImportError(
                "google-genai package required. Install: pip install google-genai"
            ) from exc


# ---------------------------------------------------------------------------
# Register provider
# ---------------------------------------------------------------------------

from . import LLMProvider, register_provider  # noqa: E402

register_provider(LLMProvider.GEMINI, GeminiProvider)
register_provider(LLMProvider.GOOGLE_VERTEX, GoogleVertexProvider)
