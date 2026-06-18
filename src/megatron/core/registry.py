from __future__ import annotations

from typing import Any, Generic, TypeVar

T = TypeVar("T")


class Registry(Generic[T]):
    """Generic plugin registry. Subclasses register by name via decorator."""

    def __init__(self, kind: str):
        self.kind = kind
        self._items: dict[str, type[T]] = {}

    def register(self, name: str):
        def decorator(cls: type[T]) -> type[T]:
            if name in self._items:
                raise ValueError(f"{self.kind} '{name}' already registered")
            self._items[name] = cls
            return cls

        return decorator

    def get(self, name: str) -> type[T]:
        if name not in self._items:
            raise KeyError(f"{self.kind} '{name}' not found; available: {self.names()}")
        return self._items[name]

    def create(self, name: str, **config: Any) -> T:
        cls = self.get(name)
        return cls(**config)

    def names(self) -> list[str]:
        return sorted(self._items.keys())

    def __contains__(self, name: str) -> bool:
        return name in self._items

    def __len__(self) -> int:
        return len(self._items)
