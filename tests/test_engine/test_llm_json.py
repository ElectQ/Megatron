from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from megatron.engine.runner import ModuleRunner
from megatron.llm.provider import ChatResponse, parse_json_response


def test_repair_ignores_braces_inside_a_truncated_string():
    """A CVE writeup may quote payload `{abc`; that brace is prose, not JSON."""
    content = (
        '{"items":[{"external_id":"1","source_id":"twitter",'
        '"tier":"drop"},{"external_id":"2","source_id":"twitter",'
        '"tier":"skim","one_liner":"payload {abc'
    )

    result = parse_json_response(content)

    assert result["_partial"] is True
    assert result["items"] == [
        {"external_id": "1", "source_id": "twitter", "tier": "drop"},
        {"external_id": "2", "source_id": "twitter", "tier": "skim"},
    ]


@pytest.mark.asyncio
async def test_day_bundle_asks_for_json_mode():
    llm = SimpleNamespace(
        chat=AsyncMock(
            return_value=ChatResponse(content='{"items": []}', prompt_tokens=2, completion_tokens=3)
        )
    )
    module = SimpleNamespace(agent_backend="none")

    await ModuleRunner(None)._invoke(module, llm, "tier these", json_mode=True)

    assert llm.chat.await_args.kwargs["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_regular_call_does_not_force_json_mode():
    llm = SimpleNamespace(
        chat=AsyncMock(return_value=ChatResponse(content="OK", prompt_tokens=2, completion_tokens=3))
    )
    module = SimpleNamespace(agent_backend="none")

    await ModuleRunner(None)._invoke(module, llm, "say OK")

    assert llm.chat.await_args.kwargs["response_format"] is None
