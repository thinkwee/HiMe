"""
Smart truncation for large tool results.

Prevents large outputs (DataFrame prints, huge SQL results) from
consuming the entire context window. Each tool type has a tailored
truncation strategy that preserves the most useful information.
"""
from __future__ import annotations

import json

MAX_RESULT_CHARS = 8000  # Truncate results exceeding this total character count


def truncate_result(result: dict, tool_name: str) -> dict:
    """Intelligently truncate a tool result if it exceeds MAX_RESULT_CHARS.

    Preserves key information (column names, row counts, head/tail)
    rather than blindly cutting at a character boundary.
    """
    result_str = json.dumps(result, ensure_ascii=False, default=str)
    if len(result_str) <= MAX_RESULT_CHARS:
        return result

    if tool_name == "code":
        return _truncate_code_result(result)
    elif tool_name == "sql":
        return _truncate_sql_result(result)
    else:
        return _generic_truncate(result, result_str)


def _truncate_code_result(result: dict) -> dict:
    """Code tool: keep head + tail of output, mark omission."""
    output = result.get("output", "")
    if len(output) <= MAX_RESULT_CHARS:
        return result

    third = MAX_RESULT_CHARS // 3
    head = output[:third]
    tail = output[-third:]
    omitted = len(output) - len(head) - len(tail)

    result = dict(result)
    result["output"] = f"{head}\n\n... [{omitted} characters omitted] ...\n\n{tail}"
    result["truncated"] = True
    result["original_length"] = len(output)
    return result


def _truncate_sql_result(result: dict) -> dict:
    """SQL tool: keep first 20 rows and add a note about total count."""
    rows = result.get("rows", [])
    total = len(rows)
    if total <= 20:
        # Might be the markdown that's too long
        md = result.get("markdown", "")
        if len(md) > MAX_RESULT_CHARS:
            lines = md.split("\n")
            keep = lines[:22]  # header + separator + 20 rows
            result = dict(result)
            result["markdown"] = "\n".join(keep) + f"\n... ({total} rows total, truncated for context)"
            result["truncated"] = True
        return result

    result = dict(result)
    result["rows"] = rows[:20]
    result["truncated"] = True
    result["total_rows"] = total
    result["note"] = f"Showing 20 of {total} rows. Use LIMIT or WHERE in SQL for specific ranges."

    # Also truncate the markdown table
    md = result.get("markdown", "")
    if md:
        lines = md.split("\n")
        keep = lines[:22]  # header + separator + 20 rows
        result["markdown"] = "\n".join(keep) + f"\n... ({total} rows total)"

    return result


def _generic_truncate(result: dict, result_str: str) -> dict:
    """Generic fallback: keep first MAX_RESULT_CHARS characters of the JSON."""
    return {
        "success": result.get("success", True),
        "summary": result_str[:MAX_RESULT_CHARS],
        "truncated": True,
        "original_length": len(result_str),
        "note": "Result truncated due to size. Key data preserved above.",
    }
