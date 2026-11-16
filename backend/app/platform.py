"""Composition root: builds the platform services and registers installed use cases."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel

from app.core.config import Settings
from app.core.logging import AuditLog
from app.it_intelligence_agent.llm import get_chat_model
from app.it_intelligence_agent.platform_tools import register_platform_tools
from app.it_intelligence_agent.text2cypher import FewShotExample, validate_examples
from app.it_intelligence_agent.tools import ToolRegistry
from app.technology_graph.client import GraphClient
from app.technology_graph.query_service import QueryService
from app.technology_graph.schema import SchemaService
from app.technology_graph.validator import CypherValidator
from app.usecases.registry import UseCaseRegistry, load_installed

logger = logging.getLogger(__name__)


class PlatformServices:
    def __init__(self, settings: Settings, client: GraphClient | None = None,
                 llm: BaseChatModel | None = None) -> None:
        self.settings = settings
        self.client = client or GraphClient(settings)
        self.schema = SchemaService(self.client)
        self.validator = CypherValidator(self.client, self.schema, settings.query_max_length)
        self.audit = AuditLog(settings.audit_log_path)
        self.queries = QueryService(self.client, self.validator, self.audit)
        self.tools = ToolRegistry()
        self.usecases = UseCaseRegistry()
        self._llm = llm
        self._validated_examples: dict[str, list[FewShotExample]] = {}
        register_platform_tools(self.tools)

    # -- lifecycle -----------------------------------------------------------------------
    def install_usecases(self) -> None:
        for factory in load_installed(self.settings.installed_usecases):
            usecase = factory()
            self.usecases.register(usecase.id, usecase)
            for tool in usecase.tools:
                self.tools.add(tool)
            self.schema.primary_labels.update(usecase.required_node_labels)
            logger.info("Registered use case %s with %d tools", usecase.id, len(usecase.tools))

    def startup(self) -> dict[str, Any]:
        """Run once the graph is reachable: introspect schema, validate examples, run use-case startup hooks."""
        schema = self.schema.get(refresh=True)
        report: dict[str, Any] = {"labels": len(schema.labels), "relationship_types": len(schema.relationship_types),
                                  "usecases": {}}
        for usecase in self.usecases:
            self._validated_examples[usecase.id] = validate_examples(usecase.few_shot_examples, self.validator)
            if usecase.on_startup:
                usecase.startup_report = usecase.on_startup(self)
            report["usecases"][usecase.id] = {
                "few_shot_examples_valid": len(self._validated_examples[usecase.id]),
                "few_shot_examples_total": len(usecase.few_shot_examples),
                **usecase.startup_report}
        return report

    # -- accessors used by agent tools -------------------------------------------------
    def llm(self) -> BaseChatModel:
        if self._llm is None:
            self._llm = get_chat_model(self.settings)
        return self._llm

    def examples_for(self, usecase_id: str | None) -> list[FewShotExample]:
        if usecase_id:
            return self._validated_examples.get(usecase_id, [])
        return [ex for exs in self._validated_examples.values() for ex in exs]

    def synonyms_for(self, usecase_id: str | None) -> dict[str, list[str]]:
        if usecase_id and usecase_id in self.usecases:
            return self.usecases.get(usecase_id).synonyms
        merged: dict[str, list[str]] = {}
        for uc in self.usecases:
            for k, v in uc.synonyms.items():
                merged.setdefault(k, []).extend(v)
        return merged

    def prompt_for(self, usecase_id: str | None) -> str:
        if usecase_id and usecase_id in self.usecases:
            return self.usecases.get(usecase_id).system_prompt
        return ""

    def manifest(self) -> dict[str, Any]:
        path = Path(self.settings.ingestion_manifest_path)
        if not path.exists():
            return {"sources": {}}
        try:
            return json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return {"sources": {}}
