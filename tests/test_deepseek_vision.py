"""Vision capability checks for DeepSeek models."""

from backend.agent.llm.openai_provider import DeepSeekProvider


def test_deepseek_flash_supports_vision():
    provider = DeepSeekProvider(model="deepseek-flash", api_key="test-key")
    assert provider.supports_vision() is True


def test_legacy_deepseek_flash_aliases_support_vision():
    for model in ("deepseek-v4-flash", "deepseek-v4-flash-vision-exp"):
        provider = DeepSeekProvider(model=model, api_key="test-key")
        assert provider.supports_vision() is True


def test_deepseek_pro_does_not_claim_vision_support():
    provider = DeepSeekProvider(model="deepseek-v4-pro", api_key="test-key")
    assert provider.supports_vision() is False
