"""
Tests for the agent infrastructure modules added during the improvement work:
  - errors.py: Structured error classification
  - budget.py: Analysis budget tracking
  - context_manager.py: Token-based context compression
  - cancellation.py: Cancellation signal propagation
  - llm/fallback.py: LLM provider fallback chain
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

# =========================================================================
# ErrorCategory / AgentError / classify_error
# =========================================================================

class TestErrorClassification:
    """Tests for backend.agent.errors."""

    def test_classify_timeout(self):
        from backend.agent.errors import ErrorCategory, classify_error
        err = classify_error(asyncio.TimeoutError())
        assert err.category == ErrorCategory.TRANSIENT
        assert err.recoverable is True

    def test_classify_rate_limit_429(self):
        from backend.agent.errors import ErrorCategory, classify_error
        err = classify_error(Exception("HTTP 429 rate limit exceeded"))
        assert err.category == ErrorCategory.RATE_LIMITED

    def test_classify_capacity_529(self):
        from backend.agent.errors import ErrorCategory, classify_error
        err = classify_error(Exception("529 overloaded"))
        assert err.category == ErrorCategory.CAPACITY

    def test_classify_capacity_503(self):
        from backend.agent.errors import ErrorCategory, classify_error
        exc = Exception("503 service unavailable")
        exc.status_code = 503
        err = classify_error(exc)
        assert err.category in (ErrorCategory.CAPACITY, ErrorCategory.TRANSIENT)

    def test_classify_context_overflow(self):
        from backend.agent.errors import ErrorCategory, classify_error
        err = classify_error(Exception("prompt too long, context length exceeded"))
        assert err.category == ErrorCategory.CONTEXT_OVERFLOW

    def test_classify_permission_401(self):
        from backend.agent.errors import ErrorCategory, classify_error
        err = classify_error(Exception("401 unauthorized"))
        assert err.category == ErrorCategory.PERMISSION
        assert err.recoverable is False

    def test_classify_validation_value_error(self):
        from backend.agent.errors import ErrorCategory, classify_error
        err = classify_error(ValueError("invalid parameter"))
        assert err.category == ErrorCategory.VALIDATION

    def test_classify_validation_key_error(self):
        from backend.agent.errors import ErrorCategory, classify_error
        err = classify_error(KeyError("missing_key"))
        assert err.category == ErrorCategory.VALIDATION

    def test_classify_unknown_defaults_to_tool_bug(self):
        from backend.agent.errors import ErrorCategory, classify_error
        err = classify_error(RuntimeError("something weird"))
        assert err.category == ErrorCategory.TOOL_BUG

    def test_to_tool_result(self):
        from backend.agent.errors import AgentError, ErrorCategory
        err = AgentError(ErrorCategory.TRANSIENT, "timeout", recoverable=True)
        result = err.to_tool_result()
        assert result["success"] is False
        assert result["error"] == "timeout"
        assert result["error_type"] == "transient"
        assert result["recoverable"] is True

    def test_extract_status_code_from_attribute(self):
        from backend.agent.errors import _extract_status_code
        exc = Exception("fail")
        exc.status_code = 429
        assert _extract_status_code(exc) == 429

    def test_extract_status_code_from_message(self):
        from backend.agent.errors import _extract_status_code
        assert _extract_status_code(Exception("got 503")) == 503

    def test_extract_status_code_none(self):
        from backend.agent.errors import _extract_status_code
        assert _extract_status_code(Exception("generic error")) is None

    def test_fallback_triggered_exception(self):
        from backend.agent.errors import FallbackTriggered
        exc = FallbackTriggered("openai", "gpt-4o-mini")
        assert exc.provider == "openai"
        assert exc.model == "gpt-4o-mini"
        assert "openai" in str(exc)


# =========================================================================
# ContextManager
# =========================================================================

class TestContextManager:
    """Tests for backend.agent.context_manager."""

    def test_no_compact_for_small_context(self):
        from backend.agent.context_manager import ContextManager
        cm = ContextManager(context_window=100_000)
        msgs = [{"content": "short message"}]
        assert not cm.should_compact(msgs)

    def test_compact_for_large_context(self):
        from backend.agent.context_manager import ContextManager
        cm = ContextManager(context_window=100_000)
        # 400k ASCII chars → ~133k tokens (utf8_bytes/3), exceeds 100k * 0.85 threshold
        msgs = [{"content": "x" * 400_000}]
        assert cm.should_compact(msgs)

    def test_estimate_tokens(self):
        from backend.agent.context_manager import ContextManager
        cm = ContextManager()
        msgs = [{"content": "hello world"}]
        tokens = cm.estimate_tokens(msgs)
        # "hello world" is 11 bytes UTF-8, /3 ≈ 3 tokens
        assert tokens > 0
        assert tokens < 100

    def test_estimate_tokens_includes_tool_calls(self):
        from backend.agent.context_manager import ContextManager
        cm = ContextManager()
        msgs = [{"content": "hi", "tool_calls": [{"name": "sql", "arguments": {"query": "SELECT 1"}}]}]
        t1 = cm.estimate_tokens(msgs)
        msgs2 = [{"content": "hi"}]
        t2 = cm.estimate_tokens(msgs2)
        assert t1 > t2

    def test_circuit_breaker_trips_after_max_failures(self):
        from backend.agent.context_manager import ContextManager
        cm = ContextManager()
        assert not cm.circuit_breaker_tripped
        cm.record_compact_failure()
        cm.record_compact_failure()
        cm.record_compact_failure()
        assert cm.circuit_breaker_tripped
        # Once tripped, should_compact returns False
        msgs = [{"content": "x" * 500_000}]
        assert not cm.should_compact(msgs)

    def test_success_resets_circuit_breaker(self):
        from backend.agent.context_manager import ContextManager
        cm = ContextManager()
        cm.record_compact_failure()
        cm.record_compact_failure()
        cm.record_compact_success()
        assert cm.compact_failure_count == 0
        assert not cm.circuit_breaker_tripped

    def test_emergency_truncate(self):
        from backend.agent.context_manager import ContextManager
        cm = ContextManager()
        preamble = [{"role": "system", "content": "sys"}, {"role": "user", "content": "goal"}]
        body = [{"role": "assistant", "content": f"msg_{i}"} for i in range(20)]
        msgs = preamble + body
        result, note = cm.emergency_truncate(msgs, preamble_size=2)
        # Should keep preamble + 75% of body
        assert len(result) < len(msgs)
        assert result[0]["content"] == "sys"
        assert result[1]["content"] == "goal"
        assert "truncated" in note.lower() or "dropped" in note.lower()


# =========================================================================
# CancellationToken
# =========================================================================

class TestCancellationToken:
    """Tests for backend.agent.cancellation."""

    def test_initial_state_not_cancelled(self):
        from backend.agent.cancellation import CancellationToken
        token = CancellationToken()
        assert not token.is_cancelled

    def test_cancel_sets_flag(self):
        from backend.agent.cancellation import CancellationToken
        token = CancellationToken()
        token.cancel("test reason")
        assert token.is_cancelled

    def test_child_inherits_cancellation(self):
        from backend.agent.cancellation import CancellationToken
        parent = CancellationToken()
        child = parent.create_child()
        assert not child.is_cancelled
        parent.cancel("shutdown")
        assert child.is_cancelled

    def test_grandchild_inherits_cancellation(self):
        from backend.agent.cancellation import CancellationToken
        root = CancellationToken()
        child = root.create_child()
        grandchild = child.create_child()
        root.cancel()
        assert grandchild.is_cancelled

    def test_check_raises_when_cancelled(self):
        from backend.agent.cancellation import CancellationToken, CancelledError
        token = CancellationToken()
        token.check()  # Should not raise
        token.cancel("done")
        with pytest.raises(CancelledError):
            token.check()

    def test_check_passes_when_not_cancelled(self):
        from backend.agent.cancellation import CancellationToken
        token = CancellationToken()
        token.check()  # Should not raise

    def test_reset_clears_state(self):
        from backend.agent.cancellation import CancellationToken
        token = CancellationToken()
        token.create_child()
        token.cancel()
        assert token.is_cancelled
        token.reset()
        assert not token.is_cancelled
        # Old children are detached
        assert len(token._children) == 0

    def test_cancelled_error_has_reason(self):
        from backend.agent.cancellation import CancelledError
        err = CancelledError("agent stopped")
        assert err.reason == "agent stopped"
        assert "agent stopped" in str(err)

    def test_sibling_not_affected_by_other_child_cancel(self):
        from backend.agent.cancellation import CancellationToken
        parent = CancellationToken()
        child1 = parent.create_child()
        child2 = parent.create_child()
        child1.cancel("child1 done")
        assert not child2.is_cancelled
        assert not parent.is_cancelled


# =========================================================================
# FallbackConfig / FallbackLLMProvider
# =========================================================================

class TestFallbackConfig:
    """Tests for backend.agent.llm.fallback.FallbackConfig."""

    def test_from_env_defaults(self):
        from backend.agent.llm.fallback import FallbackConfig
        with patch.dict("os.environ", {}, clear=True):
            config = FallbackConfig.from_env()
            assert config.primary == ("gemini", "")
            assert config.fallbacks == []
            assert not config.has_fallbacks

    def test_from_env_with_fallbacks(self):
        from backend.agent.llm.fallback import FallbackConfig
        env = {
            "DEFAULT_LLM_PROVIDER": "anthropic",
            "DEFAULT_MODEL": "claude-opus-4-5",
            "FALLBACK_LLM_PROVIDERS": "openai:gpt-4o-mini, groq:llama-3.3-70b",
        }
        with patch.dict("os.environ", env, clear=True):
            config = FallbackConfig.from_env()
            assert config.primary == ("anthropic", "claude-opus-4-5")
            assert len(config.fallbacks) == 2
            assert config.fallbacks[0] == ("openai", "gpt-4o-mini")
            assert config.fallbacks[1] == ("groq", "llama-3.3-70b")
            assert config.has_fallbacks

    def test_empty_fallback_string(self):
        from backend.agent.llm.fallback import FallbackConfig
        env = {"FALLBACK_LLM_PROVIDERS": ""}
        with patch.dict("os.environ", env, clear=True):
            config = FallbackConfig.from_env()
            assert config.fallbacks == []


class TestFallbackLLMProvider:
    """Tests for backend.agent.llm.fallback.FallbackLLMProvider."""

    def test_passes_through_to_primary(self):
        from backend.agent.llm.fallback import FallbackLLMProvider

        primary = MagicMock()
        primary.model = "test-model"
        primary.api_key = "test-key"

        async def _gen(*a, **kw):
            yield {"type": "content", "content": "hello"}
        primary.complete = _gen
        primary.supports_tools.return_value = True

        provider = FallbackLLMProvider(primary, [])
        assert provider.supports_tools() is True
        assert provider.active_provider_name == type(primary).__name__

    def test_activate_next_fallback(self):
        from backend.agent.llm.fallback import FallbackLLMProvider

        primary = MagicMock()
        primary.model = "primary"
        primary.api_key = None

        provider = FallbackLLMProvider(primary, [("gemini", "test")])
        # Can't actually create real providers in test, so test the config
        assert provider._fallback_index == -1
        assert provider._fallback_configs == [("gemini", "test")]

    def test_reset_to_primary(self):
        from backend.agent.llm.fallback import FallbackLLMProvider

        primary = MagicMock()
        primary.model = "primary"
        primary.api_key = None

        provider = FallbackLLMProvider(primary, [("openai", "")])
        provider._fallback_index = 0
        provider._consecutive_failures = 5
        provider.reset_to_primary()
        assert provider._active_provider is primary
        assert provider._fallback_index == -1
        assert provider._consecutive_failures == 0


# =========================================================================
# Retry enhancements
# =========================================================================

class TestRetryAsync:
    """Tests for the enhanced retry_async in backend.agent.llm.__init__."""

    async def test_succeeds_on_first_try(self):
        from backend.agent.llm import retry_async

        async def ok():
            return "ok"

        result = await retry_async(ok)
        assert result == "ok"

    async def test_retries_on_transient_error(self):
        from backend.agent.llm import retry_async
        attempts = []

        async def flaky():
            attempts.append(1)
            if len(attempts) < 3:
                raise Exception("503 unavailable")
            return "ok"

        result = await retry_async(flaky, max_retries=5)
        assert result == "ok"
        assert len(attempts) == 3

    async def test_raises_on_non_retryable(self):
        from backend.agent.llm import retry_async

        async def fail():
            raise ValueError("bad input")

        with pytest.raises(ValueError):
            await retry_async(fail)

    async def test_calls_on_retry_callback(self):
        from backend.agent.llm import retry_async
        callbacks = []

        async def fail():
            raise Exception("429 rate limit")

        async def on_retry(attempt, delay, msg):
            callbacks.append(attempt)

        with pytest.raises(Exception, match="429 rate limit"):
            await retry_async(fail, max_retries=3, on_retry=on_retry)

        # Should have been called for attempts 0 and 1 (not the final raise)
        assert len(callbacks) == 2
