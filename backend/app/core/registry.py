"""A small typed registry used for use cases, agent tools and LLM providers."""

from __future__ import annotations

from typing import Generic, Iterator, TypeVar

T = TypeVar("T")


class DuplicateRegistration(ValueError):
    pass


class Registry(Generic[T]):
    def __init__(self, kind: str) -> None:
        self.kind = kind
        self._items: dict[str, T] = {}

    def register(self, key: str, item: T) -> T:
        if key in self._items:
            raise DuplicateRegistration(f"{self.kind} '{key}' is already registered")
        self._items[key] = item
        return item

    def get(self, key: str) -> T:
        try:
            return self._items[key]
        except KeyError as exc:
            raise KeyError(f"Unknown {self.kind} '{key}'. Registered: {sorted(self._items)}") from exc

    def __contains__(self, key: str) -> bool:
        return key in self._items

    def __iter__(self) -> Iterator[T]:
        return iter(self._items.values())

    def keys(self) -> list[str]:
        return list(self._items)

    def clear(self) -> None:
        self._items.clear()
