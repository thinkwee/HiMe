"""
Token-aware context compression with circuit breaker.

Replaces the simple turn-count based compression with token estimation.
Includes a circuit breaker (max 3 consecutive failures) and emergency
truncation fallback.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class ContextManager:
    """Token-aware context manager inspired by Claude Code's autoCompact.

    Uses character-based token estimation to decide when to compress,
    rather than counting turn groups.
    """

    BUFFER_TOKENS = 4000          # Reserved for LLM output
    SUMMARY_BUDGET = 2000         # Reserved for summary generation
    MAX_COMPACT_FAILURES = 3      # Circuit breaker threshold

    def __init__(self, context_window: int = 128_000) -> None:
        self.context_window = context_window
        self.compact_failure_count = 0

    def estimate_tokens(
        self,
        messages: list[dict],
        tool_definitions: list[dict] | None = None,
        system_prompt_tokens: int = 0,
    ) -> int:
        """Fast token estimation using UTF-8 byte count.

        UTF-8 bytes / 3 gives a reasonable estimate for mixed content:
        - ASCII chars: 1 byte each, so ~3 chars/token (close to real ~4)
        - CJK chars: 3 bytes each, so ~1 char/token (close to real ~1.5)
        This avoids the old char-count method that underestimated CJK by 50-100%.
        """
        total_bytes = sum(len(str(m.get("content", "")).encode("utf-8")) for m in messages)
        for m in messages:
            for tc in m.get("tool_calls", []):
                total_bytes += len(str(tc).encode("utf-8"))
        tool_bytes = len(str(tool_definitions).encode("utf-8")) if tool_definitions else 0
        return int((total_bytes + tool_bytes) / 3) + system_prompt_tokens

    def should_compact(self, messages: list[dict], tool_definitions: list[dict] | None = None) -> bool:
        """Check if context compression is needed based on estimated tokens."""
        if self.compact_failure_count >= self.MAX_COMPACT_FAILURES:
            return False  # Circuit breaker tripped

        estimated = self.estimate_tokens(messages, tool_definitions)
        threshold = self.context_window - self.BUFFER_TOKENS - self.SUMMARY_BUDGET
        return estimated > threshold

    def record_compact_success(self) -> None:
        """Reset the circuit breaker after a successful compaction."""
        self.compact_failure_count = 0

    def record_compact_failure(self) -> None:
        """Increment the circuit breaker counter."""
        self.compact_failure_count += 1
        logger.warning(
            "Compaction failed (%d/%d)",
            self.compact_failure_count,
            self.MAX_COMPACT_FAILURES,
        )

    @property
    def circuit_breaker_tripped(self) -> bool:
        return self.compact_failure_count >= self.MAX_COMPACT_FAILURES

    def emergency_truncate(
        self,
        messages: list[dict],
        preamble_size: int,
    ) -> tuple[list[dict], str]:
        """Last-resort truncation: drop oldest 25% of body messages.

        Used when normal compaction fails (circuit breaker) or on
        context-overflow errors.  Preserves tool_use/tool_result pairing
        by advancing the cut point past any orphaned tool result messages.
        """
        body = messages[preamble_size:]
        keep_count = max(1, len(body) * 3 // 4)  # Keep 75%
        cut_idx = len(body) - keep_count

        # Advance past any orphaned tool result messages at the cut boundary
        # so we don't start the kept portion with tool results that lack
        # their preceding assistant (tool_use) message.
        while cut_idx < len(body) and body[cut_idx].get("role") == "tool":
            cut_idx += 1

        kept = body[cut_idx:]
        truncated = list(messages[:preamble_size]) + kept
        dropped = len(body) - len(kept)
        logger.warning("Emergency truncation: dropped %d oldest messages", dropped)
        return truncated, f"[Context emergency-truncated: {dropped} messages dropped]"
