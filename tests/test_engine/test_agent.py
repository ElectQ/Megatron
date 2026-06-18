from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from megatron.engine.agent import agent_registry
from megatron.engine.agent_loop import LiteAgentLoop
from megatron.llm.provider import ChatResponse
from megatron.plugins.tools.base import ToolSet


def test_agent_registered():
    assert "lite" in agent_registry


@pytest.mark.asyncio
async def test_agent_loop_no_tools_single_turn():
    """When LLM returns no tool_calls, loop ends immediately."""
    agent = LiteAgentLoop(max_turns=5)
    llm = MagicMock()
    llm.chat = AsyncMock(
        return_value=ChatResponse(
            content='{"briefing":"done","items":[]}',
            prompt_tokens=10,
            completion_tokens=5,
            cost_usd=0.001,
            tool_calls=[],
        )
    )
    tools = ToolSet.from_config([])
    result = await agent.run("analyze this", tools, llm)
    assert result.content == '{"briefing":"done","items":[]}'
    assert result.turns == 1
    assert result.tool_calls == []
    assert result.prompt_tokens == 10


@pytest.mark.asyncio
async def test_agent_loop_with_tool_call():
    """LLM calls a tool in turn 1, then returns final answer in turn 2."""
    call_1 = ChatResponse(
        content="",
        prompt_tokens=10,
        completion_tokens=5,
        cost_usd=0.001,
        tool_calls=[
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "fetch_url",
                    "arguments": json.dumps({"url": "https://example.com/cve"}),
                },
            }
        ],
    )
    call_2 = ChatResponse(
        content='{"briefing":"found cve","items":[]}',
        prompt_tokens=20,
        completion_tokens=8,
        cost_usd=0.002,
        tool_calls=[],
    )
    llm = MagicMock()
    llm.chat = AsyncMock(side_effect=[call_1, call_2])

    from megatron.plugins.tools.base import BaseTool, ToolResult

    class FakeTool(BaseTool):
        name = "fetch_url"
        description = "test"
        schema = {"type": "object", "properties": {"url": {"type": "string"}}}

        async def run(self, url=""):
            return ToolResult(name="fetch_url", ok=True, data={"text": "CVE details here"})

    tools = ToolSet([FakeTool()])
    agent = LiteAgentLoop(max_turns=5)
    result = await agent.run("find the cve", tools, llm)

    assert result.turns == 2
    assert result.content == '{"briefing":"found cve","items":[]}'
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0]["tool"] == "fetch_url"
    assert result.tool_calls[0]["ok"] is True
    assert result.prompt_tokens == 30  # 10 + 20
    assert llm.chat.call_count == 2


@pytest.mark.asyncio
async def test_agent_loop_respects_max_turns():
    """If LLM keeps requesting tools, loop stops at max_turns."""
    looping_resp = ChatResponse(
        content="",
        prompt_tokens=1,
        completion_tokens=1,
        cost_usd=0,
        tool_calls=[
            {"id": "c", "type": "function", "function": {"name": "fetch_url", "arguments": "{}"}}
        ],
    )
    llm = MagicMock()
    llm.chat = AsyncMock(return_value=looping_resp)

    from megatron.plugins.tools.base import BaseTool, ToolResult

    class NoopTool(BaseTool):
        name = "fetch_url"
        description = "noop"
        schema = {"type": "object", "properties": {}}

        async def run(self, **kw):
            return ToolResult(name="fetch_url", ok=True, data="ok")

    tools = ToolSet([NoopTool()])
    agent = LiteAgentLoop(max_turns=3)
    result = await agent.run("loop", tools, llm)
    assert result.turns == 3
    assert len(result.tool_calls) == 3
