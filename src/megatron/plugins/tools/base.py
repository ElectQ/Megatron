from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ...core.registry import Registry


@dataclass
class ToolResult:
    """Standard return from a tool execution."""

    name: str
    ok: bool
    data: Any = None
    error: str = ""
    raw: dict = field(default_factory=dict)

    def to_llm_message(self) -> str:
        """Serialize for injection back into the LLM message stream."""
        if self.ok:
            import json

            try:
                return json.dumps(self.data, ensure_ascii=False)[:4000]
            except Exception:
                return str(self.data)[:4000]
        return f"[ERROR] {self.error}"


class BaseTool(ABC):
    """Abstract agent tool. Each tool declares its name + JSON schema (for
    function-calling) and implements run(). Tools are stateless and
    config-injected — the engine decides WHICH tools to enable per module.
    """

    name: str = ""
    description: str = ""

    def __init__(self, **config: Any):
        self.config = config

    @property
    @abstractmethod
    def schema(self) -> dict:
        """JSON Schema describing the tool's parameters, as required by LLM
        function-calling. Subclasses define this."""
        raise NotImplementedError

    def function_spec(self) -> dict:
        """Return the OpenAI-style 'tools' entry for this tool."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.schema,
            },
        }

    @abstractmethod
    async def run(self, **kwargs: Any) -> ToolResult:
        """Execute the tool with parsed arguments. Must return ToolResult."""
        raise NotImplementedError


tool_registry: Registry[BaseTool] = Registry(kind="tool")


def register_tool(name: str):
    return tool_registry.register(name)


class ToolSet:
    """A configured, instantiated set of tools for one module run.

    Built from a module's tools_config (list of {name, config}) — the engine
    never hard-codes which tools are available. Unknown tool names are skipped
    so a misconfiguration never crashes a run.
    """

    def __init__(self, tools: list[BaseTool]):
        self._tools: dict[str, BaseTool] = {t.name: t for t in tools}

    @classmethod
    def from_config(cls, tools_config: list[dict]) -> ToolSet:
        instances: list[BaseTool] = []
        for entry in tools_config or []:
            name = entry.get("name", "") if isinstance(entry, dict) else str(entry)
            cfg = entry.get("config", {}) if isinstance(entry, dict) else {}
            enabled = entry.get("enabled", True) if isinstance(entry, dict) else True
            if not enabled:
                continue
            if name in tool_registry:
                try:
                    instances.append(tool_registry.create(name, **cfg))
                except Exception as e:
                    from ...core.logging import get_logger

                    get_logger(__name__).warning(
                        "toolset.instantiate_failed", name=name, error=str(e)
                    )
        return cls(instances)

    def function_specs(self) -> list[dict]:
        return [t.function_spec() for t in self._tools.values()]

    async def execute(self, name: str, arguments: dict) -> ToolResult:
        tool = self._tools.get(name)
        if not tool:
            return ToolResult(name=name, ok=False, error=f"Tool '{name}' not enabled")
        try:
            return await tool.run(**arguments)
        except TypeError as e:
            return ToolResult(name=name, ok=False, error=f"Bad arguments: {e}")
        except Exception as e:
            return ToolResult(name=name, ok=False, error=str(e))

    @property
    def names(self) -> list[str]:
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)


__all__ = [
    "BaseTool",
    "ToolResult",
    "ToolSet",
    "tool_registry",
    "register_tool",
]
