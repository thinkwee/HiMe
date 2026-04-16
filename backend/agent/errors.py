"""
Structured error classification and recovery for the autonomous health agent.

Provides:
  - ErrorCategory enum for classifying errors
  - AgentError for structured error representation
  - classify_error() for automatic error classification
"""
from __future__ import annotations

import asyncio
from enum import Enum


class ErrorCategory(Enum):
    """Error classification — determines recovery strategy."""
    TRANSIENT = "transient"              # Network, timeout — can retry
    RATE_LIMITED = "rate_limited"        # 429 — back off and retry
    CAPACITY = "capacity"                # 529/503 — service overloaded, may need fallback
    VALIDATION = "validation"            # Invalid input — LLM needs to fix arguments
    PERMISSION = "permission"            # Not authorized — not recoverable
    CONTEXT_OVERFLOW = "context_overflow" # Token limit exceeded — compress and retry
    TOOL_BUG = "tool_bug"               # Tool internal error
    LLM_ERROR = "llm_error"             # LLM-specific error


class AgentError:
    """Structured error with classification and recovery metadata."""

    def __init__(
        self,
        category: ErrorCategory,
        message: str,
        recoverable: bool = True,
        original: Exception | None = None,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        self.category = category
        self.message = message
        self.recoverable = recoverable
        self.original = original
        self.status_code = status_code
        self.retry_after = retry_after

    def to_tool_result(self) -> dict:
        """Convert to a tool result dict for LLM consumption."""
        return {
            "success": False,
            "error": self.message,
            "error_type": self.category.value,
            "recoverable": self.recoverable,
        }

    def __repr__(self) -> str:
        return f"AgentError({self.category.value}: {self.message})"


class FallbackTriggered(Exception):
    """Raised when retry exhaustion triggers a provider fallback."""

    def __init__(self, provider: str, model: str = "") -> None:
        self.provider = provider
        self.model = model
        super().__init__(f"Fallback triggered: {provider}/{model}")


def _extract_status_code(exc: Exception) -> int | None:
    """Try to extract HTTP status code from various exception types."""
    # httpx, aiohttp, requests-style
    for attr in ("status_code", "status", "code"):
        val = getattr(exc, attr, None)
        if isinstance(val, int):
            return val

    # Check nested response object
    resp = getattr(exc, "response", None)
    if resp is not None:
        for attr in ("status_code", "status"):
            val = getattr(resp, attr, None)
            if isinstance(val, int):
                return val

    # Parse from error message
    msg = str(exc)
    for code in (429, 529, 503, 500, 502, 504, 400, 401, 403, 404):
        if str(code) in msg:
            return code

    return None


def _extract_retry_after(exc: Exception) -> float | None:
    """Try to extract Retry-After value from exception/response headers."""
    resp = getattr(exc, "response", None)
    if resp is None:
        return None

    headers = getattr(resp, "headers", None)
    if headers is None:
        return None

    # Standard Retry-After header
    retry_after = headers.get("Retry-After") or headers.get("retry-after")
    if retry_after:
        try:
            return float(retry_after)
        except (ValueError, TypeError):
            pass

    # Anthropic-specific
    reset = headers.get("Anthropic-Ratelimit-Unified-Reset")
    if reset:
        try:
            from datetime import datetime, timezone
            reset_time = datetime.fromisoformat(reset.replace("Z", "+00:00"))
            delta = (reset_time - datetime.now(timezone.utc)).total_seconds()
            return max(0.0, delta)
        except (ValueError, TypeError):
            pass

    return None


def classify_error(error: Exception, context: str = "") -> AgentError:
    """Classify an exception into a structured AgentError.

    Used by retry logic and recovery chains to determine strategy.
    """
    msg = str(error).lower()
    status = _extract_status_code(error)
    retry_after = _extract_retry_after(error)

    # Timeout
    if isinstance(error, (asyncio.TimeoutError,)):
        return AgentError(
            ErrorCategory.TRANSIENT,
            f"Operation timed out: {context}",
            retry_after=retry_after,
            original=error,
        )

    # Rate limiting (429)
    if status == 429 or any(kw in msg for kw in ("rate limit", "rate_limit", "quota exceeded")):
        return AgentError(
            ErrorCategory.RATE_LIMITED,
            "Rate limited — will retry after backoff",
            status_code=status,
            retry_after=retry_after,
            original=error,
        )

    # Capacity / overloaded (529, 503)
    if status in (529, 503) or any(kw in msg for kw in ("overloaded", "capacity", "529")):
        return AgentError(
            ErrorCategory.CAPACITY,
            "Service overloaded",
            status_code=status,
            retry_after=retry_after,
            original=error,
        )

    # Context overflow
    if any(kw in msg for kw in ("context", "prompt too long", "token limit", "max.*token")):
        if any(kw in msg for kw in ("long", "length", "exceed", "limit", "overflow")):
            return AgentError(
                ErrorCategory.CONTEXT_OVERFLOW,
                "Context window exceeded",
                original=error,
            )

    # Transient server errors (500, 502, 504)
    if status in (500, 502, 504) or any(kw in msg for kw in (
        "unavailable", "timeout", "connection", "temporarily", "internal server",
    )):
        return AgentError(
            ErrorCategory.TRANSIENT,
            f"Transient error: {error}",
            status_code=status,
            retry_after=retry_after,
            original=error,
        )

    # Permission / auth errors
    if status in (401, 403) or any(kw in msg for kw in (
        "permission", "not authorized", "unauthorized", "forbidden", "authentication",
    )):
        return AgentError(
            ErrorCategory.PERMISSION,
            str(error),
            recoverable=False,
            status_code=status,
            original=error,
        )

    # Validation errors
    if isinstance(error, (ValueError, KeyError, TypeError)):
        return AgentError(
            ErrorCategory.VALIDATION,
            str(error),
            original=error,
        )

    # Default: tool/LLM bug
    return AgentError(
        ErrorCategory.TOOL_BUG,
        str(error),
        original=error,
    )
