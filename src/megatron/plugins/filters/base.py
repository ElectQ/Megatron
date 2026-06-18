from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ...core.registry import Registry
from ...core.types import Item


class BaseFilter(ABC):
    """Scores and selects items. Subclasses define scoring logic."""

    name: str = ""

    def __init__(self, **config: Any):
        self.config = config
        self.threshold = float(config.get("threshold", 0.0))

    @abstractmethod
    def score(self, item: Item) -> float:
        """Return importance 0.0~1.0."""
        raise NotImplementedError

    def should_include(self, item: Item) -> bool:
        return self.score(item) >= self.threshold


filter_registry: Registry[BaseFilter] = Registry(kind="filter")


def register_filter(name: str):
    return filter_registry.register(name)


def run_filters(items: list[Item], filters: list[BaseFilter]) -> list[tuple[Item, float]]:
    """Apply filters: keep items passing all, scored by average across filters.

    Returns list of (item, combined_score) sorted desc.
    """
    scored: list[tuple[Item, float]] = []
    for item in items:
        if not filters:
            scored.append((item, 0.0))
            continue
        passes = True
        scores = []
        for f in filters:
            s = f.score(item)
            scores.append(s)
            if not f.should_include(item):
                passes = False
        if passes:
            avg = sum(scores) / len(scores)
            scored.append((item, avg))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


__all__ = ["BaseFilter", "filter_registry", "register_filter", "run_filters"]
