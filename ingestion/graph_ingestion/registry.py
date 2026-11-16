"""Data-source registry: which providers can populate the technology graph, and how."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import neo4j

LoaderFn = Callable[[neo4j.Session, int], dict[str, Any]]


@dataclass(frozen=True)
class DataSource:
    provider_id: str
    display_name: str
    ingestion_mode: str  # "synthetic" or "cartography"
    description: str
    root_label: str  # label whose nodes carry the provider's freshness (lastupdated)
    loader: LoaderFn


_REGISTRY: dict[tuple[str, str], DataSource] = {}


def register(source: DataSource) -> None:
    _REGISTRY[(source.provider_id, source.ingestion_mode)] = source


def get(provider_id: str, mode: str) -> DataSource:
    try:
        return _REGISTRY[(provider_id, mode)]
    except KeyError as exc:
        available = ", ".join(f"{p}/{m}" for p, m in sorted(_REGISTRY))
        raise SystemExit(f"No data source registered for {provider_id}/{mode}. Available: {available}") from exc


def all_sources() -> list[DataSource]:
    return [s for _, s in sorted(_REGISTRY.items())]
