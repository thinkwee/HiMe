"""
Tool definition and execution methods for the autonomous health agent.

Extracted from autonomous_agent.py to reduce file size.
These methods are mixed into AutonomousHealthAgent via AgentToolsMixin.
"""
import json
import logging
import re

from .tools.result_manager import truncate_result

logger = logging.getLogger(__name__)


def _salvage_truncated_json(raw: str) -> dict:
    """Best-effort extraction of key-value pairs from truncated JSON.

    When the LLM response is cut off by max_tokens, the tool argument JSON
    is incomplete (e.g. missing closing quotes/braces).  This function:
    1. Extracts all complete "key": "value" pairs
    2. For the last field (likely truncated mid-value), captures whatever
       was written — essential for push_report where `content` is huge and
       almost always the field that gets cut off.

    Returns a dict of salvaged fields (may be empty).
    """
    result: dict = {}
    # Pass 1: extract complete "key": "value" pairs
    for m in re.finditer(
        r'"(\w+)"\s*:\s*"((?:[^"\\]|\\.)*)"\s*[,}]',
        raw,
    ):
        result[m.group(1)] = m.group(2).replace('\\"', '"').replace("\\n", "\n")

    # Pass 2: capture the last (truncated) field — the one that was being
    # written when max_tokens hit.  Pattern: "key": "value_without_closing_quote
    last_match = None
    for m in re.finditer(r'"(\w+)"\s*:\s*"((?:[^"\\]|\\.)*?)$', raw):
        last_match = m
    if last_match:
        key = last_match.group(1)
        if key not in result:  # don't overwrite a complete match
            val = last_match.group(2).replace('\\"', '"').replace("\\n", "\n")
            if val:  # only if we got some content
                result[key] = val

    if result:
        logger.info(
            "Salvaged %d fields from truncated JSON: %s",
            len(result), list(result.keys()),
        )
    return result


class AgentToolsMixin:
    """Tool definition and execution methods."""

    # ------------------------------------------------------------------
    # Parallel tool execution helpers
    # ------------------------------------------------------------------

    def _group_tool_calls_by_concurrency(self, tool_calls: list) -> list:
        """Group tool calls into batches by concurrency safety.

        Consecutive concurrency-safe tools form one parallel group.
        Non-safe tools are singleton groups (executed serially).
        Returns: list of (is_parallel, [tool_call_dicts])
        """
        groups: list = []
        current_safe: list = []

        for tc in tool_calls:
            tool_name = tc.get("name", "")
            tool = self.tool_registry.get_tool(tool_name)
            is_safe = getattr(tool, "is_concurrency_safe", False) if tool else False

            if is_safe:
                current_safe.append(tc)
            else:
                # Flush any pending safe group
                if current_safe:
                    groups.append((True, current_safe))
                    current_safe = []
                groups.append((False, [tc]))

        if current_safe:
            groups.append((True, current_safe))

        return groups

    def _get_tool_definitions(self) -> list:
        return self.tool_registry.get_definitions()

    def _get_chat_tool_definitions(self) -> list:
        """Chat mode: pure dispatcher.  Tools = analyze, manage, reply_user, finish_chat.

        The chat orchestrator has **no write powers and no data tools**.
        - Data analysis is delegated to ``sub_analysis`` via ``analyze``.
        - Every write side-effect (memory CRUD, ``update_md`` of user.md /
          experience.md, ``create_page``) is delegated to ``sub_manage``
          via ``manage``.

        This keeps the orchestrator a thin conversational dispatcher and
        funnels all persistence through one sub-agent that owns the
        relevant prompt context (HimeUI templates, memory schema, etc.).
        Skills are likewise out-of-scope here — they're loaded by
        sub_analysis where execution happens.
        """
        excluded = {
            "push_report", "sql", "code", "read_skill",
            "update_md", "create_page",
        }
        defs = []
        for t in self.tool_registry.get_all_tools():
            defn = t.get_definition()
            name = defn.get("function", {}).get("name")
            if not name:
                logger.error("Tool %s has invalid definition, skipping", type(t).__name__)
                continue
            if name not in excluded:
                defs.append(defn)
        # analyze and manage are handled in the loop (not registered tool classes)
        manage_def = self._load_tool_json("manage")
        if manage_def:
            defs.insert(0, manage_def)
        analyze_def = self._load_tool_json("analyze")
        if analyze_def:
            defs.insert(0, analyze_def)
        return defs

    def _get_sub_analysis_tool_definitions(
        self, extra_tools: set[str] | None = None,
    ) -> list:
        """Sub-analysis mode (chat sub-agent): sql, code, and read_skill.

        The sub-agent owns the skills (analysis playbooks) so the agent
        that actually executes the analysis is the one that decides whether
        to load a playbook.  See ``_run_sub_analysis`` for the matching
        skills block injected into its system prompt.

        Args:
            extra_tools: additional tool names to include (e.g.
                ``{"push_report"}`` for autonomous analysis so the LLM
                can publish the report itself with distinct ``content``
                and ``im_digest`` fields).
        """
        allowed = {"sql", "code", "read_skill"}
        if extra_tools:
            allowed |= extra_tools
        defs = []
        for t in self.tool_registry.get_all_tools():
            defn = t.get_definition()
            name = defn.get("function", {}).get("name")
            if not name:
                logger.error("Tool %s has invalid definition, skipping", type(t).__name__)
                continue
            if name in allowed:
                defs.append(defn)
        return defs

    def _get_sub_manage_tool_definitions(self) -> list:
        """Sub-manage mode (chat write executor): sql (memory), update_md, create_page."""
        allowed = {"sql", "update_md", "create_page"}
        defs = []
        for t in self.tool_registry.get_all_tools():
            defn = t.get_definition()
            name = defn.get("function", {}).get("name")
            if not name:
                logger.error("Tool %s has invalid definition, skipping", type(t).__name__)
                continue
            if name in allowed:
                defs.append(defn)
        return defs

    @staticmethod
    def _load_tool_json(tool_name: str) -> dict:
        """Load a single tool definition from tools.json."""
        from pathlib import Path
        path = Path(__file__).parent / "tools" / "tools.json"
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f).get(tool_name, {})
        except Exception:
            return {}

    def _feed_evidence_to_tool(
        self, tool_name: str, tool_results: list, user_message: str = "",
        chat_history: list | None = None,
    ) -> None:
        """Feed accumulated tool results to user-facing tools for evidence tracking."""
        if tool_name in ("push_report", "reply_user"):
            tool = self.tool_registry.get_tool(tool_name)
            if tool and hasattr(tool, "_current_tool_results"):
                tool._current_tool_results = list(tool_results)
            if tool and hasattr(tool, "_current_user_message"):
                tool._current_user_message = user_message
            if tool and hasattr(tool, "_current_chat_history"):
                tool._current_chat_history = list(chat_history or [])

    async def _execute_tool(
        self, tool_name: str, arguments: dict,
        evidence_trail: list = None, user_message: str = "",
        chat_history: list | None = None,
    ) -> dict:
        tool = self.tool_registry.get_tool(tool_name)
        if not tool:
            hint = ""
            if ":" in tool_name and "sql" in tool_name:
                hint = (
                    " Hint: the tool name is just 'sql'. Put the database prefix "
                    "inside the query parameter, e.g. sql(query='memory:SELECT ...')"
                )
            return {"success": False, "error": f"Unknown tool: {tool_name}.{hint}"}

        # When JSON parsing fails (e.g. response truncated by max_tokens), providers
        # inject {"_raw": "<truncated json string>"}. Try to salvage fields from it
        # rather than dropping everything — push_report's content field can easily
        # exceed max_tokens, causing truncation mid-JSON.
        raw_fallback = arguments.pop("_raw", None)
        if raw_fallback and not arguments:
            arguments = _salvage_truncated_json(raw_fallback)

        # Feed evidence trail to telegram-facing tools before execution
        if evidence_trail is not None:
            self._feed_evidence_to_tool(tool_name, evidence_trail, user_message=user_message, chat_history=chat_history)

        try:
            if tool_name == "push_report":
                if self.pushed_report_in_cycle:
                    return {
                        "success": False,
                        "error": "Already pushed a report in this analysis run. This ends the analysis.",
                    }
                current_sim_time = getattr(self, "current_simulation_timestamp", None)
                if current_sim_time:
                    meta = arguments.get("metadata")
                    if meta is None:
                        meta = {}
                    elif isinstance(meta, str):
                        try:
                            meta = json.loads(meta)
                        except json.JSONDecodeError:
                            meta = {}
                    if isinstance(meta, dict):
                        meta["data_timestamp"] = current_sim_time
                        arguments["metadata"] = meta

            # Use validated __call__ if the tool has an input schema,
            # otherwise fall back to direct execute()
            if tool.input_schema is not None:
                return await tool(**arguments)
            return await tool.execute(**arguments)

        except Exception as e:
            logger.error("Tool execution error (%s): %s", tool_name, e, exc_info=True)
            return {"success": False, "error": str(e), "error_type": type(e).__name__}

    @staticmethod
    def _format_tool_result(tool_name: str, result: dict, arguments: dict = None) -> str:
        """Format a tool result for the LLM context.

        Each result is self-documenting: it includes the original query/code
        so the LLM can understand what each result was answering when it
        reviews older turns during multi-step analysis.

        Large results are auto-truncated to prevent context window overflow.
        """
        # Apply smart truncation before formatting
        result = truncate_result(result, tool_name)
        # Build a compact query label for self-documentation
        query_label = ""
        if arguments:
            if tool_name == "sql" and "query" in arguments:
                q = arguments["query"]
                # Show just the SQL part (strip the db prefix for brevity)
                if ":" in q:
                    _, sql_part = q.split(":", 1)
                    query_label = f" — `{sql_part.strip()[:120]}`"
                else:
                    query_label = f" — `{q[:120]}`"
            elif tool_name == "code" and "code" in arguments:
                first_line = arguments["code"].strip().split("\n")[0][:80]
                query_label = f" — `{first_line}`"

        if tool_name == "sql" and result.get("success") and "markdown" in result:
            md = result["markdown"]
            meta_parts = [f"{result.get('row_count', 0)} rows"]
            if result.get("truncated"):
                meta_parts.append("truncated")
            meta = ", ".join(meta_parts)
            return f"Tool `sql`{query_label} returned ({meta}):\n\n{md}"
        # Default: JSON
        return f"Tool `{tool_name}`{query_label} returned:\n```json\n{json.dumps(result, indent=2, default=str)}\n```"
