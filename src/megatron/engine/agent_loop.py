from __future__ import annotations

import json
import time

from ..core.logging import get_logger
from ..llm.provider import LLMProvider
from ..plugins.tools.base import ToolSet
from .agent import AgentResult, register_agent

logger = get_logger(__name__)


@register_agent("lite")
class LiteAgentLoop:
    """Self-built minimal agent loop.

    Drives the LLM through up to `max_turns` rounds: if the model returns tool
    calls, execute them via ToolSet and feed results back; otherwise stop.
    No framework, no magic — fully debuggable. Behavior (max turns, tools,
    system prompt) is entirely config-driven; the loop itself has zero
    analysis-specific logic so it works for any task.
    """

    def __init__(self, **config):
        self.max_turns = int(config.get("max_turns", 8))
        self.system_prompt = config.get(
            "system_prompt",
            "你是一个能调用工具获取信息的分析助手。当需要更多细节时调用工具；当你掌握了足够信息可以回答时，直接输出最终结果，不要再调用工具。",
        )

    async def run(
        self,
        prompt: str,
        tools: ToolSet,
        llm: LLMProvider,
    ) -> AgentResult:
        messages: list[dict] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": prompt},
        ]
        function_specs = tools.function_specs() if len(tools) > 0 else None

        total_prompt = 0
        total_completion = 0
        total_cost = 0.0
        tool_log: list[dict] = []
        content = ""
        turns = 0
        started = time.time()

        for turn in range(1, self.max_turns + 1):
            turns = turn
            resp = await llm.chat(messages, tools=function_specs)
            total_prompt += resp.prompt_tokens
            total_completion += resp.completion_tokens
            total_cost += resp.cost_usd

            tool_calls = resp.tool_calls

            if not tool_calls:
                content = resp.content
                logger.info(
                    "agent.loop.final",
                    turn=turn,
                    tools_used=len(tool_log),
                    tokens=total_prompt + total_completion,
                )
                break

            assistant_msg: dict = {"role": "assistant", "content": resp.content}
            assistant_msg["tool_calls"] = tool_calls
            messages.append(assistant_msg)

            for call in tool_calls:
                fn = call.get("function", {})
                tool_name = fn.get("name", "")
                tool_args = _parse_args(fn.get("arguments", "{}"))
                call_id = call.get("id", tool_name)
                logger.info("agent.tool_call", turn=turn, tool=tool_name, args=tool_args)
                result = await tools.execute(tool_name, tool_args)
                tool_log.append(
                    {
                        "turn": turn,
                        "tool": tool_name,
                        "args": tool_args,
                        "ok": result.ok,
                        "error": result.error,
                    }
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": tool_name,
                        "content": result.to_llm_message(),
                    }
                )

        if not content:
            content = "[agent loop ended without final answer]"

        logger.info(
            "agent.loop.done",
            turns=turns,
            tool_calls=len(tool_log),
            cost=round(total_cost, 6),
            duration=round(time.time() - started, 2),
        )
        return AgentResult(
            content=content,
            prompt_tokens=total_prompt,
            completion_tokens=total_completion,
            cost_usd=round(total_cost, 6),
            tool_calls=tool_log,
            turns=turns,
        )


def _parse_args(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


__all__ = ["LiteAgentLoop"]
