"""Adapter over Cartography's official rules engine (``cartography.rules``).

At startup it discovers the installed rule catalogue, keeps the facts whose
module is GCP, and decides for each whether the loaded graph can satisfy it:
a fact is applicable when every node label and relationship type its Cypher
matches on is present in the graph. Facts that cannot run are listed with the
reason. Execution goes through the engine's own runner (``_run_fact``), which
runs the fact query in a read transaction and parses rows with the rule's
output model; this module only normalises the results.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from cartography.rules.data.rules import RULES
from cartography.rules.runners import _run_fact
from cartography.rules.spec.model import Fact, Maturity, Module, Rule
from cartography.rules.spec.result import CounterResult

from app.technology_graph.client import GraphClient
from app.technology_graph.schema import GraphSchema

logger = logging.getLogger(__name__)

_NODE_LABEL = re.compile(r"\(\s*\w*\s*:\s*`?([A-Za-z]\w*)`?")
_REL_TYPE = re.compile(r"\[\s*\w*\s*:\s*`?([A-Z_]+)`?")


@dataclass
class FactStatus:
    rule_id: str
    rule_name: str
    fact_id: str
    fact_name: str
    maturity: str
    asset_label: str | None
    required_labels: list[str]
    required_relationship_types: list[str]
    applicable: bool
    reason: str
    frameworks: list[str] = field(default_factory=list)


@dataclass
class NormalizedFinding:
    rule_id: str
    rule_name: str
    fact_id: str
    fact_name: str
    maturity: str
    resource_label: str | None
    resource_id: str | None
    description: str
    evidence: dict[str, Any]
    source: str = "cartography-rules"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _requirements(fact: Fact) -> tuple[list[str], list[str]]:
    labels = sorted(set(_NODE_LABEL.findall(fact.cypher_query)))
    rels = sorted(set(_REL_TYPE.findall(fact.cypher_query)))
    return labels, rels


def _frameworks(rule: Rule) -> list[str]:
    return sorted({f"{fw.short_name}:{fw.requirement}" for fw in rule.frameworks})


class RulesAdapter:
    def __init__(self, client: GraphClient, include_experimental: bool = True) -> None:
        self.client = client
        self.include_experimental = include_experimental

    @staticmethod
    def gcp_facts() -> list[tuple[Rule, Fact]]:
        return [(rule, fact) for rule in RULES.values() for fact in rule.facts if fact.module == Module.GCP]

    def catalogue(self, schema: GraphSchema) -> list[FactStatus]:
        statuses = []
        for rule, fact in self.gcp_facts():
            labels, rels = _requirements(fact)
            missing_labels = [lbl for lbl in labels if not schema.has_label(lbl)]
            missing_rels = [r for r in rels if not schema.has_relationship_type(r)]
            if not self.include_experimental and fact.maturity == Maturity.EXPERIMENTAL:
                applicable, reason = False, "Experimental fact excluded by configuration"
            elif missing_labels or missing_rels:
                parts = []
                if missing_labels:
                    parts.append("no nodes with label(s) " + ", ".join(missing_labels))
                if missing_rels:
                    parts.append("no relationships of type(s) " + ", ".join(missing_rels))
                applicable, reason = False, "Graph cannot satisfy this fact: " + "; ".join(parts)
            else:
                applicable, reason = True, "All required labels and relationship types are present"
            statuses.append(FactStatus(
                rule_id=rule.id, rule_name=rule.name, fact_id=fact.id, fact_name=fact.name,
                maturity=fact.maturity.value, asset_label=fact.asset_label, required_labels=labels,
                required_relationship_types=rels, applicable=applicable, reason=reason,
                frameworks=_frameworks(rule)))
        return sorted(statuses, key=lambda s: (not s.applicable, s.rule_id, s.fact_id))

    def run(self, schema: GraphSchema, rule_ids: list[str] | None = None) -> dict[str, Any]:
        """Execute every applicable GCP fact (optionally filtered by rule id) through the official runner."""
        statuses = self.catalogue(schema)
        wanted = [s for s in statuses if s.applicable and (not rule_ids or s.rule_id in rule_ids)]
        counter = CounterResult(total_facts=len(wanted))
        findings: list[NormalizedFinding] = []
        executed: list[dict[str, Any]] = []
        for idx, status in enumerate(wanted, start=1):
            rule = RULES[status.rule_id]
            fact = next(f for f in rule.facts if f.id == status.fact_id)
            counter.current_fact = idx
            try:
                result = _run_fact(fact, rule, self.client.driver, self.client.database, counter, "json",
                                   self.client.settings.neo4j_uri)
            except Exception as exc:  # noqa: BLE001 - a broken fact must not hide the others
                logger.warning("Rule fact %s failed: %s", fact.id, exc)
                executed.append({"rule_id": rule.id, "fact_id": fact.id, "ok": False, "error": str(exc),
                                 "findings": 0})
                continue
            executed.append({"rule_id": rule.id, "fact_id": fact.id, "ok": True, "findings": len(result.findings),
                             "total_assets": result.total_assets, "failing": result.failing})
            description = rule.description.strip().split("\n")[0]
            for f in result.findings:
                data = f.model_dump(exclude={"extra"})
                data.update(f.extra or {})
                rid = data.get(fact.asset_id_field) if fact.asset_id_field else None
                findings.append(NormalizedFinding(
                    rule_id=rule.id, rule_name=rule.name, fact_id=fact.id, fact_name=fact.name,
                    maturity=fact.maturity.value, resource_label=fact.asset_label,
                    resource_id=str(rid) if rid is not None else None, description=description,
                    evidence={k: v for k, v in data.items() if v is not None}))
        findings.sort(key=lambda f: (f.rule_id, f.fact_id, f.resource_id or "", str(sorted(f.evidence.items()))))
        return {"findings": findings, "executed": executed,
                "skipped": [asdict(s) for s in statuses if not s.applicable
                            and (not rule_ids or s.rule_id in rule_ids)]}
