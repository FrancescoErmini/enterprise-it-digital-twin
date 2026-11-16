"""Remediation assessments for investigation findings.

Every option's operational consequences are computed from the graph (which
workloads run as the identity, which instances sit behind the rule, which
grants would be removed). Nothing is executed: the platform is read-only and
only recommends.
"""

from __future__ import annotations

from typing import Any

from app.usecases.security.gcp import scoring
from app.usecases.security.gcp.investigation import Finding, GraphSnapshot


def _disruption_for_workloads(n: int, base: str = "low") -> str:
    if n >= 3:
        return "high"
    if n >= 1:
        return "medium" if base == "low" else base
    return base


def _option(oid: str, title: str, actions: list[str], disruption: str, closes: bool, consequences: str,
            affected: list[str], validation: list[str], rollback: str) -> dict[str, Any]:
    return {"id": oid, "title": title, "actions": actions, "disruption": disruption,
            "closes_exposure": closes, "operational_consequences": consequences,
            "workloads_affected": affected, "validation_steps": validation, "rollback": rollback}


def options_for(f: Finding, s: GraphSnapshot) -> list[dict[str, Any]]:
    d = f.details
    if f.scenario == "A":
        sharing = d["workloads_sharing_identity"]
        others = [w for w in sharing if w != f.primary_resource["id"]]
        vpc_instances = sorted(i for i, w in s.workloads.items() if w["project_id"] == f.primary_resource["project_id"])
        return [
            _option("A1", "Close the internet-facing management/data port",
                    [f"Restrict or delete firewall rule(s) {', '.join(d['firewall_ids'])} (ports {d['ports']}); "
                     "allow administration only from IAP range 35.235.240.0/20"],
                    "low", False,
                    f"Operators switch to IAP for SSH. {len(vpc_instances)} instance(s) share the project network; "
                    "none lose application traffic if they only serve HTTPS.", [],
                    ["Re-run Cartography rule compute_instance_exposed: the instance no longer appears",
                     "Confirm IAP SSH works for the on-call group"],
                    "Re-create the deleted rule from its recorded configuration."),
            _option("A2", "Run the VM as a dedicated least-privilege identity",
                    [f"Create a dedicated service account for {f.primary_resource['name']} with only the roles it uses",
                     "Stop the VM, change its service account, start it"],
                    "medium", True,
                    "One VM restart in a maintenance window; every storage/API call of the application must be re-tested.",
                    [f.primary_resource["id"]],
                    ["Instance RUNS_AS points to the new identity", "Application smoke tests pass"],
                    "Switch the VM back to the previous identity (requires another restart)."),
            _option("A3", f"Remove the broad grants from {d['principal']}",
                    [f"Remove bindings {', '.join(d['binding_ids'])} and re-grant only bucket-level access needed"],
                    _disruption_for_workloads(len(others), "medium"), True,
                    (f"{len(sharing)} workload(s) run as this identity; all lose the removed permissions at once."
                     if len(sharing) > 1 else "Only this workload uses the identity; jobs relying on removed grants fail until re-granted."),
                    sharing,
                    ["Policy Analyzer / audit logs show no denied calls for 7 days",
                     "CAN_* edges from the identity drop to the intended resources after the next sync"],
                    "Re-add the removed bindings (keep an export of the policy before the change)."),
        ]
    if f.scenario == "B":
        workloads = d["workloads"]
        return [
            _option("B1", "Move production rights to a production-owned identity",
                    ["Create a deployer identity inside the production project(s)",
                     f"Grant it the needed subset of {', '.join(d['roles'])}",
                     "Let CI impersonate it only through a gated, audited workflow; then remove the cross-environment bindings"],
                    "medium", True, "CI pipelines need a new authentication step; production deploys pause until wired.",
                    workloads, ["No binding on production projects references a development identity",
                                "Re-run this investigation: scenario B no longer reported"],
                    "Re-add the previous bindings from the exported policy."),
            _option("B2", "Revoke the production grants now",
                    [f"Remove bindings {', '.join(d['binding_ids'])} from the development identity"],
                    "high", True, "Any CI job that touches production breaks immediately.", workloads,
                    ["Production CAN_* edges from the identity disappear after the next sync"],
                    "Re-add the bindings."),
            _option("B3", "Narrow the grants with IAM conditions while migrating",
                    ["Add resource-name and expiry conditions to the bindings until B1 lands"],
                    "low", False, "No immediate break; residual risk remains during the migration window.", [],
                    ["Bindings show has_condition=true"], "Remove the conditions."),
        ]
    if f.scenario == "C":
        return [
            _option("C1", "Remove the public binding and enforce public access prevention",
                    [f"Delete binding(s) {', '.join(d['public_binding_ids'])}", "Set publicAccessPrevention=enforced"],
                    "medium", True, "Anonymous links stop working; legitimate external sharing needs signed URLs.", [],
                    ["Cartography rule object_storage_public no longer reports the bucket",
                     "Anonymous GET on an object returns 403"],
                    "Re-add the binding and set public access prevention back to inherited."),
            _option("C2", "Serve exports through short-lived signed URLs",
                    ["Keep the bucket private; issue V4 signed URLs from the export service"],
                    "medium", True, "Application change in the export service; internal readers unaffected.", [],
                    ["Export links carry X-Goog-Signature and expire"], "Revert the application change."),
        ]
    if f.scenario == "D":
        instances = d["exposed_instance_ids"]
        return [
            _option("D1", "Restrict the source range to the intended partner CIDRs",
                    [f"Replace 0.0.0.0/0 on {d['firewall_name']} with the partner's published egress ranges"],
                    "low", True, "Partner keeps access if its ranges are correct; everyone else loses it (the intent).",
                    [], ["Rule compute_instance_exposed no longer lists the port for these instances"],
                    "Restore the previous source range."),
            _option("D2", "Remove the rule and provide private connectivity",
                    [f"Delete {d['firewall_name']}; connect the partner via VPN or Private Service Connect"],
                    "high", True, "Partner integration stops until the private path exists.", instances,
                    ["No INGRESS rule allows the port from 0.0.0.0/0"], "Re-create the rule."),
        ]
    if f.scenario == "E":
        workloads = d["workload_ids"]
        return [
            _option("E1", "Revoke the broad grants immediately",
                    [f"Remove bindings {', '.join(d['binding_ids'])}"],
                    _disruption_for_workloads(len(workloads)), True,
                    f"All {len(workloads)} workloads lose permissions simultaneously; high outage likelihood.",
                    workloads, ["No owner/editor binding remains on the identity"], "Re-add the bindings."),
            _option("E2", "Split into per-workload identities, then revoke",
                    ["Create one least-privilege identity per workload group",
                     "Migrate one workload at a time (VM restart in a maintenance window)",
                     "Remove the broad grants once no workload uses the shared identity"],
                    "low", True, "Outage risk limited to one workload per step; takes several change windows.",
                    workloads[:1], ["Each migrated workload RUNS_AS its own identity",
                                    "Shared identity has zero RUNS_AS edges before revocation"],
                    "Point the migrated workload back to the shared identity."),
            _option("E3", "Deny the most dangerous permissions first",
                    ["Apply an IAM deny policy for setIamPolicy and service-account key creation on the identity"],
                    "low", False, "Low impact unless automation changes IAM; must be checked against deploy tooling.",
                    [], ["Attempted setIamPolicy by the identity is denied"], "Delete the deny policy."),
        ]
    return []


def assess(f: Finding, s: GraphSnapshot) -> dict[str, Any]:
    opts = options_for(f, s)
    closing = [o for o in opts if o["closes_exposure"]]
    ranked = sorted(closing or opts, key=lambda o: (scoring.DISRUPTION_RANK[o["disruption"]], o["id"]))
    recommended = ranked[0] if ranked else None
    alternative = ranked[1] if len(ranked) > 1 else next((o for o in opts if o is not recommended), None)
    trade_offs = [f"{o['id']} ({o['disruption']} disruption, {'closes' if o['closes_exposure'] else 'reduces'} "
                  f"the exposure): {o['operational_consequences']}" for o in opts]
    confidence = {"verified_fact": "high", "potential_exposure": "medium",
                  "unverified_hypothesis": "low"}[f.classification]
    return {
        "finding_id": f.finding_id, "scenario": f.scenario, "title": f.title, "classification": f.classification,
        "risk_score": f.risk_score, "severity": f.severity,
        "problem": " ".join(f.facts),
        "affected_resources": f.affected_resource_ids,
        "evidence_paths": f.graph_paths[:10],
        "potential_impact": _impact(f),
        "recommended_mitigation": recommended, "alternative_mitigation": alternative, "options": opts,
        "trade_offs": trade_offs,
        "validation_steps": (recommended or {}).get("validation_steps", []),
        "rollback": (recommended or {}).get("rollback"),
        "confidence": confidence, "missing_information": f.missing_evidence,
        "priority_score": scoring.priority_score(f.risk_score, (recommended or {}).get("disruption", "medium")),
        "executes_changes": False,
    }


def _impact(f: Finding) -> str:
    d = f.details
    if f.scenario == "A":
        return (f"Code execution on {f.primary_resource['name']} would yield {d['principal']}'s permissions: "
                f"{len(d['reachable_resource_ids'])} reachable resource(s) across {', '.join(d['granted_projects'])}.")
    if f.scenario == "B":
        return (f"Compromise of the {d['home_environment']} identity could reach {len(d['reachable_resource_ids'])} "
                f"production resource(s)" + (" and impersonate IAM-admin identities." if d["privileged_impersonation_ids"] else "."))
    if f.scenario == "C":
        return f"Unauthenticated users could read objects of a {d['classification']} bucket."
    if f.scenario == "D":
        return f"Internet sources could reach tcp {d['ports']} on {len(d['exposed_instance_ids'])} instance(s)."
    if f.scenario == "E":
        return (f"Compromise of any of {len(d['workload_ids'])} workloads yields {', '.join(d['roles'])} on "
                f"{', '.join(d['granted_projects'])}.")
    return ""


def plan(findings: list[Finding], s: GraphSnapshot) -> dict[str, Any]:
    assessments = sorted((assess(f, s) for f in findings), key=lambda a: (-a["priority_score"], a["finding_id"]))
    phases = {"now": [], "next": [], "later": []}
    for a in assessments:
        disruption = (a["recommended_mitigation"] or {}).get("disruption", "medium")
        if a["risk_score"] >= 55 and disruption == "low":
            phases["now"].append(a["finding_id"])
        elif a["risk_score"] >= 55 or (a["risk_score"] >= 40 and disruption == "low"):
            phases["next"].append(a["finding_id"])
        else:
            phases["later"].append(a["finding_id"])
    return {"items": assessments, "phases": phases,
            "notes": ["Ordering: priority_score = risk_score - 3 x (disruption rank of the recommended option - 1)",
                      "The platform never applies these changes; each step lists validation and rollback."]}
