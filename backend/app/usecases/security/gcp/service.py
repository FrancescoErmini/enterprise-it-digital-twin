"""GCP security use-case service: live investigation, rules, overview and remediation views."""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, Any

from app.it_intelligence_agent.evidence import Classification, EvidenceItem
from app.usecases.security.gcp import graph_queries as Q
from app.usecases.security.gcp import remediation, scoring
from app.usecases.security.gcp.investigation import DETECTORS, Finding, GraphSnapshot, Investigator
from app.usecases.security.gcp.rules_adapter import RulesAdapter

if TYPE_CHECKING:
    from app.platform import PlatformServices

USECASE_ID = "cloud-security-gcp"


class GcpSecurityService:
    def __init__(self, platform: "PlatformServices") -> None:
        self.platform = platform
        self.rules = RulesAdapter(platform.client)

    # -- live data ---------------------------------------------------------------------
    def snapshot(self) -> GraphSnapshot:
        return GraphSnapshot(self.platform.client, self.rules, self.platform.schema.get())

    def investigate(self, scenarios: list[str] | None = None) -> tuple[list[Finding], GraphSnapshot]:
        snap = self.snapshot()
        return Investigator(snap).run(scenarios), snap

    def rules_status(self) -> dict[str, Any]:
        catalogue = self.rules.catalogue(self.platform.schema.get())
        applicable = [c.__dict__ for c in catalogue if c.applicable]
        skipped = [c.__dict__ for c in catalogue if not c.applicable]
        return {"engine": "cartography.rules", "provider_module": "GCP", "applicable": applicable,
                "skipped": skipped, "counts": {"applicable": len(applicable), "skipped": len(skipped)}}

    def resolve_resource(self, ref: str) -> str | None:
        """Accept a node id, an instance name, a service-account email or a resource name."""
        rows = self.platform.client.read(
            "MATCH (n) WHERE n.id = $ref OR n.instancename = $ref OR n.email = $ref OR n.name = $ref "
            "RETURN n.id AS id ORDER BY size(n.id) LIMIT 1", {"ref": ref}, max_rows=1).rows
        return rows[0]["id"] if rows else None

    def resource_context(self, ref: str) -> dict[str, Any] | None:
        rid = self.resolve_resource(ref)
        if not rid:
            return None
        rows = self.platform.client.read(Q.RESOURCE_CONTEXT, {"resource_id": rid}, max_rows=1).rows
        if not rows:
            return None
        row = rows[0]
        props = {k: v for k, v in row["properties"].items() if k not in ("lastupdated", "firstseen")}
        snap = self.snapshot()
        project = snap.project_by_resource.get(rid) or (rid if "GCPProject" in row["labels"] else None)
        related_rules = [f.as_dict() for f in snap.rule_findings
                         if f.resource_id == rid or rid in {str(v) for v in f.evidence.values()}]
        return {"id": rid, "labels": row["labels"], "properties": props, "project_id": project,
                "environment": snap.environment(project), "classification": snap.classification(rid),
                "neighbors": sorted(row["neighbors"], key=lambda n: (n["type"], str(n["neighbor_id"])))
                if row["neighbors"] and row["neighbors"][0].get("type") else [],
                "rule_findings": related_rules[:30], "cypher": [Q.RESOURCE_CONTEXT.strip()]}

    def overview(self) -> dict[str, Any]:
        snap = self.snapshot()
        findings = Investigator(snap).run()
        counts = {r["label"]: r["c"] for r in self.platform.client.read(Q.OVERVIEW_COUNTS).rows}
        rule_projects = {f.evidence.get("project_id") or f.evidence.get("account_id") for f in snap.rule_findings}
        scenario_projects = {f.primary_resource.get("project_id") for f in findings}
        affected = sorted(p for p in (rule_projects | scenario_projects) if p in snap.env_by_project)
        high = [f for f in findings if f.risk_score >= 55]
        return {
            "synthetic_data": True,
            "metrics": [
                {"label": "Resources in scope", "value": sum(counts.values()), "detail": counts},
                {"label": "Projects", "value": len(snap.env_by_project)},
                {"label": "Affected projects", "value": len(affected), "detail": affected},
                {"label": "Rule findings (Cartography)", "value": len(snap.rule_findings),
                 "detail": dict(Counter(f.rule_id for f in snap.rule_findings))},
                {"label": "Investigation scenarios", "value": len(findings),
                 "detail": dict(Counter(f.scenario for f in findings))},
                {"label": "High-priority scenarios (risk >= 55)", "value": len(high)},
            ],
            "environments": snap.env_by_project,
        }

    def findings_view(self) -> dict[str, Any]:
        findings, _ = self.investigate()
        return {"findings": [f.as_dict() for f in findings], "detectors": DETECTORS,
                "scoring_profile": scoring.profile()}

    def remediation_plan(self) -> dict[str, Any]:
        findings, snap = self.investigate()
        return remediation.plan(findings, snap)

    # -- evidence helpers -----------------------------------------------------------------
    @staticmethod
    def finding_evidence(f: Finding) -> list[EvidenceItem]:
        items = [EvidenceItem(statement=f.title, classification=Classification(f.classification),
                              resource_ids=f.affected_resource_ids, source=f"investigate_exposure:{f.detector}",
                              graph_paths=f.graph_paths[:10], details={"risk_score": f.risk_score,
                                                                        "facts": f.facts})]
        for fact in f.facts:
            items.append(EvidenceItem(statement=fact, classification=Classification.VERIFIED_FACT,
                                      resource_ids=[f.primary_resource["id"]], source=f"graph:{f.detector}"))
        d = f.details
        if f.scenario == "A":
            items.append(EvidenceItem(
                statement=(f"If an attacker gained code execution on {f.primary_resource['name']}, they could obtain "
                           f"tokens for {d['principal']} from the metadata server; no evidence of compromise exists."),
                classification=Classification.UNVERIFIED_HYPOTHESIS,
                resource_ids=[f.primary_resource["id"], d["principal_id"]], source="investigate_exposure"))
        if f.scenario == "B" and d.get("privileged_impersonation_ids"):
            items.append(EvidenceItem(
                statement=("Escalation path: the development identity could impersonate an identity with IAM-admin "
                           "capability on production; whether the path is usable at request time is not verified."),
                classification=Classification.UNVERIFIED_HYPOTHESIS,
                resource_ids=[f.primary_resource["id"], *d["privileged_impersonation_ids"]],
                source="investigate_exposure"))
        return items
