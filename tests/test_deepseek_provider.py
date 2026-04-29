"""
DeepSeek V4 provider integration smoke tests.

These tests mock the AsyncOpenAI client and verify that DeepSeekProvider:
  - resolves the correct base_url and default model (deepseek-v4-flash)
  - flags ``_is_deepseek = True`` so the OpenAI reasoning-effort branch is
    bypassed (DeepSeek rejects the OpenAI-only "minimal" value)
  - threads ``DEEPSEEK_THINKING`` into ``extra_body.thinking``
  - threads ``DEEPSEEK_REASONING_EFFORT`` into the request kwargs
  - extracts DeepSeek's ``prompt_cache_hit_tokens`` usage field
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.agent.llm.openai_provider import DeepSeekProvider, OpenAIProvider
from backend.agent.llm_providers import create_provider

# ---------------------------------------------------------------------------
# Fake streaming helper
# ---------------------------------------------------------------------------


class _FakeStream:
    """Minimal async iterator that yields a single chunk and stops.

    The chunk carries a usage block — including DeepSeek-specific
    ``prompt_cache_hit_tokens`` — so the provider's extraction logic is
    exercised end to end.
    """

    def __init__(self, chunks):
        self._chunks = list(chunks)

    def __aiter__(self):
        async def _gen():
            for c in self._chunks:
                yield c
        return _gen()


def _make_capture_client():
    """Return (client_mock, captured_kwargs_holder).

    The mock implements ``chat.completions.create(**kwargs)`` and returns an
    awaitable resolving to a fake async stream.
    """
    captured: dict = {}

    usage_chunk = SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=120,
            completion_tokens=40,
            completion_tokens_details=None,
            prompt_tokens_details=None,
            prompt_cache_hit_tokens=80,
        ),
        choices=[SimpleNamespace(
            delta=SimpleNamespace(
                content=None,
                tool_calls=None,
                reasoning_content=None,
            ),
            finish_reason="stop",
        )],
    )

    async def _create(**kwargs):
        captured.update(kwargs)
        return _FakeStream([usage_chunk])

    client = MagicMock()
    client.chat = MagicMock()
    client.chat.completions = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=_create)
    return client, captured


# ---------------------------------------------------------------------------
# Provider construction
# ---------------------------------------------------------------------------


def test_deepseek_provider_defaults(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    provider = create_provider("deepseek")

    assert isinstance(provider, DeepSeekProvider)
    assert isinstance(provider, OpenAIProvider)
    assert provider._is_deepseek is True
    assert provider.model == "deepseek-v4-flash"


def test_deepseek_provider_explicit_pro(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    provider = create_provider("deepseek", model="deepseek-v4-pro")
    assert provider.model == "deepseek-v4-pro"


# ---------------------------------------------------------------------------
# Reasoning / thinking-mode wiring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_openai_reasoning_effort_does_not_leak_to_deepseek(monkeypatch):
    """OPENAI_REASONING_EFFORT="minimal" must NOT be forwarded to DeepSeek —
    DeepSeek rejects it with HTTP 400."""
    from backend.config import settings as _settings
    monkeypatch.setattr(_settings, "OPENAI_REASONING_EFFORT", "minimal")
    monkeypatch.setattr(_settings, "DEEPSEEK_THINKING", None)
    monkeypatch.setattr(_settings, "DEEPSEEK_REASONING_EFFORT", None)

    provider = DeepSeekProvider(api_key="test-key")
    client, captured = _make_capture_client()
    provider._client = client

    chunks = [c async for c in provider.complete([{"role": "user", "content": "hi"}])]

    assert "reasoning_effort" not in captured
    assert "parallel_tool_calls" not in captured
    # DeepSeek should keep the caller-supplied temperature.
    assert "temperature" in captured
    # And cache-hit tokens should propagate to the token_usage event.
    usage_events = [c for c in chunks if c.get("type") == "token_usage"]
    assert usage_events, "expected a token_usage chunk"
    assert usage_events[-1]["cache_read_tokens"] == 80


@pytest.mark.asyncio
async def test_deepseek_thinking_enabled(monkeypatch):
    from backend.config import settings as _settings
    monkeypatch.setattr(_settings, "OPENAI_REASONING_EFFORT", "")
    monkeypatch.setattr(_settings, "DEEPSEEK_THINKING", "enabled")
    monkeypatch.setattr(_settings, "DEEPSEEK_REASONING_EFFORT", "high")

    provider = DeepSeekProvider(api_key="test-key")
    client, captured = _make_capture_client()
    provider._client = client

    [c async for c in provider.complete([{"role": "user", "content": "ping"}])]

    assert captured.get("extra_body") == {"thinking": {"type": "enabled"}}
    assert captured.get("reasoning_effort") == "high"


@pytest.mark.asyncio
async def test_deepseek_thinking_disabled(monkeypatch):
    from backend.config import settings as _settings
    monkeypatch.setattr(_settings, "OPENAI_REASONING_EFFORT", "")
    monkeypatch.setattr(_settings, "DEEPSEEK_THINKING", "disabled")
    monkeypatch.setattr(_settings, "DEEPSEEK_REASONING_EFFORT", None)

    provider = DeepSeekProvider(api_key="test-key")
    client, captured = _make_capture_client()
    provider._client = client

    [c async for c in provider.complete([{"role": "user", "content": "ping"}])]

    assert captured.get("extra_body") == {"thinking": {"type": "disabled"}}
    assert "reasoning_effort" not in captured


@pytest.mark.asyncio
async def test_deepseek_thinking_unset_passes_through(monkeypatch):
    """When DEEPSEEK_THINKING is explicitly empty the DeepSeek server default
    applies; HIME must not inject any thinking toggle."""
    from backend.config import settings as _settings
    monkeypatch.setattr(_settings, "OPENAI_REASONING_EFFORT", "")
    monkeypatch.setattr(_settings, "DEEPSEEK_THINKING", None)
    monkeypatch.setattr(_settings, "DEEPSEEK_REASONING_EFFORT", None)

    provider = DeepSeekProvider(api_key="test-key")
    client, captured = _make_capture_client()
    provider._client = client

    [c async for c in provider.complete([{"role": "user", "content": "ping"}])]

    assert "extra_body" not in captured
    assert "reasoning_effort" not in captured


def test_deepseek_thinking_default_is_disabled():
    """HIME ships with thinking off by default — fast/cheap for tool-calling
    agentic loops. Verifies the static default declared on the Settings model."""
    from backend.config import Settings
    assert Settings.model_fields["DEEPSEEK_THINKING"].default == "disabled"


@pytest.mark.asyncio
async def test_deepseek_default_settings_disable_thinking():
    """End-to-end check on a freshly-instantiated Settings: the default config
    causes ``extra_body.thinking={'type': 'disabled'}`` to be sent."""
    import backend.config as cfg
    from backend.config import Settings

    fresh = Settings(DEEPSEEK_API_KEY="test-key")
    # Patch the module-local settings handle the provider reads from.
    original = cfg.settings
    cfg.settings = fresh
    try:
        provider = DeepSeekProvider(api_key="test-key")
        client, captured = _make_capture_client()
        provider._client = client
        [c async for c in provider.complete([{"role": "user", "content": "ping"}])]
        assert captured.get("extra_body") == {"thinking": {"type": "disabled"}}
    finally:
        cfg.settings = original
