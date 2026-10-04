"""retry_async: HTTP 402 (out of credit) switches to the fallback provider at once."""
from __future__ import annotations

import pytest

from backend.agent.errors import FallbackTriggered
from backend.agent.llm import retry_async


class _PaymentRequired(Exception):
    status_code = 402


@pytest.mark.asyncio
async def test_402_triggers_fallback_without_retry() -> None:
    calls = 0

    async def _call() -> None:
        nonlocal calls
        calls += 1
        raise _PaymentRequired("insufficient quota")

    with pytest.raises(FallbackTriggered):
        await retry_async(_call)
    assert calls == 1
