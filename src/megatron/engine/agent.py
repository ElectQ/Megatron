from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ..llm.provider import LLMProvider
from ..core.registry import Registry
from ..plugins.tools.base import ToolSet


@dataclass
class AgentResult:
    """Final outcome of an agent run."""

    content: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    tool_calls: list[dict] = field(default_factory=list)
    turns: int = 0


@runtime_checkable
class AgentBackend(Protocol):
    """Abstract agent backend. The engine picks one per module via config —
    implementations are interchangeable (lite loop, LangGraph, etc.)."""

    async def run(
        self,
        prompt: str,
        tools: ToolSet,
        llm: LLMProvider,
    ) -> AgentResult: ...


agent_registry: Registry = Registry(kind="agent")


def register_agent(name: str):
    return agent_registry.register(name)


__all__ = ["AgentResult", "AgentBackend", "agent_registry", "register_agent"]
