"""
Analysis and chat loop implementations for the autonomous health agent.

Extracted from autonomous_agent.py to reduce file size.
These methods are mixed into AutonomousHealthAgent via AgentLoopsMixin.

Design philosophy: make the happy path trivially easy for the LLM, and make
failures graceful without retrying.  When the LLM fails to call a tool, we
use its text output directly (auto-reply / auto-push-report) instead of
injecting retry prompts that pollute context.
"""
import asyncio
import hashlib
import json
import logging
import re
import time
import uuid
from typing import Any

from ..config import settings
from ..utils import ts_now
from .errors import ErrorCategory, FallbackTriggered, classify_error
from .llm import set_llm_log_context

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers for evidence & meta-marker cleanup
# ---------------------------------------------------------------------------

_META_MARKER_RE = re.compile(
    r"\[(?:Repl\w*|TELEGRAM\s+MESSAGE\s+from\s+\S+)\s+at\s+[^\]]*\]:?\s*",
    re.IGNORECASE,
)


def _strip_meta_markers(text: str) -> str:
    """Remove LLM-generated meta markers like ``[Replied at ...]``."""
    return _META_MARKER_RE.sub("", text).strip()


# Regex matching raw tool call markup that should never reach end users.
_RAW_TOOL_CALL_RE = re.compile(
    r"<\|?tool_call\|?>.*?(?:</?tool_call\|?>|$)",
    re.DOTALL,
)


def _contains_raw_tool_call(text: str) -> bool:
    """Return True if *text* contains raw tool-call markup from the LLM."""
    return bool(
        "<tool_call>" in text
        or "<|tool_call>" in text
        or "<tool_call|>" in text
    )


def _strip_raw_tool_calls(text: str) -> str:
    """Remove raw tool-call markup that leaked through unparsed.

    Returns the cleaned text (which may be empty if the entire message was
    a tool-call attempt).
    """
    return _RAW_TOOL_CALL_RE.sub("", text).strip()


def _filter_data_evidence(tool_results: list[dict]) -> list[dict]:
    """Keep successful tool results as evidence (all tool types)."""
    return [
        tr for tr in tool_results
        if tr.get("result", {}).get("success", False)
    ]



def _hash_tool_calls(tool_calls: list[dict]) -> str:
    """Hash tool call names + arguments for duplicate detection."""
    sig = json.dumps(
        [(tc.get("name"), tc.get("arguments")) for tc in tool_calls],
        sort_keys=True, default=str,
    )
    return hashlib.md5(sig.encode()).hexdigest()


def _sanitize_json_string(raw: str) -> str:
    """Escape literal newlines/tabs inside JSON string values so json.loads succeeds."""
    out: list[str] = []
    in_str = False
    i = 0
    while i < len(raw):
        ch = raw[i]
        if ch == '"' and (i == 0 or raw[i - 1] != '\\'):
            in_str = not in_str
            out.append(ch)
        elif in_str and ch == '\n':
            out.append('\\n')
        elif in_str and ch == '\r':
            out.append('\\r')
        elif in_str and ch == '\t':
            out.append('\\t')
        else:
            out.append(ch)
        i += 1
    return ''.join(out)


def _gemma_normalize_quotes(body: str) -> str:
    """Convert Gemma's ``<|"|>`` string delimiters to real ``"`` while escaping
    any raw ``"`` that appear *inside* delimited regions.

    Gemma 4 emits ``<|"|>`` as JSON-string boundary markers, but the content
    between a pair of markers (e.g. Python source code) may contain literal
    double-quote characters.  A naïve global replace of ``<|"|>`` → ``"``
    turns those inner quotes into invalid JSON.  This function walks the text,
    replaces each ``<|"|>`` with ``"``, and escapes every raw ``"`` found
    between an opening and closing marker pair.
    """
    MARKER = '<|"|>'
    parts: list[str] = body.split(MARKER)
    # parts[0] is before the first marker, parts[1] is inside first pair, etc.
    # Even-indexed parts are outside strings, odd-indexed parts are inside.
    out: list[str] = []
    for idx, part in enumerate(parts):
        if idx % 2 == 1:
            # Inside a <|"|>…<|"|> pair — escape raw quotes
            out.append('"')
            out.append(part.replace('\\', '\\\\').replace('"', '\\"'))
            out.append('"')
        else:
            out.append(part)
    return "".join(out)


def _parse_xml_tool_calls(text: str) -> list[dict]:
    """Parse tool calls from model-generated XML when vLLM parser didn't extract them.

    Supports four formats:
      1. Hermes/JSON:  <tool_call>{"name": "fn", "arguments": {...}}</tool_call>
      2. GLM-4.7:      <tool_call>fn_name<arg_key>k</arg_key><arg_value>v</arg_value></tool_call>
      3. Qwen3.5:      <tool_call><function=fn_name><parameter=k>v</parameter></function></tool_call>
      5. Gemma 4:      <|tool_call>call:fn_name{"key": "val"}<tool_call|>
    """
    results: list[dict] = []

    # --- Format 5: Gemma 4  <|tool_call>call:fn_name{...}<tool_call|> ---
    # Gemma may use <|"|> as quote markers instead of real quotes.
    # Also handle incomplete calls where the closing <tool_call|> was truncated.
    for match in re.finditer(r"<\|tool_call>\s*(.*?)(?:\s*<tool_call\|>|\s*$)", text, re.DOTALL):
        body = match.group(1).strip()
        # Gemma uses <|"|> as JSON string delimiters, but the content between
        # them may contain raw " characters (e.g. Python code with print("...")).
        # We must escape those inner quotes before converting <|"|> to real ".
        body = _gemma_normalize_quotes(body)
        gemma_match = re.match(r"call:(\w+)\s*(\{.*\})", body, re.DOTALL)
        if gemma_match:
            fn_name = gemma_match.group(1)
            raw_json = gemma_match.group(2)
            # Gemma may emit raw newlines/tabs inside string values — escape them
            raw_json = _sanitize_json_string(raw_json)
            try:
                args = json.loads(raw_json)
                results.append({"name": fn_name, "arguments": args})
            except json.JSONDecodeError:
                # Keys may be unquoted — try adding quotes around bare keys
                fixed = re.sub(r'(?<=[{,])\s*(\w+)\s*:', r' "\1":', raw_json)
                try:
                    args = json.loads(fixed)
                    results.append({"name": fn_name, "arguments": args})
                except json.JSONDecodeError:
                    pass

    if results:
        return results

    for match in re.finditer(r"<tool_call>\s*(.*?)\s*</tool_call>", text, re.DOTALL):
        body = match.group(1).strip()

        # --- Format 1: JSON (Hermes) ---
        if body.startswith("{"):
            try:
                tc_data = json.loads(body)
                if "name" in tc_data:
                    results.append({
                        "name": tc_data["name"],
                        "arguments": tc_data.get("arguments", {}),
                    })
                    continue
            except json.JSONDecodeError:
                pass

        # --- Format 3: Qwen3.5-Coder  <function=name><parameter=k>v</parameter></function> ---
        qwen_match = re.search(r"<function=(\S+?)>(.*?)</function>", body, re.DOTALL)
        if qwen_match:
            fn_name = qwen_match.group(1)
            params_text = qwen_match.group(2)
            args: dict[str, Any] = {}
            for pm in re.finditer(
                r"<parameter=(\S+?)>\s*(.*?)\s*</parameter>", params_text, re.DOTALL
            ):
                key = pm.group(1)
                val = pm.group(2)
                try:
                    args[key] = json.loads(val)
                except (json.JSONDecodeError, ValueError):
                    args[key] = val
            results.append({"name": fn_name, "arguments": args})
            continue

        # --- Format 2: GLM-4.7  fn_name<arg_key>k</arg_key><arg_value>v</arg_value> ---
        glm_args = re.findall(
            r"<arg_key>\s*(.*?)\s*</arg_key>\s*<arg_value>\s*(.*?)\s*</arg_value>",
            body,
            re.DOTALL,
        )
        if glm_args:
            fn_name = re.sub(r"<arg_key>.*", "", body, flags=re.DOTALL).strip()
            args = {}
            for key, val in glm_args:
                try:
                    args[key] = json.loads(val)
                except (json.JSONDecodeError, ValueError):
                    args[key] = val
            if fn_name:
                results.append({"name": fn_name, "arguments": args})
            continue

        # --- Format 4: Generic fallback ---
        # Try to extract function name and JSON arguments from any format
        # e.g. "sql\n{\"query\": \"SELECT ...\"}" or "name: sql, arguments: {...}"
        generic = re.match(r"(\w+)\s*[\n({]", body)
        if generic:
            fn_name = generic.group(1)
            # Try to find JSON anywhere in the body (non-greedy to handle parallel calls)
            json_match = re.search(r"\{.*?\}", body, re.DOTALL)
            if json_match:
                try:
                    args = json.loads(json_match.group())
                    results.append({"name": fn_name, "arguments": args})
                    continue
                except json.JSONDecodeError:
                    pass

    return results


def _yield_missing_tool_results(messages: list) -> list:
    """Insert synthetic error tool_results for any orphaned tool_use blocks."""
    last_assistant = next(
        (m for m in reversed(messages) if m["role"] == "assistant"), None
    )
    if not last_assistant:
        return messages
    # Collect all tool_call ids that already have a corresponding tool result
    existing_result_ids: set = set()
    for m in messages:
        if m["role"] == "tool":
            tc_id = m.get("tool_call_id")
            if tc_id:
                existing_result_ids.add(tc_id)
    # Find tool_calls in the last assistant message that lack results
    missing = []
    for tc in last_assistant.get("tool_calls", []):
        tc_id = tc.get("id", "")
        if tc_id and tc_id not in existing_result_ids:
            missing.append(tc_id)
    if not missing:
        return messages
    for id_ in missing:
        messages.append({
            "role": "tool",
            "tool_call_id": id_,
            "content": json.dumps({"success": False, "error": "Tool execution was interrupted."}),
        })
    logger.warning("Injected %d synthetic tool results for orphaned tool_use blocks", len(missing))
    return messages


# ======================================================================
# Shared helpers
# ======================================================================

def _loop_meta(loop: str, agent, chat_id: str) -> dict:
    """Return event metadata dict based on loop type."""
    if loop == "chat":
        return {"chat_id": chat_id}
    if loop == "quick":
        return {"task": "quick_analysis"}
    return {"cycle": agent.cycle_count}


async def _llm_call(
    agent,
    messages: list[dict],
    tools: list[dict],
    *,
    loop: str,
    chat_id: str = "",
    max_tokens: int | None = None,
    temperature: float = 0.7,
    prev_tool_results: list[dict] | None = None,
    timeout: float | None = None,
) -> tuple[str, list[dict], str | None]:
    """Call the LLM and return (text, tool_calls).

    Handles streaming, event emission, XML tool-call fallback, and token
    tracking.  Raises on LLM error.

    Args:
        timeout: Per-call timeout in seconds.  When set, the entire LLM
            call (including provider-level retries) is wrapped in
            ``asyncio.wait_for`` so that a hanging or retrying provider
            cannot block the caller indefinitely.
    """
    response_content: list[str] = []
    tool_calls: list[dict] = []
    thought_signature = None

    set_llm_log_context(
        user_id=agent.user_id,
        loop=loop,
        chat_id=chat_id,
        tool_results=prev_tool_results or [],
    )

    async def _consume(llm) -> None:
        nonlocal thought_signature
        async for chunk in llm.complete(
            messages=messages,
            tools=tools,
            stream=True,
            max_tokens=max_tokens or settings.AGENT_MAX_TOKENS or None,
            temperature=temperature,
        ):
            ctype = chunk["type"]
            if ctype == "content":
                response_content.append(chunk["content"])
                await agent._emit({
                    "type": "chat_content" if loop == "chat" else "content",
                    "content": chunk["content"],
                    **_loop_meta(loop, agent, chat_id),
                })
            elif ctype == "agent_thinking":
                await agent._emit({
                    "type": "chat_thinking" if loop == "chat" else "agent_thinking",
                    "content": chunk.get("content", ""),
                    **_loop_meta(loop, agent, chat_id),
                })
            elif ctype == "thought_signature":
                thought_signature = chunk["signature"]
            elif ctype == "tool_call":
                tool_calls.append(chunk)
            elif ctype == "token_usage":
                prompt = chunk.get("prompt_tokens") or 0
                completion = chunk.get("completion_tokens") or chunk.get("response_tokens") or 0
                thoughts = chunk.get("thoughts_tokens") or 0
                cache_read = chunk.get("cache_read_tokens") or 0
                cache_creation = chunk.get("cache_creation_tokens") or 0
                agent.cumulative_tokens["prompt_tokens"] += prompt
                agent.cumulative_tokens["completion_tokens"] += completion
                agent.cumulative_tokens["thoughts_tokens"] += thoughts
                agent.cumulative_tokens.setdefault("cache_read_tokens", 0)
                agent.cumulative_tokens.setdefault("cache_creation_tokens", 0)
                agent.cumulative_tokens["cache_read_tokens"] += cache_read
                agent.cumulative_tokens["cache_creation_tokens"] += cache_creation
                await agent._emit({
                    "type": "token_usage",
                    "prompt_tokens": chunk.get("prompt_tokens"),
                    "completion_tokens": chunk.get("completion_tokens"),
                    "thoughts_tokens": chunk.get("thoughts_tokens"),
                    "response_tokens": chunk.get("response_tokens"),
                    "cache_read_tokens": chunk.get("cache_read_tokens"),
                    "cache_creation_tokens": chunk.get("cache_creation_tokens"),
                    **_loop_meta(loop, agent, chat_id),
                })
            elif ctype == "error":
                await agent._emit({"type": "error", "error": chunk["error"], **_loop_meta(loop, agent, chat_id)})
                raise RuntimeError(chunk["error"])

    async def _run_with_fallback() -> None:
        """Run the consume loop against the primary provider; if the primary
        exhausts its capacity-error retry budget (FallbackTriggered) and a
        fallback provider is configured, swap to the fallback for one retry.
        Any partial state from the failed primary attempt is discarded."""
        try:
            await _consume(agent.llm)
            return
        except FallbackTriggered:
            if not (settings.FALLBACK_LLM_PROVIDER and settings.FALLBACK_LLM_MODEL):
                raise
        # Primary gave up on capacity errors — discard partial output and
        # retry once against the configured fallback provider.
        logger.warning(
            "LLM fallback: primary %s/%s exhausted capacity retries — "
            "switching to %s/%s for this call",
            settings.DEFAULT_LLM_PROVIDER, settings.DEFAULT_MODEL,
            settings.FALLBACK_LLM_PROVIDER, settings.FALLBACK_LLM_MODEL,
        )
        response_content.clear()
        tool_calls.clear()
        from .llm_providers import create_provider
        fallback_llm = create_provider(
            settings.FALLBACK_LLM_PROVIDER,
            settings.FALLBACK_LLM_MODEL,
        )
        await _consume(fallback_llm)

    if timeout is not None:
        await asyncio.wait_for(_run_with_fallback(), timeout=timeout)
    else:
        await _run_with_fallback()

    text = "".join(response_content)

    # XML tool-call fallback (vLLM / GLM / Qwen / Gemma)
    valid = [tc for tc in tool_calls if tc.get("name")]
    if not valid and ("<tool_call>" in text or "<|tool_call>" in text):
        valid = _parse_xml_tool_calls(text)

    # Ensure every tool call has an id (generate if provider didn't provide one)
    for tc in valid:
        if not tc.get("id"):
            tc["id"] = f"call_{uuid.uuid4().hex[:24]}"

    return text, valid, thought_signature


def _parse_arguments(arguments: Any) -> dict:
    """Ensure tool arguments are a dict."""
    if isinstance(arguments, str):
        try:
            return json.loads(arguments)
        except json.JSONDecodeError:
            return {}
    return arguments if isinstance(arguments, dict) else {}


def _build_assistant_msg(text: str, tool_calls: list[dict], signature: str | None = None) -> dict:
    """Build an assistant message with tool_calls in OpenAI format.

    This is the canonical format understood by OpenAI-compatible APIs
    (OpenAI, ZhipuAI, Groq, DeepSeek, etc.) and converted by other
    provider adapters (Anthropic, Gemini, Bedrock).
    """
    msg: dict[str, Any] = {"role": "assistant", "content": text or None}
    if signature:
        msg["signature"] = signature
    if tool_calls:
        msg["tool_calls"] = [
            {
                "id": tc.get("id", ""),
                "type": "function",
                "function": {
                    "name": tc["name"],
                    "arguments": (
                        json.dumps(tc["arguments"], ensure_ascii=False)
                        if isinstance(tc["arguments"], dict)
                        else tc.get("arguments", "{}")
                    ),
                },
            }
            for tc in tool_calls
        ]
    return msg


def _build_tool_result_msg(tool_call_id: str, result: dict, tool_name: str = "") -> dict:
    """Build a role: 'tool' message for a single tool result.

    The tool_call_id links this result to the corresponding tool_call
    in the preceding assistant message.  For Gemini, we also stash the
    tool name in ``_tool_name`` since functionResponse requires it.
    """
    content = json.dumps(result, ensure_ascii=False, default=str)
    msg: dict[str, Any] = {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": content,
    }
    if tool_name:
        msg["_tool_name"] = tool_name  # used by Gemini's functionResponse
    return msg


# ======================================================================
# Sliding window context compression
# ======================================================================

def _find_turn_groups(
    messages: list[dict], start_idx: int,
) -> list[list[dict]]:
    """Group messages after *start_idx* into turn groups.

    A turn group = one assistant message + its subsequent tool result messages.
    Returns a list of groups, each group being a list of messages.
    """
    groups: list[list[dict]] = []
    current: list[dict] = []
    for msg in messages[start_idx:]:
        if msg["role"] == "assistant":
            if current:
                groups.append(current)
            current = [msg]
        elif msg["role"] == "tool" and current:
            current.append(msg)
        else:
            # Unexpected role — attach to current group if one exists
            if current:
                current.append(msg)
    if current:
        groups.append(current)
    return groups


def _extract_turn_digest(group: list[dict], turn_num: int) -> str:
    """Build a compact, structured digest of one turn group for LLM summarization.

    Extracts tool names, SQL queries, key result values/output so the
    summarizer LLM has all the numbers it needs.
    """
    assistant_msg = group[0]
    tool_msgs = group[1:]

    parts: list[str] = []
    tool_calls = assistant_msg.get("tool_calls", [])
    for tc in tool_calls:
        fn = tc.get("function", {})
        name = fn.get("name", "?")
        raw_args = fn.get("arguments", "{}")
        if isinstance(raw_args, str):
            try:
                args = json.loads(raw_args)
            except json.JSONDecodeError:
                args = {}
        else:
            args = raw_args

        if name == "sql":
            parts.append(f"sql({args.get('query', '')[:120]})")
        elif name == "code":
            first_line = args.get("code", "").strip().split("\n")[0][:80]
            parts.append(f"code({first_line})")
        else:
            parts.append(f"{name}()")

    # Extract result digests
    result_parts: list[str] = []
    for tm in tool_msgs:
        try:
            result = json.loads(tm.get("content", "{}"))
            if result.get("success"):
                if "markdown" in result:
                    md_lines = result["markdown"].strip().split("\n")
                    # Keep header + first 5 data rows for small results
                    keep = md_lines[:7] if len(md_lines) <= 9 else md_lines[:5]
                    rc = result.get("row_count", "?")
                    table = "\n".join(keep)
                    if len(md_lines) > 9:
                        table += f"\n... ({rc} rows total)"
                    result_parts.append(f"{rc} rows:\n{table}")
                elif "output" in result:
                    result_parts.append(f"output: {str(result['output'])[:200]}")
                else:
                    result_parts.append("ok")
            else:
                result_parts.append(f"ERROR: {result.get('error', '?')[:80]}")
        except (json.JSONDecodeError, TypeError):
            pass

    calls_str = ", ".join(parts) if parts else "(text only)"
    results_str = " | ".join(result_parts) if result_parts else ""

    line = f"Turn {turn_num}: {calls_str}"
    if results_str:
        line += f"\n  → {results_str}"
    return line


async def _summarize_turns_with_llm(
    turn_groups: list[list[dict]],
    start_turn: int,
    llm_provider,
) -> str:
    """Summarize compressed turn groups using an LLM call.

    Falls back to structural digest on failure.
    """
    # Build structured input for the summarizer
    digests = [
        _extract_turn_digest(g, start_turn + i)
        for i, g in enumerate(turn_groups)
    ]
    digest_text = "\n".join(digests)

    end_turn = start_turn + len(turn_groups) - 1
    label = f"Steps {start_turn}-{end_turn}"

    # Structural fallback (used if LLM fails)
    fallback = f"{label}: " + " | ".join(
        _extract_turn_digest(g, start_turn + i).split("\n")[0]
        for i, g in enumerate(turn_groups)
    )
    if len(fallback) > 500:
        fallback = fallback[:497] + "..."

    try:
        prompt_msgs = [{
            "role": "user",
            "content": (
                "Summarize these analysis steps in 1-2 sentences. "
                "Keep ALL specific numbers and key findings.\n\n"
                f"{digest_text}"
            ),
        }]
        set_llm_log_context(loop="context_compression")
        parts: list[str] = []
        async for chunk in llm_provider.complete(
            messages=prompt_msgs,
            tools=None,
            stream=True,
            max_tokens=150,
            temperature=0.0,
        ):
            if chunk.get("type") == "content":
                parts.append(chunk["content"])

        summary = "".join(parts).strip()
        if summary:
            return f"{label}: {summary}"
    except Exception as exc:
        logger.debug("LLM summarization failed, using structural fallback: %s", exc)

    return fallback


def _extract_key_findings(turn_groups: list[list[dict]]) -> str:
    """Extract key findings from turn groups about to be compressed.

    Preserves important numbers, trends, and conclusions that would
    otherwise be lost when the turns are summarized. This is a fast
    structural extraction — no LLM call needed.
    """
    _FINDING_KEYWORDS = (
        "average", "mean", "total", "highest", "lowest",
        "increase", "decrease", "normal", "abnormal",
        "concern", "recommend", "pattern", "trend",
        "bpm", "steps", "hours", "minutes", "mg/dl",
        "percent", "%", "score", "index",
    )
    findings: list[str] = []

    for group in turn_groups:
        for msg in group:
            if msg.get("role") == "tool":
                try:
                    content = msg.get("content", "")
                    if isinstance(content, str):
                        result = json.loads(content)
                    else:
                        result = content
                    if isinstance(result, dict) and result.get("success"):
                        # Extract SQL row counts and key data
                        if "row_count" in result and "columns" in result:
                            cols = result.get("columns", [])
                            rc = result.get("row_count", 0)
                            findings.append(f"SQL: {rc} rows, cols={cols[:5]}")
                        # Extract code output (short outputs only)
                        elif "output" in result:
                            out = str(result["output"])
                            if out and len(out) < 300:
                                findings.append(f"Code: {out[:200]}")
                except (json.JSONDecodeError, TypeError):
                    pass

            elif msg.get("role") == "assistant":
                content = str(msg.get("content", ""))
                for line in content.split("\n"):
                    line_lower = line.lower()
                    if any(kw in line_lower for kw in _FINDING_KEYWORDS):
                        stripped = line.strip()[:150]
                        if stripped:
                            findings.append(f"Finding: {stripped}")

    # Deduplicate and limit
    unique = list(dict.fromkeys(findings))[:10]
    return "\n".join(unique)


async def _compress_context_if_needed(
    messages: list[dict],
    preamble_size: int,
    window_size: int,
    original_user_content: str,
    llm_provider,
    accumulated_summary: str,
) -> tuple[list[dict], str]:
    """Compress overflow turns if batch threshold is reached.

    Triggers when turn groups exceed ``window_size + batch_size``.
    Compresses the oldest ``batch_size`` groups into an LLM summary.
    Key findings are extracted BEFORE compression to preserve critical data.

    Returns ``(new_messages, updated_accumulated_summary)``.
    """
    batch_size = max(1, window_size // 4)
    groups = _find_turn_groups(messages, preamble_size)

    if len(groups) <= window_size + batch_size:
        return messages, accumulated_summary  # Nothing to compress

    # Take the oldest batch_size groups
    to_compress = groups[:batch_size]
    to_keep = groups[batch_size:]

    # Extract key findings BEFORE compression (preserves critical numbers)
    key_findings = _extract_key_findings(to_compress)

    # Figure out the turn number for labeling
    prev_batches = accumulated_summary.count("Steps ")
    start_turn = prev_batches * batch_size + 1

    summary_line = await _summarize_turns_with_llm(
        to_compress, start_turn, llm_provider,
    )

    # Append key findings to the summary
    if key_findings:
        summary_line += f"\nKey findings: {key_findings}"

    new_summary = (
        f"{accumulated_summary}\n{summary_line}"
        if accumulated_summary
        else summary_line
    )

    logger.info(
        "Context window compressed: %d turn groups -> %d kept, %d summarized",
        len(groups), len(to_keep), batch_size,
    )

    # Rebuild messages
    new_messages = list(messages[:preamble_size])

    # Inject accumulated summary into the user message (last preamble element)
    user_idx = preamble_size - 1
    new_messages[user_idx] = {
        **new_messages[user_idx],
        "content": (
            original_user_content
            + "\n\n--- Previous analysis context (auto-summarized) ---\n"
            + new_summary
            + "\n---"
        ),
    }

    # Flatten kept turn groups back into messages
    for group in to_keep:
        new_messages.extend(group)

    return new_messages, new_summary


class AgentLoopsMixin:
    """Analysis and chat loop implementations."""

    # ==================================================================
    # Analysis loop
    # ==================================================================

    async def _run_one_shot_analysis(self, goal: str | None) -> None:
        """Execute a single autonomous analysis task (cron / trigger driven).

        Architecture: thin wrapper around the shared ``_run_sub_analysis``
        engine.  No standalone LLM loop, no LLM-driven push_report — the
        wrapper:

          1. Runs ``_run_sub_analysis(goal, source='analysis')`` for the
             read-only data work.
          2. Programmatically calls ``push_report`` with the findings,
             since cron-triggered analyses always publish their output.

        The autonomous loop has **no write powers** — sub_analysis can
        only read.  Any persistence (memory CRUD, experience updates,
        page changes) must go through the chat path:
        user → orchestrator → ``manage`` → ``sub_manage``.  This keeps
        every state mutation user-authorised and auditable.
        """
        self.cycle_count += 1
        self.last_analysis_complete_time = ts_now()
        self.pushed_report_in_cycle = False
        self._set_state("thinking", loop="analysis")

        # Resolve current simulation timestamp from latest data — used by
        # push_report metadata so reports record the data window they're
        # describing rather than wall-clock time.
        try:
            latest_data = await asyncio.to_thread(
                self.data_store.query,
                "SELECT MAX(timestamp) as ts FROM samples",
            )
            current_sim_time = None
            if not latest_data.empty and "ts" in latest_data.columns and latest_data.iloc[0]["ts"]:
                current_sim_time = latest_data.iloc[0]["ts"]
            self.current_simulation_timestamp = current_sim_time
        except Exception:
            current_sim_time = None

        await self._emit({
            "type": "cycle_start",
            "cycle": self.cycle_count,
            "timestamp": ts_now(),
            "simulation_time": current_sim_time,
            "goal": goal,
        })

        # --- Stage 1: delegate data work + report publishing to sub_analysis ---
        # The sub_analysis engine gets ``push_report`` as an extra tool so
        # the LLM independently authors ``content`` (full report) and
        # ``im_digest`` (short IM message).  If the LLM calls push_report,
        # ``sub_result["report_pushed"]`` is True and we skip the
        # programmatic fallback in Stage 2.
        sub_goal = goal or (
            "Run an open-ended health analysis. Query the data, find "
            "something meaningful to report, and publish the report."
        )
        try:
            sub_result = await self._run_sub_analysis(
                sub_goal,
                chat_id=None,
                source="analysis",
                max_turns=self.max_turns,
                extra_tools={"push_report"},
            )
        except Exception as exc:
            logger.error("Autonomous analysis sub_analysis error: %s", exc, exc_info=True)
            sub_result = {
                "success": False,
                "findings": f"Analysis error: {exc}",
                "evidence": [],
                "charts": [],
            }

        findings = sub_result.get("findings", "") or ""
        evidence = _filter_data_evidence(sub_result.get("evidence", []))

        # --- Stage 2: handle post-analysis bookkeeping ---
        # If the LLM called push_report itself (happy path), we only need
        # to update internal state.  Otherwise fall back to a programmatic
        # push so every autonomous cycle produces a report.
        if sub_result.get("report_pushed"):
            # LLM authored the report — just update internal state
            self.pushed_report_in_cycle = True
            self.last_sleep_time = time.time()
            report_id = sub_result.get("report_id")
            await self._emit({
                "type": "report_pushed",
                "cycle": self.cycle_count,
                "report_id": report_id,
                "timestamp": ts_now(),
            })
        else:
            # Fallback: LLM didn't call push_report (weak model, error,
            # or hit max_turns).  Programmatically push with findings.
            logger.info("LLM did not call push_report — using programmatic fallback")
            title = (goal[:80] if goal else f"Analysis Cycle {self.cycle_count}")
            clean_findings = (
                _strip_raw_tool_calls(findings) if _contains_raw_tool_call(findings)
                else findings.strip()
            )
            if not clean_findings:
                clean_findings = (
                    f"Analysis completed but no detailed findings were generated.\n\n"
                    f"Goal: {goal or 'general analysis'}"
                )

            push_args = {
                "title": title,
                "content": clean_findings,
                "im_digest": clean_findings,
                "time_range_start": current_sim_time or ts_now(),
                "time_range_end": current_sim_time or ts_now(),
                "alert_level": "info",
            }
            try:
                push_result = await asyncio.wait_for(
                    self._execute_tool(
                        "push_report", push_args, evidence_trail=evidence,
                    ),
                    timeout=15.0,
                )
                if push_result.get("success"):
                    self.pushed_report_in_cycle = True
                    self.last_sleep_time = time.time()
                    await self._emit({
                        "type": "report_pushed",
                        "cycle": self.cycle_count,
                        "report_id": push_result.get("report_id"),
                        "timestamp": ts_now(),
                    })
            except Exception as exc:
                logger.warning("Autonomous push_report fallback failed: %s", exc)

        # Share the report with chat histories so the orchestrator can
        # reference it in subsequent conversation.
        if self.pushed_report_in_cycle and findings:
            bot_msg = {"role": "assistant", "content": findings}
            reply_tool = self.tool_registry.get_tool("reply_user")
            gw_registry = getattr(reply_tool, "_gateway_registry", None) if reply_tool else None
            if gw_registry is not None:
                for gw in gw_registry.all():
                    default_chat_id = getattr(gw, "default_chat_id", None)
                    if not default_chat_id:
                        continue
                    key = f"{gw.channel.value}:{default_chat_id}"
                    if key not in self._chat_histories:
                        self._chat_histories[key] = []
            for key, hist in self._chat_histories.items():
                hist.append(bot_msg)
                if len(hist) > self._max_chat_history:
                    self._chat_histories[key] = hist[-self._max_chat_history:]

        await self._emit({
            "type": "cycle_end",
            "cycle": self.cycle_count,
            "timestamp": ts_now(),
        })

        self.cycle_messages = []
        self.pushed_report_in_cycle = False
        self._set_state("idle", loop="analysis")
        self._save_state()

    # ==================================================================
    # Quick analysis — for iOS cat feature
    # ==================================================================

    async def run_quick_analysis(self) -> dict[str, str]:
        """Rapid health status analysis for the iOS cat avatar.

        Architecture: thin wrapper around the shared ``_run_sub_analysis``
        engine.  No standalone LLM loop, no special prompt mode — quick
        is just sub_analysis with a tighter turn budget, an sql-only
        tool filter, and a goal that asks for a JSON ``{state, message}``
        contract.

        Steps:
          1. Resolve the 1-hour analysis window from latest data.
          2. Render ``prompts/quick_goal.md`` with the window hint.
          3. Run ``_run_sub_analysis(goal, max_turns=8, source='quick',
             allowed_tools={'sql'})``.
          4. Parse the JSON contract from the resulting findings text.
          5. Programmatically push a record-keeping report (no LLM call).
          6. Return ``{state, message}`` to the iOS endpoint.
        """
        self._set_state("quick_analysis", loop="analysis")
        self.pushed_report_in_cycle = False
        await self._emit({"type": "quick_analysis_start", "timestamp": ts_now()})
        try:
            return await self._run_quick_analysis_inner()
        except (asyncio.CancelledError, Exception) as exc:
            logger.warning("Quick analysis interrupted: %s", type(exc).__name__)
            return {"state": "neutral", "message": "Analysis interrupted. Try again later."}
        finally:
            self._set_state("idle", loop="analysis")
            await self._emit({
                "type": "quick_analysis_complete",
                "state": "neutral",
                "timestamp": ts_now(),
            })

    async def _run_quick_analysis_inner(self) -> dict[str, str]:
        """Inner implementation — see ``run_quick_analysis`` docstring.

        The goal template (``quick_goal.md``) is fully static — no
        ``str.format`` substitution.  The LLM discovers the latest
        timestamp itself with ``SELECT MAX(timestamp) FROM samples`` and
        derives its own 1-hour window from it.  This keeps the prompt
        in the cacheable prefix and removes the wrapper's pre-fetch.
        """
        from .prompt_loader import load_prompt

        goal = await asyncio.to_thread(load_prompt, "quick_goal.md")

        # --- Run sub_analysis (sql + push_report, 8 turns max) ---
        # The LLM calls push_report directly with content, im_digest,
        # and metadata containing {state, message} for the cat avatar.
        # The wrapper reads state/message from report_args.metadata —
        # fully structured, no text parsing needed.
        try:
            sub_result = await asyncio.wait_for(
                self._run_sub_analysis(
                    goal,
                    chat_id=None,
                    source="quick",
                    max_turns=8,
                    allowed_tools={"sql", "push_report"},
                    extra_tools={"push_report"},
                ),
                timeout=30.0,
            )
        except asyncio.TimeoutError:
            return {
                "state": "neutral",
                "message": "Analysis took too long. Try again later.",
            }
        except Exception as exc:
            logger.error("Quick analysis sub_analysis error: %s", exc)
            return {"state": "neutral", "message": "Analysis error. Try again later."}

        # --- Extract cat state from report_args.metadata ---
        result_state = "relaxed"
        result_message = "Unable to interpret current health state."

        if sub_result.get("report_pushed"):
            # LLM called push_report — read state/message from metadata
            report_args = sub_result.get("report_args", {})
            meta = report_args.get("metadata")
            if isinstance(meta, dict):
                result_state = meta.get("state", "relaxed") or "relaxed"
                result_message = meta.get("message", result_message) or result_message

            self.pushed_report_in_cycle = True
            await self._emit({
                "type": "report_pushed",
                "task": "quick_analysis",
                "report_id": sub_result.get("report_id"),
                "timestamp": ts_now(),
            })
        else:
            # Fallback: LLM didn't call push_report — push programmatically
            findings = sub_result.get("findings", "") or ""
            evidence = _filter_data_evidence(sub_result.get("evidence", []))
            now_iso = ts_now()
            try:
                push_args = {
                    "title": "Quick Health Check",
                    "content": findings or result_message,
                    "im_digest": (findings[:200] if findings else result_message),
                    "time_range_start": now_iso,
                    "time_range_end": now_iso,
                    "alert_level": "info",
                    "source": "quick_analysis",
                }
                push_result = await asyncio.wait_for(
                    self._execute_tool("push_report", push_args, evidence_trail=evidence),
                    timeout=15.0,
                )
                if push_result.get("success"):
                    self.pushed_report_in_cycle = True
                    await self._emit({
                        "type": "report_pushed",
                        "task": "quick_analysis",
                        "report_id": push_result.get("report_id"),
                        "timestamp": ts_now(),
                    })
            except Exception as e:
                logger.warning("Quick analysis push_report fallback failed: %s", e)

        return {"state": result_state, "message": result_message}

    # ==================================================================
    # Chat sub-agent — focused data analysis for chat questions
    # ==================================================================

    async def _run_sub_analysis(
        self,
        goal: str,
        *,
        chat_id: str | None = None,
        source: str = "chat",
        max_turns: int = 15,
        allowed_tools: set | None = None,
        extra_tools: set | None = None,
    ) -> dict:
        """Run a focused data analysis as a sub-agent and return findings.

        This is the **single read-only data engine** used by every entry
        point that needs to query health data:

        - chat orchestrator's ``analyze(goal=...)`` tool — ``source="chat"``
        - cron / trigger autonomous analysis — ``source="analysis"``
        - iOS quick cat-state check — ``source="quick"``

        The sub-agent has ``sql`` + ``code`` + ``read_skill`` and stops
        when the LLM outputs text without tool calls (= findings).  It
        never writes anything — all persistence flows through the chat
        path's ``manage`` → ``sub_manage`` route.

        Args:
            goal:          user-message goal string the LLM acts on.
            chat_id:       Gateway chat id for typing indicator + event
                           tagging.  ``None`` for non-chat callers.
            source:        label used in emit events (``chat`` / ``analysis``
                           / ``quick``) so the monitor UI can route them.
            max_turns:     hard cap on LLM turns (default 15).
            allowed_tools: optional whitelist of tool names; if given,
                           filters the sub-agent's tool definitions
                           (e.g. ``{"sql"}`` for the latency-sensitive
                           iOS quick path that wants to skip ``code``).
            extra_tools:   additional tool names to expose beyond the
                           default ``sql/code/read_skill`` set.  The
                           autonomous path passes ``{"push_report"}`` so
                           the LLM can publish the report itself with
                           distinct ``content`` and ``im_digest``.

        Returns ``{"success", "findings", "charts", "evidence"}``.
        """
        # Send a single "typing..." indicator at the start. Only Telegram
        # currently supports this — other channels silently skip.
        if chat_id is not None:
            try:
                reply_tool = self.tool_registry.get_tool("reply_user")
                registry = getattr(reply_tool, "_gateway_registry", None) if reply_tool else None
                if registry is not None:
                    from ..messaging.base import MessageChannel as _MC
                    tg_gw = registry.get(_MC.TELEGRAM)
                    sender = getattr(tg_gw, "sender", None) if tg_gw is not None else None
                    if sender is not None and hasattr(sender, "send_chat_action"):
                        await sender.send_chat_action(chat_id, "typing")
            except Exception:
                pass  # best-effort

        # Refresh code tool's df so the sub-agent sees the latest health data
        _code_tool = self.tool_registry.get_tool("code")
        if _code_tool is not None and hasattr(_code_tool, "refresh_df_async"):
            await _code_tool.refresh_df_async()

        # Single source of truth for prompt assembly: agent_prompts.py.
        system_prompt = await asyncio.to_thread(self.build_sub_analysis_prompt)

        messages: list[dict] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": goal},
        ]

        sub_tools = self._get_sub_analysis_tool_definitions(extra_tools=extra_tools)
        if allowed_tools is not None:
            sub_tools = [
                t for t in sub_tools
                if (t.get("function", {}) if isinstance(t, dict) else {}).get("name") in allowed_tools
            ]
        # Build the set of tool names the loop will actually execute
        _executable_tools = {"sql", "code", "read_skill"}
        if extra_tools:
            _executable_tools |= extra_tools
        all_evidence: list[dict] = []
        charts: list[str] = []
        findings = ""
        prev_tool_results: list[dict] = []
        prev_call_sig = ""
        dup_count = 0

        # Event names are source-tagged so the monitor UI can route per-source.
        evt_call = f"{source}_tool_call"
        evt_result = f"{source}_tool_result"

        def _evt_payload(base: dict) -> dict:
            if chat_id is not None:
                base["chat_id"] = chat_id
            base["source"] = source
            return base

        for _turn in range(1, max_turns + 1):
            try:
                text, tool_calls, sig = await _llm_call(
                    self, messages, sub_tools,
                    loop=source, chat_id=chat_id,
                    prev_tool_results=prev_tool_results,
                )
            except Exception as e:
                agent_error = classify_error(e, "sub_analysis_llm")
                logger.error("Sub-analysis LLM error (%s): %s", agent_error.category.value, e)
                # Recovery: context overflow → emergency truncate and retry once
                if agent_error.category == ErrorCategory.CONTEXT_OVERFLOW:
                    from .context_manager import ContextManager
                    messages, _ = ContextManager().emergency_truncate(messages, 2)
                    try:
                        text, tool_calls, sig = await _llm_call(
                            self, messages, sub_tools,
                            loop=source, chat_id=chat_id,
                            prev_tool_results=prev_tool_results,
                        )
                    except Exception:
                        findings = f"Analysis error: {e}"
                        break
                else:
                    findings = f"Analysis error: {e}"
                    break

            # No tool calls → text IS the findings
            if not tool_calls:
                findings = text
                break

            # Duplicate detection
            call_sig = _hash_tool_calls(tool_calls)
            if call_sig == prev_call_sig:
                dup_count += 1
                if dup_count >= 2:
                    logger.warning("Sub-analysis (%s): 3 identical rounds, breaking", source)
                    findings = text or "Analysis stalled."
                    break
            else:
                dup_count = 0
            prev_call_sig = call_sig

            messages.append(_build_assistant_msg(text, tool_calls, sig))
            tool_results: list[dict] = []

            for tc in tool_calls:
                tool_name = tc.get("name")
                tc_id = tc.get("id", "")
                if tool_name not in _executable_tools:
                    continue
                arguments = _parse_arguments(tc.get("arguments", {}))

                await self._emit(_evt_payload({
                    "type": evt_call, "tool": tool_name,
                    "arguments": arguments,
                }))

                # push_report needs the evidence trail for fact verification
                evidence_kw: dict = {}
                if tool_name == "push_report":
                    evidence_kw["evidence_trail"] = _filter_data_evidence(all_evidence)

                try:
                    result = await asyncio.wait_for(
                        self._execute_tool(tool_name, arguments, **evidence_kw),
                        timeout=60.0,
                    )
                except asyncio.TimeoutError:
                    result = {"success": False, "error": "Tool timed out."}
                await self._emit(_evt_payload({
                    "type": evt_result, "tool": tool_name,
                    "success": result.get("success", False),
                    "result": result,
                }))
                tool_results.append({
                    "id": tc_id, "tool": tool_name,
                    "arguments": arguments, "result": result,
                })

                # Detect chart paths from code calls
                if tool_name == "code" and result.get("success"):
                    code_text = arguments.get("code", "")
                    for m in re.finditer(
                        r"savefig\(['\"](/tmp/[^'\"]+\.png)['\"]", code_text,
                    ):
                        charts.append(m.group(1))

                # push_report success → record it and stop the loop
                if tool_name == "push_report" and result.get("success"):
                    all_evidence.extend(tool_results)
                    return {
                        "success": True,
                        "findings": text or "",
                        "charts": charts,
                        "evidence": _filter_data_evidence(all_evidence),
                        "report_pushed": True,
                        "report_id": result.get("report_id"),
                        "report_args": arguments,
                    }

            all_evidence.extend(tool_results)
            prev_tool_results = tool_results
            for tr in tool_results:
                messages.append(
                    _build_tool_result_msg(tr["id"], tr["result"], tr["tool"])
                )
            findings = text  # keep last text as fallback findings

        if not findings:
            findings = "Analysis completed but no findings were generated."

        return {
            "success": True,
            "findings": findings,
            "charts": charts,
            "evidence": _filter_data_evidence(all_evidence),
        }

    # ==================================================================
    # Chat manage sub-agent — framework CRUD operations for chat
    # ==================================================================

    async def _run_chat_manage(self, goal: str, chat_id: str) -> dict:
        """Run a framework management operation as a sub-agent and return results.

        Called by the chat loop when the model invokes the ``manage`` tool.
        The sub-agent has ``sql`` (memory DB read/write) and ``create_page``.
        It stops when the LLM outputs text without tool calls (= result summary).

        Returns ``{"success", "result", "actions_taken", "evidence"}``.
        """
        # Single source of truth for prompt assembly: agent_prompts.py.
        system_prompt = await asyncio.to_thread(self.build_sub_manage_prompt)

        messages: list[dict] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": goal},
        ]

        sub_tools = self._get_sub_manage_tool_definitions()
        allowed_manage_tools = {
            (t.get("function", {}) if isinstance(t, dict) else {}).get("name")
            for t in sub_tools
        }

        all_evidence: list[dict] = []
        actions_taken: list[str] = []
        result_text = ""
        prev_tool_results: list[dict] = []
        prev_call_sig = ""
        dup_count = 0
        max_sub_turns = 8

        for _turn in range(1, max_sub_turns + 1):
            try:
                text, tool_calls, sig = await _llm_call(
                    self, messages, sub_tools,
                    loop="chat", chat_id=chat_id,
                    prev_tool_results=prev_tool_results,
                )
            except Exception as e:
                agent_error = classify_error(e, "sub_manage_llm")
                logger.error("Sub-manage LLM error (%s): %s", agent_error.category.value, e)
                if agent_error.category == ErrorCategory.CONTEXT_OVERFLOW:
                    from .context_manager import ContextManager
                    messages, _ = ContextManager().emergency_truncate(messages, 2)
                    try:
                        text, tool_calls, sig = await _llm_call(
                            self, messages, sub_tools,
                            loop="chat", chat_id=chat_id,
                            prev_tool_results=prev_tool_results,
                        )
                    except Exception:
                        result_text = f"Management error: {e}"
                        break
                else:
                    result_text = f"Management error: {e}"
                    break

            # No tool calls → text IS the result
            if not tool_calls:
                result_text = text
                break

            # Duplicate detection
            call_sig = _hash_tool_calls(tool_calls)
            if call_sig == prev_call_sig:
                dup_count += 1
                if dup_count >= 2:
                    logger.warning("Sub-manage: 3 identical rounds, breaking")
                    result_text = text or "Management operation stalled."
                    break
            else:
                dup_count = 0
            prev_call_sig = call_sig

            messages.append(_build_assistant_msg(text, tool_calls, sig))
            tool_results: list[dict] = []

            for tc in tool_calls:
                tool_name = tc.get("name")
                tc_id = tc.get("id", "")
                if tool_name not in allowed_manage_tools:
                    continue
                arguments = _parse_arguments(tc.get("arguments", {}))

                await self._emit({
                    "type": "chat_tool_call", "tool": tool_name,
                    "arguments": arguments, "chat_id": chat_id,
                })
                try:
                    result = await asyncio.wait_for(
                        self._execute_tool(tool_name, arguments),
                        timeout=30.0,
                    )
                except asyncio.TimeoutError:
                    result = {"success": False, "error": "Tool timed out."}
                await self._emit({
                    "type": "chat_tool_result", "tool": tool_name,
                    "success": result.get("success", False),
                    "result": result, "chat_id": chat_id,
                })
                tool_results.append({
                    "id": tc_id, "tool": tool_name,
                    "arguments": arguments, "result": result,
                })
                if result.get("success"):
                    actions_taken.append(f"{tool_name}: {str(arguments)[:120]}")

            all_evidence.extend(tool_results)
            prev_tool_results = tool_results
            for tr in tool_results:
                messages.append(
                    _build_tool_result_msg(tr["id"], tr["result"], tr["tool"])
                )
            result_text = text  # keep last text as fallback

        if not result_text:
            result_text = "Management operation completed."

        return {
            "success": True,
            "result": result_text,
            "actions_taken": actions_taken,
            "evidence": all_evidence,
        }

    # ==================================================================
    # Chat loop — handles a single user message from any gateway
    # ==================================================================

    async def _handle_chat_message(self, envelope: Any) -> None:
        """Process a single inbound user message with multi-turn tool use.

        Design (sub-agent architecture):
        - Model calls analyze(goal) → sub-agent runs data analysis → findings returned.
        - Model calls reply_user → message sent (up to 2 per conversation).
        - Model outputs text without tools → auto-reply if needed, DONE.
        - Typical flow: reply_user(ack) → analyze(goal) → reply_user(findings) → stop.
        - 3 identical tool-call rounds → break, fallback reply.
        - Max turns exhausted → fallback reply.
        """
        sender = envelope.sender_id or "user"
        chat_id = envelope.chat_id or sender
        channel = envelope.channel.value if envelope.channel is not None else "chat"
        # Platform-prefixed history key so the same numeric chat_id on
        # different gateways (Telegram 12345 vs Feishu 12345) can't collide.
        history_key = f"{channel}:{chat_id}"
        self.user_messages_received += 1

        self._set_state("chat_processing", loop="chat")
        await self._emit({
            "type": "user_message",
            "sender": sender,
            "content": envelope.content,
            "chat_id": chat_id,
            "channel": channel,
        })

        system_prompt = await asyncio.to_thread(
            self._get_system_prompt, mode="chat"
        )
        history = self._chat_histories.get(history_key, [])
        ts_str = self._format_message_timestamp(envelope.timestamp)
        user_msg_for_llm: dict = {
            "role": "user",
            "content": f"[{channel.upper()} MESSAGE from {sender} at {ts_str}]: {envelope.content}",
        }
        user_msg_for_history: dict = {
            "role": "user",
            "content": envelope.content,
        }

        chat_messages = [
            {"role": "system", "content": system_prompt},
            *history,
            user_msg_for_llm,
        ]

        chat_tools = self._get_chat_tool_definitions()

        # Inject the current envelope into the reply tool so it can route the
        # outbound message to the correct gateway (Telegram / Feishu / ...).
        _reply_tool = self.tool_registry.get_tool("reply_user")
        if _reply_tool is not None and hasattr(_reply_tool, "_current_envelope"):
            _reply_tool._current_envelope = envelope

        reply_text: str | None = None
        original_user_content = user_msg_for_llm["content"]
        preamble_size = len(chat_messages)  # system + history + user
        prev_chat_tool_results: list[dict] = []
        all_evidence: list[dict] = []
        accumulated_summary = ""
        finished = False
        has_replied = False
        reply_send_count = 0
        prev_call_sig = ""
        dup_count = 0
        self._chat_fabrication_retried = False
        chat_budget = min(self.max_turns, settings.CHAT_MAX_TURNS)

        for turn in range(1, chat_budget + 1):
            self._set_state("chat_thinking", loop="chat")

            try:
                text, tool_calls, sig = await _llm_call(
                    self, chat_messages, chat_tools,
                    loop="chat", chat_id=str(chat_id),
                    prev_tool_results=prev_chat_tool_results,
                )
            except Exception as e:
                logger.error("LLM error (chat): %s", e, exc_info=True)
                _yield_missing_tool_results(chat_messages)
                break

            # --- No tool calls: graceful exit ---
            if not tool_calls:
                clean = _strip_meta_markers(text)
                # Strip any raw tool-call markup that leaked through
                if _contains_raw_tool_call(clean):
                    logger.warning("Chat turn %d: stripping leaked tool-call markup from auto-reply", turn)
                    clean = _strip_raw_tool_calls(clean)
                if not has_replied and clean and clean not in ("(tools)", "(done)", "(no response)"):
                    # LLM wrote text but didn't call reply_user — send it
                    logger.info("Chat turn %d: no tool calls, auto-replying with text", turn)
                    sent = await self._auto_reply(clean, chat_id, all_evidence, user_message=envelope.content, chat_history=history)
                    if sent:
                        has_replied = True
                        reply_text = clean
                        finished = True
                        break
                    # Auto-reply was blocked (e.g. fabricated operation claim).
                    # Give the agent one retry: inject feedback so it can use
                    # actual tools instead of just claiming it did something.
                    if not getattr(self, "_chat_fabrication_retried", False):
                        self._chat_fabrication_retried = True
                        logger.info("Chat turn %d: auto-reply blocked, injecting retry feedback", turn)
                        chat_messages.append(_build_assistant_msg(clean, [], sig))
                        chat_messages.append({
                            "role": "user",
                            "content": (
                                "[System: Your reply was blocked because it claimed to perform an action "
                                "(e.g. setting a schedule, creating a page) without actually calling the "
                                "required tool. Please use your tools (sql, analyze, reply_user, etc.) "
                                "to perform the action first, then reply.]"
                            ),
                        })
                        continue
                # No more retries or conversational text — done
                finished = True
                break

            # --- Add assistant message with proper tool_calls structure ---
            # Strip meta markers (e.g. "[Replied at ...]") the LLM mimics
            chat_messages.append(_build_assistant_msg(_strip_meta_markers(text), tool_calls, sig))

            # Merge multiple reply_user calls in the same batch
            reply_calls = [tc for tc in tool_calls if tc.get("name") == "reply_user"]
            if len(reply_calls) > 1:
                merged_parts: list[str] = []
                for rtc in reply_calls:
                    args = _parse_arguments(rtc.get("arguments", {}))
                    msg = args.get("message", "")
                    if msg:
                        merged_parts.append(msg)
                merged_tc = {"name": "reply_user", "arguments": {"message": "\n\n".join(merged_parts)}}
                tool_calls = [tc for tc in tool_calls if tc.get("name") != "reply_user"] + [merged_tc]

            # Execution order: reply_user first (user sees ack immediately),
            # manage before analyze (framework ops first), analyze last (may block),
            # finish_chat very last.
            _ORDER = {"reply_user": 0, "finish_chat": 99, "manage": 40, "analyze": 50}
            tool_calls.sort(key=lambda tc: _ORDER.get(tc.get("name", ""), 10))

            # --- Duplicate tool call detection ---
            call_sig = _hash_tool_calls(tool_calls)
            if call_sig == prev_call_sig:
                dup_count += 1
                if dup_count >= 2:
                    logger.warning("Chat: 3 identical tool-call rounds, breaking loop")
                    _yield_missing_tool_results(chat_messages)
                    if not has_replied:
                        clean = _strip_meta_markers(text)
                        if clean:
                            sent = await self._auto_reply(clean, chat_id, all_evidence, user_message=envelope.content, chat_history=history)
                            has_replied = sent
                            if sent:
                                reply_text = clean
                    break
            else:
                dup_count = 0
            prev_call_sig = call_sig

            # --- Execute tools ---
            tool_results: list[dict] = []

            for tc in tool_calls:
                tool_name = tc.get("name")
                tc_id = tc.get("id", "")
                arguments = _parse_arguments(tc.get("arguments", {}))

                if tool_name == "analyze":
                    # Delegate data analysis to sub-agent
                    sub_goal = arguments.get("goal", "")
                    self._set_state("chat_analyzing", loop="chat")
                    await self._emit({"type": "chat_tool_call", "tool": "analyze",
                                      "arguments": arguments, "chat_id": chat_id})
                    try:
                        result = await asyncio.wait_for(
                            self._run_sub_analysis(
                                sub_goal,
                                chat_id=str(chat_id),
                                source="chat",
                            ),
                            timeout=180.0,
                        )
                    except asyncio.TimeoutError:
                        result = {"success": False, "findings": "Analysis timed out.", "evidence": []}
                    except Exception as exc:
                        result = {"success": False, "findings": str(exc), "evidence": []}
                    all_evidence.extend(result.get("evidence", []))
                    await self._emit({"type": "chat_tool_result", "tool": "analyze",
                                      "success": result.get("success", False),
                                      "result": {"findings": result.get("findings", "")[:500]},
                                      "chat_id": chat_id})
                    tool_results.append({"id": tc_id, "tool": "analyze", "result": result})
                    prev_chat_tool_results = tool_results
                    continue
                elif tool_name == "manage":
                    sub_goal = arguments.get("goal", "")
                    self._set_state("chat_managing", loop="chat")
                    await self._emit({"type": "chat_tool_call", "tool": "manage",
                                      "arguments": arguments, "chat_id": chat_id})
                    try:
                        result = await asyncio.wait_for(
                            self._run_chat_manage(sub_goal, str(chat_id)),
                            timeout=120.0,
                        )
                    except asyncio.TimeoutError:
                        result = {"success": False, "result": "Management operation timed out.", "actions_taken": [], "evidence": []}
                    except Exception as exc:
                        result = {"success": False, "result": str(exc), "actions_taken": [], "evidence": []}
                    all_evidence.extend(result.get("evidence", []))
                    await self._emit({"type": "chat_tool_result", "tool": "manage",
                                      "success": result.get("success", False),
                                      "result": {"result": result.get("result", "")[:500]},
                                      "chat_id": chat_id})
                    tool_results.append({"id": tc_id, "tool": "manage", "result": result})
                    prev_chat_tool_results = tool_results
                    continue
                elif tool_name == "finish_chat":
                    if not has_replied:
                        tool_results.append({
                            "id": tc_id, "tool": "finish_chat",
                            "result": {
                                "success": False,
                                "error": "Cannot finish: no reply sent yet. Call reply_user first.",
                            },
                        })
                        continue
                    finished = True
                    tool_results.append({
                        "id": tc_id, "tool": "finish_chat",
                        "result": {"success": True, "message": "Chat session finished."},
                    })
                    continue
                elif tool_name == "reply_user":
                    # Empty args fallback: use content text if model put answer there
                    msg = arguments.get("message", "")
                    if not msg and text:
                        arguments["message"] = _strip_meta_markers(text)
                    elif msg:
                        # Strip echoed envelope headers the LLM may have copied
                        arguments["message"] = _strip_meta_markers(msg)
                    # 2-send limit: 1st = interim ack, 2nd = final answer
                    if reply_send_count >= 2:
                        tool_results.append({
                            "id": tc_id, "tool": "reply_user",
                            "result": {"success": True,
                                       "message": "Already sent 2 messages. Wrap up."},
                        })
                        continue
                    reply_send_count += 1
                    arguments["chat_id"] = str(chat_id)

                self._set_state(f"chat_executing:{tool_name}", loop="chat")
                await self._emit({
                    "type": "chat_tool_call",
                    "tool": tool_name,
                    "arguments": arguments,
                    "sender": sender,
                    "chat_id": chat_id,
                })

                try:
                    evidence = _filter_data_evidence(all_evidence + tool_results) if tool_name == "reply_user" else None
                    result = await asyncio.wait_for(
                        self._execute_tool(
                            tool_name, arguments, evidence_trail=evidence,
                            user_message=envelope.content if tool_name in ("reply_user",) else "",
                            chat_history=history if tool_name == "reply_user" else None,
                        ),
                        timeout=60.0,
                    )
                except asyncio.TimeoutError:
                    result = {"success": False, "error": "Tool timed out after 60s."}

                await self._emit({
                    "type": "chat_tool_result",
                    "tool": tool_name,
                    "success": result.get("success", False),
                    "result": result,
                    "chat_id": chat_id,
                    **({"reply_text": arguments.get("message", "")[:200]} if tool_name == "reply_user" else {}),
                })
                tool_results.append({"id": tc_id, "tool": tool_name, "arguments": arguments, "result": result})
                prev_chat_tool_results = tool_results

                # C2 fix: only mark has_replied after verifying tool success
                if tool_name == "reply_user" and result.get("success"):
                    has_replied = True
                    reply_text = arguments.get("message", "")

                if finished:
                    break

            all_evidence.extend(tool_results)

            if finished:
                break

            # --- Add tool results as proper role:"tool" messages ---
            for tr in tool_results:
                chat_messages.append(_build_tool_result_msg(tr["id"], tr["result"], tr["tool"]))

            # Sliding window compression
            try:
                chat_messages, accumulated_summary = await _compress_context_if_needed(
                    chat_messages, preamble_size, self.context_window_size,
                    original_user_content, self.llm, accumulated_summary,
                )
            except Exception as exc:
                logger.warning("Context compression error (chat): %s", exc)

        # --- If we never replied, auto-reply ---
        if not has_replied:
            logger.warning("Chat with %s ended without reply, sending fallback", sender)
            await self._auto_reply(
                "Sorry, I couldn't process your message properly. Please try again!",
                chat_id, all_evidence, user_message=envelope.content,
                chat_history=history,
            )
            reply_text = "(auto-reply: processing failed)"
            finished = True

        self._set_state("chat_complete", loop="chat")
        hist = self._chat_histories.setdefault(history_key, [])
        hist.append(user_msg_for_history)
        if reply_text:
            # Store reply as plain text — no brackets, timestamps, or markers
            # that the LLM could mimic in subsequent turns.
            hist.append({"role": "assistant", "content": reply_text})
        if len(hist) > self._max_chat_history:
            hist = hist[-self._max_chat_history:]
            self._chat_histories[history_key] = hist
        self._save_state()
        logger.info("Chat with %s completed (%d turns, history=%d msgs)", sender, turn, len(hist))

    async def _auto_reply(
        self,
        text: str,
        chat_id: Any,
        tool_results: list[dict] | None = None,
        user_message: str = "",
        chat_history: list[dict] | None = None,
    ) -> bool:
        """Send text as a reply_user on behalf of the LLM.

        When *tool_results* are provided, evidence is attached so the
        "Show Evidence" button appears on the outbound message.

        Returns True if the message was actually sent, False otherwise.
        """
        # Safety net: never send raw tool-call markup to users
        if _contains_raw_tool_call(text):
            text = _strip_raw_tool_calls(text)
        if not text:
            return False
        try:
            reply_tool = self.tool_registry.get_tool("reply_user")
            if reply_tool:
                # Always set evidence (even empty) to clear stale data
                if hasattr(reply_tool, "_current_tool_results"):
                    reply_tool._current_tool_results = list(
                        _filter_data_evidence(tool_results or [])
                    )
                if hasattr(reply_tool, "_current_user_message"):
                    reply_tool._current_user_message = user_message
                if hasattr(reply_tool, "_current_chat_history"):
                    reply_tool._current_chat_history = chat_history or []
                result = await reply_tool.execute(message=text, chat_id=str(chat_id))
                if result.get("success"):
                    await self._emit({
                        "type": "chat_reply",
                        "content": text[:300],
                        "chat_id": str(chat_id),
                        "auto": True,
                    })
                    return True
                else:
                    logger.warning("Auto-reply failed: %s", result.get("error", "")[:120])
                    return False
        except Exception as e:
            logger.error("Auto-reply failed: %s", e)
        return False
