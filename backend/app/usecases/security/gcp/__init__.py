"""POC 01 - Cloud Security Investigation - Google Cloud."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.usecases.registry import UIPanel, UseCase
from app.usecases.security.gcp import graph_queries, prompts, scoring
from app.usecases.security.gcp.service import USECASE_ID, GcpSecurityService
from app.usecases.security.gcp.tools import build_tools

if TYPE_CHECKING:
    from app.platform import PlatformServices

logger = logging.getLogger(__name__)


def _service(platform: "PlatformServices") -> GcpSecurityService:
    return GcpSecurityService(platform)


def _startup(platform: "PlatformServices") -> dict[str, Any]:
    service = _service(platform)
    status = service.rules_status()
    lines = [f"[{USECASE_ID}] Cartography GCP rule facts: {status['counts']['applicable']} applicable, "
             f"{status['counts']['skipped']} skipped"]
    lines += [f"  APPLICABLE {s['rule_id']}/{s['fact_id']} ({s['maturity']})" for s in status["applicable"]]
    lines += [f"  SKIPPED    {s['rule_id']}/{s['fact_id']}: {s['reason']}" for s in status["skipped"]]
    print("\n".join(lines), flush=True)
    invalid = {name: platform.validator.validate(q).reasons for name, q in graph_queries.ALL_QUERIES.items()}
    invalid = {k: v for k, v in invalid.items() if v}
    for name, reasons in invalid.items():
        logger.error("Use-case query %s failed validation: %s", name, reasons)
    return {"rules": status["counts"], "use_case_queries_valid": len(graph_queries.ALL_QUERIES) - len(invalid),
            "use_case_queries_invalid": invalid}


def build_usecase() -> UseCase:
    return UseCase(
        id=USECASE_ID,
        title="Cloud Security Investigation - Google Cloud",
        domain="security",
        provider="gcp",
        description=("POC 01. Investigates potential compromise scenarios in a Google Cloud organisation "
                     "using Cartography's GCP schema and rules engine, ranks them with a deterministic score "
                     "and proposes remediation that minimises operational disruption. Read-only."),
        required_node_labels=prompts.REQUIRED_NODE_LABELS,
        tools=build_tools(lambda ctx: _service(ctx.platform)),
        scoring_profile=scoring.profile(),
        ui_panels=[
            UIPanel("overview", "Security overview", "metrics",
                    "Resources, affected projects, rule findings and high-priority scenarios",
                    lambda p: _service(p).overview()),
            UIPanel("findings", "Investigation results", "findings",
                    "Scenario findings with evidence chains, classification and risk score",
                    lambda p: _service(p).findings_view()),
            UIPanel("remediation", "Prioritized remediation plan", "remediation_plan",
                    "Recommended and alternative mitigations with operational consequences",
                    lambda p: _service(p).remediation_plan()),
            UIPanel("rules", "Cartography rules", "rules",
                    "Applicable versus skipped GCP rule facts, with reasons",
                    lambda p: _service(p).rules_status()),
        ],
        system_prompt=prompts.SYSTEM_PROMPT,
        example_questions=prompts.EXAMPLE_QUESTIONS,
        follow_up_questions=prompts.FOLLOW_UP_QUESTIONS,
        few_shot_examples=prompts.FEW_SHOT_EXAMPLES,
        synonyms=prompts.SYNONYMS,
        on_startup=_startup,
    )
