"""Use-case registry.

A use case registers its identity, the node labels it needs, its agent tools,
its scoring profile, its prompt material and its UI panels. The API and the UI
enumerate this registry; nothing outside a use case's own package imports it.
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from app.core.registry import Registry
from app.it_intelligence_agent.text2cypher import FewShotExample
from app.it_intelligence_agent.tools import AgentTool

if TYPE_CHECKING:
    from app.platform import PlatformServices

logger = logging.getLogger(__name__)

PanelProvider = Callable[["PlatformServices"], Any]


@dataclass(frozen=True)
class UIPanel:
    id: str
    title: str
    kind: str  # metrics | findings | remediation_plan | rules (rendered generically by the UI)
    description: str
    provider: PanelProvider

    def describe(self) -> dict[str, str]:
        return {"id": self.id, "title": self.title, "kind": self.kind, "description": self.description}


@dataclass
class UseCase:
    id: str
    title: str
    domain: str
    provider: str
    description: str
    required_node_labels: list[str]
    tools: list[AgentTool]
    scoring_profile: dict[str, Any]
    ui_panels: list[UIPanel]
    system_prompt: str = ""
    example_questions: list[str] = field(default_factory=list)
    follow_up_questions: list[str] = field(default_factory=list)
    few_shot_examples: list[FewShotExample] = field(default_factory=list)
    synonyms: dict[str, list[str]] = field(default_factory=dict)
    on_startup: Callable[["PlatformServices"], dict[str, Any]] | None = None
    startup_report: dict[str, Any] = field(default_factory=dict)

    def panel(self, panel_id: str) -> UIPanel:
        for p in self.ui_panels:
            if p.id == panel_id:
                return p
        raise KeyError(f"Use case '{self.id}' has no panel '{panel_id}'")

    def describe(self, available_labels: set[str] | None = None) -> dict[str, Any]:
        missing = sorted(set(self.required_node_labels) - available_labels) if available_labels is not None else []
        return {
            "id": self.id, "title": self.title, "domain": self.domain, "provider": self.provider,
            "description": self.description, "required_node_labels": self.required_node_labels,
            "missing_node_labels": missing, "ready": available_labels is not None and not missing,
            "tools": [t.name for t in self.tools], "scoring_profile": self.scoring_profile,
            "ui_panels": [p.describe() for p in self.ui_panels],
            "example_questions": self.example_questions, "follow_up_questions": self.follow_up_questions,
        }


class UseCaseRegistry(Registry[UseCase]):
    def __init__(self) -> None:
        super().__init__("use case")


def load_installed(module_paths: tuple[str, ...]) -> list[Callable[[], UseCase]]:
    """Import each installed use-case package; each exposes ``build_usecase() -> UseCase``."""
    factories = []
    for path in module_paths:
        module = importlib.import_module(path)
        factories.append(getattr(module, "build_usecase"))
    return factories
