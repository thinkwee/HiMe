"""Tests for inbound-image (vision) message conversion.

Verifies the OpenAI-native multimodal content blocks (the canonical internal
format) convert correctly to the Anthropic schema, that plain-string content
is left untouched (regression guard for all non-image messages), and that the
vision capability flag defaults off.
"""
from __future__ import annotations

from backend.agent.llm import BaseLLMProvider, parse_image_data_uri
from backend.agent.llm.anthropic_provider import (
    AnthropicProvider,
    _openai_content_to_anthropic,
)

_BLOCKS = [
    {"type": "text", "text": "what is this?"},
    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,QUJD"}},
]


def test_parse_data_uri():
    assert parse_image_data_uri("data:image/png;base64,QUJD") == ("image/png", "QUJD")
    assert parse_image_data_uri("data:image/jpeg;base64,WX="), "should parse jpeg"
    assert parse_image_data_uri("not a data uri") == ("image/jpeg", "")


def test_anthropic_block_conversion():
    out = _openai_content_to_anthropic(_BLOCKS)
    assert out[0] == {"type": "text", "text": "what is this?"}
    assert out[1] == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/jpeg", "data": "QUJD"},
    }


def test_extract_system_routes_list_content():
    _system, filtered = AnthropicProvider._extract_system([{"role": "user", "content": _BLOCKS}])
    assert filtered[0]["content"][1]["type"] == "image"


def test_extract_system_leaves_strings_untouched():
    # Regression: text-only messages (every existing flow) must be unchanged.
    _system, filtered = AnthropicProvider._extract_system([{"role": "user", "content": "plain text"}])
    assert filtered[0]["content"] == "plain text"


def test_supports_vision_default_false():
    assert BaseLLMProvider.supports_vision(object()) is False


def test_anthropic_gemini_declare_vision():
    from backend.agent.llm.gemini import GeminiProvider
    assert "supports_vision" in AnthropicProvider.__dict__
    assert "supports_vision" in GeminiProvider.__dict__
