"""Agent tools registered by the GCP security use case."""

from __future__ import annotations

from typing import Callable

from pydantic import BaseModel, Field

from app.it_intelligence_agent.evidence import Classification, EvidenceItem, ToolOutput
from app.it_intelligence_agent.tools import AgentTool, ToolContext
from app.usecases.security.gcp import remediation
from app.usecases.security.gcp.service import USECASE_ID, GcpSecurityService

ServiceFactory = Callable[[ToolContext], GcpSecurityService]


class RunRulesInput(BaseModel):
    rule_ids: list[str] | None = Field(None, description="Optional Cartography rule ids; default: all applicable GCP rules")
    max_findings: int = Field(40, ge=1, le=200)


class ResourceContextInput(BaseModel):
    resource_id: str = Field(..., description="Node id, instance name, service-account email or resource name")


class InvestigateInput(BaseModel):
    scenarios: list[str] | None = Field(None, description=(
        "Optional scenario filter: A public workload with privileged identity, B cross-environment privilege, "
        "C public sensitive storage, D permissive network to a sensitive service, E shared privileged identity"))
    resource_id: str | None = Field(None, description="Optional: only findings involving this resource")
    top_n: int = Field(5, ge=1, le=25)


class CompareRemediationInput(BaseModel):
    finding_id: str | None = Field(None, description="Finding id from investigate_exposure, e.g. 'E::<id>'")
    resource_id: str | None = Field(None, description="Or a resource; all findings involving it are compared")


def build_tools(service_for: ServiceFactory) -> list[AgentTool]:
    def run_security_rules(ctx: ToolContext, args: RunRulesInput) -> ToolOutput:
        svc = service_for(ctx)
        result = svc.rules.run(ctx.platform.schema.get(), args.rule_ids)
        findings = result["findings"]
        by_rule: dict[str, int] = {}
        for f in findings:
            by_rule[f.rule_id] = by_rule.get(f.rule_id, 0) + 1
        evidence = [EvidenceItem(statement=f"Cartography rule {f.rule_id} ({f.fact_id}) matched {f.resource_id}",
                                 classification=Classification.VERIFIED_FACT,
                                 resource_ids=[f.resource_id] if f.resource_id else [],
                                 source=f"cartography-rules:{f.rule_id}") for f in findings[:args.max_findings]]
        return ToolOutput(tool="run_security_rules",
                          summary=f"{len(result['executed'])} facts executed, {len(findings)} findings, "
                                  f"{len(result['skipped'])} facts skipped",
                          data={"findings_by_rule": by_rule,
                                "findings": [f.as_dict() for f in findings[:args.max_findings]],
                                "skipped": [{"rule_id": s["rule_id"], "fact_id": s["fact_id"], "reason": s["reason"]}
                                            for s in result["skipped"]]},
                          evidence=evidence,
                          missing_evidence=[f"Rule fact {s['fact_id']} could not run: {s['reason']}"
                                            for s in result["skipped"]][:10])

    def get_resource_context(ctx: ToolContext, args: ResourceContextInput) -> ToolOutput:
        svc = service_for(ctx)
        context = svc.resource_context(args.resource_id)
        if not context:
            return ToolOutput(ok=False, tool="get_resource_context", summary="Resource not found",
                              error=f"No node matches '{args.resource_id}'",
                              missing_evidence=[f"'{args.resource_id}' is not in the graph"])
        neighbour_ids = sorted({n["neighbor_id"] for n in context["neighbors"] if n.get("neighbor_id")})
        evidence = [EvidenceItem(statement=f"{context['id']} ({', '.join(context['labels'])}) in project "
                                           f"{context['project_id']} ({context['environment']})",
                                 classification=Classification.VERIFIED_FACT, resource_ids=[context["id"]],
                                 source="get_resource_context")]
        if neighbour_ids:
            evidence.append(EvidenceItem(statement=f"{len(neighbour_ids)} directly connected node(s)",
                                         classification=Classification.VERIFIED_FACT,
                                         resource_ids=neighbour_ids[:100], source="get_resource_context"))
        missing = [] if context["classification"] else [
            f"No data-classification label on {context['id']}; sensitivity is unknown"]
        missing.append("Business criticality, ownership and runtime behaviour are not recorded in the graph")
        return ToolOutput(tool="get_resource_context", summary=f"Context for {context['id']}",
                          data=context, evidence=evidence, cypher=context.pop("cypher"), missing_evidence=missing)

    def investigate_exposure(ctx: ToolContext, args: InvestigateInput) -> ToolOutput:
        svc = service_for(ctx)
        findings, snap = svc.investigate(args.scenarios)
        if args.resource_id:
            rid = svc.resolve_resource(args.resource_id) or args.resource_id
            findings = [f for f in findings if rid in f.affected_resource_ids or rid == f.primary_resource["id"]]
        top = findings[:args.top_n]
        evidence = [item for f in top for item in svc.finding_evidence(f)]
        missing = sorted({m for f in top for m in f.missing_evidence})
        ranking = [{"rank": i + 1, "finding_id": f.finding_id, "scenario": f.scenario, "title": f.title,
                    "risk_score": f.risk_score, "severity": f.severity, "classification": f.classification,
                    "factors": f.factors} for i, f in enumerate(top)]
        return ToolOutput(tool="investigate_exposure",
                          summary=f"{len(findings)} scenario finding(s); returning top {len(top)} by deterministic risk score",
                          data={"ranking": ranking, "findings": [f.as_dict() for f in top],
                                "total_findings": len(findings),
                                "scoring": "risk_score = 100 x confidence x sum(weight x factor); weights in scoring_profile",
                                "rules_executed": len(snap.rules_executed)},
                          evidence=evidence, cypher=list(dict.fromkeys(snap.cypher)), missing_evidence=missing)

    def compare_remediation_options(ctx: ToolContext, args: CompareRemediationInput) -> ToolOutput:
        svc = service_for(ctx)
        findings, snap = svc.investigate()
        if args.finding_id:
            selected = [f for f in findings if f.finding_id == args.finding_id]
        elif args.resource_id:
            rid = svc.resolve_resource(args.resource_id) or args.resource_id
            selected = [f for f in findings if rid in f.affected_resource_ids or rid == f.primary_resource["id"]]
        else:
            selected = findings
        if not selected:
            return ToolOutput(ok=False, tool="compare_remediation_options", summary="No matching finding",
                              error="No investigation finding matches the given finding_id/resource_id",
                              missing_evidence=["Run investigate_exposure first to obtain finding ids"])
        assessments = [remediation.assess(f, snap) for f in selected[:8]]
        evidence = [EvidenceItem(statement=f"Remediation options for {a['finding_id']}: recommended "
                                           f"{(a['recommended_mitigation'] or {}).get('id')} "
                                           f"({(a['recommended_mitigation'] or {}).get('disruption')} disruption)",
                                 classification=Classification.VERIFIED_FACT,
                                 resource_ids=a["affected_resources"][:50], source="compare_remediation_options")
                    for a in assessments]
        return ToolOutput(tool="compare_remediation_options",
                          summary=f"Compared options for {len(assessments)} finding(s); nothing is executed",
                          data={"assessments": assessments, "executes_changes": False}, evidence=evidence,
                          cypher=list(dict.fromkeys(snap.cypher)),
                          missing_evidence=sorted({m for a in assessments for m in a["missing_information"]}))

    return [
        AgentTool("run_security_rules",
                  "Run Cartography's official GCP security rules against the graph; returns normalised findings "
                  "with resource ids, plus the rules that cannot run on this data and why.",
                  RunRulesInput, USECASE_ID, run_security_rules),
        AgentTool("get_resource_context",
                  "Describe one resource: labels, properties, project, environment, data classification, "
                  "connected nodes and rule findings that mention it.",
                  ResourceContextInput, USECASE_ID, get_resource_context),
        AgentTool("investigate_exposure",
                  "Detect and rank potential compromise scenarios (A-E) with deterministic risk scores, "
                  "evidence chains, classifications and missing evidence.",
                  InvestigateInput, USECASE_ID, investigate_exposure),
        AgentTool("compare_remediation_options",
                  "Compare remediation options for a finding or resource: operational disruption computed from "
                  "the graph, trade-offs, validation and rollback. Read-only: never applies changes.",
                  CompareRemediationInput, USECASE_ID, compare_remediation_options),
    ]

