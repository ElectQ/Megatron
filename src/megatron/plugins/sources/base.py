from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from ...core.types import Item
from ...core.registry import Registry


class BaseSource(ABC):
    """Abstract data source. Subclasses implement how to get items."""

    name: str = ""

    def __init__(self, **config: Any):
        self.config = config

    @abstractmethod
    async def fetch(self, since: datetime | None = None) -> list[Item]:
        """Return normalized items published after `since` (None = all)."""
        raise NotImplementedError


source_registry: Registry[BaseSource] = Registry(kind="source")


def register_source(name: str):
    return source_registry.register(name)


__all__ = ["BaseSource", "source_registry", "register_source"]
