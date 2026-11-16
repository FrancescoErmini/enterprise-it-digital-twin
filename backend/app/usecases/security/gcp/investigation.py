"""Investigation of compromise scenarios over the live graph.

Exposure signals come from Cartography's rules engine (internet-exposed
instances, public buckets, privileged principals). The graph is then traversed
for what the rules do not cover: which identity a workload runs as, what that
identity can reach, which environment each project belongs to, and how many
workloads share an identity. Five detectors map to the scenario families:

  A public_workload_privileged_identity
  B cross_environment_privilege
  C public_sensitive_storage
  D permissive_network_sensitive_service
  E shared_privileged_identity
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from app.technology_graph.client import GraphClient
from app.usecases.security.gcp import graph_queries as Q
from app.usecases.security.gcp import scoring
from app.usecases.security.gcp.rules_adapter import NormalizedFinding, RulesAdapter

PRIVILEGE_RULES = ("identity_administration_privileges", "delegation_boundary_modifiable",
                   "policy_administration_privileges")
EXPOSED_INSTANCE_FACT = "gcp_instance_internet_exposed"
PUBLIC_BUCKET_FACT = "gcp_bucket_public"
REMOTE_ADMIN_PORTS = {22, 3389}
DATA_SERVICE_PORTS = {3306, 5432, 6379, 9200, 27017}
SENSITIVE_CLASSES = {"restricted", "confidential"}
SHARED_IDENTITY_MIN_WORKLOADS = 3
PRIVILEGED_SCOPE_THRESHOLD = 0.6

DETECTORS = {
    "A": "public_workload_privileged_identity",
    "B": "cross_environment_privilege",
    "C": "public_sensitive_storage",
    "D": "permissive_network_sensitive_service",
    "E": "shared_privileged_identity",
}


@dataclass
class Finding:
    finding_id: str
    scenario: str
    detector: str
    title: str
    classification: str
    primary_resource: dict[str, Any]
    affected_resource_ids: list[str]
    graph_paths: list[list[str]]
    facts: list[str]
    factors: dict[str, float]
    missing_evidence: list[str]
    rule_refs: list[str]
    details: dict[str, Any] = field(default_factory=dict)
    risk_score: float = 0.0
    severity: str = "low"

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


class GraphSnapshot:
    """Everything the detectors need, read once per investigation (a handful of queries)."""

    def __init__(self, client: GraphClient, rules: RulesAdapter, schema) -> None:
        self.cypher: list[str] = []
        read = lambda name, **params: self._read(client, name, params)  # noqa: E731
        self.env_by_project = {r["project_id"]: r["folder_name"] for r in read("project_environments")}
        self.class_by_resource = {r["resource_id"]: (r["classification"], r["label_id"])
                                  for r in read("classifications")}
        self.project_by_resource = {r["resource_id"]: r["project_id"] for r in read("resource_projects")}
        self.principals = {r["principal_id"]: r for r in read("principals")}
        self.grants: dict[str, list[dict]] = defaultdict(list)
        for r in read("principal_grants"):
            self.grants[r["principal_id"]].append(r)
        self.reach: dict[str, list[dict]] = defaultdict(list)
        for r in read("principal_reach"):
            self.reach[r["principal_id"]].append(r)
        self.workloads = {r["instance_id"]: r for r in read("workloads")}
        self.workloads_by_principal: dict[str, list[str]] = defaultdict(list)
        for iid, w in sorted(self.workloads.items()):
            if w["principal_id"]:
                self.workloads_by_principal[w["principal_id"]].append(iid)
        self.firewall_rules: dict[str, list[dict]] = defaultdict(list)
        self.firewall_by_name: dict[tuple[str, str], str] = {}
        for r in read("firewall_rules"):
            self.firewall_rules[r["firewall_id"]].append(r)
            self.firewall_by_name[(r["project_id"], r["firewall_name"])] = r["firewall_id"]
        self.public_bindings: dict[str, list[dict]] = defaultdict(list)
        for r in read("bucket_public_bindings"):
            self.public_bindings[r["bucket_id"]].append(r)

        run = rules.run(schema)
        self.rule_findings: list[NormalizedFinding] = run["findings"]
        self.rules_executed = run["executed"]
        self.by_fact: dict[str, list[NormalizedFinding]] = defaultdict(list)
        for f in self.rule_findings:
            self.by_fact[f.fact_id].append(f)
        self.privileged_by_rule: dict[str, set[str]] = defaultdict(set)
        for f in self.rule_findings:
            if f.rule_id in PRIVILEGE_RULES:
                pid = f.evidence.get("principal_identifier")
                if pid:
                    self.privileged_by_rule[str(pid)].add(f.rule_id)

    def _read(self, client: GraphClient, name: str, params: dict) -> list[dict]:
        cypher = Q.ALL_QUERIES[name]
        self.cypher.append(cypher.strip())
        return client.read(cypher, params, max_rows=20000).rows

    # -- helpers -----------------------------------------------------------------------
    def environment(self, project_id: str | None) -> str | None:
        return self.env_by_project.get(project_id or "")

    def classification(self, resource_id: str) -> str | None:
        entry = self.class_by_resource.get(resource_id)
        return entry[0] if entry else None

    def resource_sensitivity(self, resource_id: str, project_id: str | None = None) -> float:
        project = project_id or self.project_by_resource.get(resource_id)
        return scoring.sensitivity(self.classification(resource_id), self.environment(project))

    def principal_permissions(self, principal_id: str, scope_projects: set[str] | None = None) -> set[str]:
        perms: set[str] = set()
        for g in self.grants.get(principal_id, []):
            if scope_projects is not None and self.grant_project(g) not in scope_projects:
                continue
            perms.update(g["permissions"] or [])
        return perms

    @staticmethod
    def grant_project(grant: dict) -> str | None:
        labels = grant["scope_labels"] or []
        if "GCPProject" in labels:
            return grant["scope_id"]
        return grant["scope_project_id"]

    def principal_name(self, principal_id: str) -> str:
        return (self.principals.get(principal_id) or {}).get("email") or principal_id

    def reach_targets(self, principal_id: str) -> list[dict]:
        return self.reach.get(principal_id, [])


class Investigator:
    def __init__(self, snapshot: GraphSnapshot) -> None:
        self.s = snapshot

    # -- shared building blocks ----------------------------------------------------------
    def _identity_profile(self, principal_id: str, scope_projects: set[str] | None = None) -> dict[str, Any]:
        s = self.s
        perms = s.principal_permissions(principal_id, scope_projects)
        scope_value, tier = scoring.privilege_scope(perms)
        grants = [g for g in s.grants.get(principal_id, [])
                  if scope_projects is None or s.grant_project(g) in scope_projects]
        reach = [r for r in s.reach_targets(principal_id)
                 if scope_projects is None or r["target_project_id"] in scope_projects]
        targets = sorted({r["target_id"] for r in reach})
        projects = sorted({s.grant_project(g) for g in grants if s.grant_project(g)})
        sens = max([s.resource_sensitivity(t, None) for t in targets] or [0.0])
        return {"permissions": sorted(perms), "privilege_scope": scope_value, "privilege_tier": tier,
                "grants": grants, "binding_ids": sorted({g["binding_id"] for g in grants}),
                "roles": sorted({g["role"] for g in grants}), "reach": reach, "targets": targets,
                "projects": projects, "max_target_sensitivity": sens,
                "rules": sorted(s.privileged_by_rule.get(principal_id, set()))}

    def _is_privileged(self, principal_id: str) -> bool:
        profile = self._identity_profile(principal_id)
        return bool(self.s.privileged_by_rule.get(principal_id)) or profile["privilege_scope"] >= PRIVILEGED_SCOPE_THRESHOLD

    def _exposed_instances(self) -> dict[str, dict[str, Any]]:
        exposed: dict[str, dict[str, Any]] = {}
        for f in self.s.by_fact.get(EXPOSED_INSTANCE_FACT, []):
            iid = f.resource_id
            if not iid:
                continue
            entry = exposed.setdefault(iid, {"ports": set(), "firewalls": set(), "project": f.evidence.get("account_id")})
            entry["ports"].add(int(f.evidence.get("port")))
            fw = self.s.firewall_by_name.get((entry["project"], f.evidence.get("security_group")))
            if fw:
                entry["firewalls"].add((fw, int(f.evidence.get("port"))))
        return exposed

    def _open_rule_ids(self, firewall_id: str, port: int) -> list[dict]:
        rules = []
        for r in self.s.firewall_rules.get(firewall_id, []):
            if r["disabled"] or r["direction"] != "INGRESS":
                continue
            if not set(r["source_ranges"]) & {"0.0.0.0/0", "::/0"}:
                continue
            if r["protocol"] == "all" or (r["protocol"] == "tcp" and (r["from_port"] or 0) <= port <= (r["to_port"] or r["from_port"] or 0)):
                rules.append(r)
        return rules

    def _finalize(self, f: Finding) -> Finding:
        f.affected_resource_ids = sorted(set(f.affected_resource_ids))
        f.risk_score = scoring.risk_score(f.factors, f.classification)
        f.severity = scoring.severity(f.risk_score)
        return f

    # -- A ----------------------------------------------------------------------------------
    def detect_public_workload_privileged_identity(self) -> list[Finding]:
        s, out = self.s, []
        for iid, exp in sorted(self._exposed_instances().items()):
            w = s.workloads.get(iid)
            if not w or not w["principal_id"] or not self._is_privileged(w["principal_id"]):
                continue
            pid = w["principal_id"]
            prof = self._identity_profile(pid)
            env = s.environment(w["project_id"])
            firewalls = sorted({fw for fw, port in exp["firewalls"] if port in REMOTE_ADMIN_PORTS | DATA_SERVICE_PORTS})
            rule_ids = sorted({r["rule_id"] for fw, port in exp["firewalls"] for r in self._open_rule_ids(fw, port)})
            path = [iid, *w["nic_ids"], *w["access_config_ids"], *firewalls, pid, *prof["binding_ids"]]
            affected = [iid, pid, *prof["targets"], *prof["projects"]]
            sens = max(s.resource_sensitivity(iid, w["project_id"]), prof["max_target_sensitivity"])
            out.append(self._finalize(Finding(
                finding_id=f"A::{iid}", scenario="A", detector=DETECTORS["A"],
                title=f"Internet-exposed {env or 'unknown-environment'} VM {w['name']} runs as privileged identity {s.principal_name(pid)}",
                classification="potential_exposure",
                primary_resource={"label": "GCPInstance", "id": iid, "name": w["name"], "project_id": w["project_id"],
                                  "environment": env},
                affected_resource_ids=affected,
                graph_paths=[path] + [[iid, pid, r["target_id"]] for r in prof["reach"][:20]],
                facts=[f"{w['name']} has public IP(s) {', '.join(ip for ip in w['public_ips'] if ip)}",
                       f"Ports reachable from 0.0.0.0/0 per Cartography rule compute_instance_exposed: {sorted(exp['ports'])}",
                       f"Runs as {s.principal_name(pid)} holding roles {', '.join(prof['roles'])} on {', '.join(prof['projects'])}",
                       f"Identity reaches {len(prof['targets'])} resource(s) via CAN_* permission edges"],
                factors={"exposure": 1.0, "privilege_scope": prof["privilege_scope"], "resource_sensitivity": sens,
                         "blast_radius": scoring.capped(len(prof["targets"]) + len(prof["projects"]), scoring.BLAST_RADIUS_CAP),
                         "affected_resources": scoring.capped(len(set(affected)), scoring.AFFECTED_CAP),
                         "operational_impact": scoring.capped(len(s.workloads_by_principal.get(pid, [])), scoring.WORKLOAD_CAP)},
                missing_evidence=["Whether a service actually listens on the exposed ports (runtime state is not in the graph)",
                                  "Host-level controls (OS Login enforcement, IAP, host firewall) beyond VPC firewall rules",
                                  "Software vulnerabilities on the VM",
                                  "Firewall target tags are not evaluated by the upstream exposure rule (VPC-wide evaluation)"],
                rule_refs=sorted({"compute_instance_exposed", *prof["rules"]}),
                details={"principal_id": pid, "principal": s.principal_name(pid), "ports": sorted(exp["ports"]),
                         "firewall_ids": firewalls, "ip_rule_ids": rule_ids, "roles": prof["roles"],
                         "binding_ids": prof["binding_ids"], "privilege_tier": prof["privilege_tier"],
                         "reachable_resource_ids": prof["targets"], "granted_projects": prof["projects"],
                         "workloads_sharing_identity": s.workloads_by_principal.get(pid, [])})))
        return out

    # -- B ----------------------------------------------------------------------------------
    def detect_cross_environment_privilege(self) -> list[Finding]:
        s, out = self.s, []
        prod_rank = scoring.ENVIRONMENT_RANK["Production"]
        for pid, principal in sorted(s.principals.items()):
            home_env = s.environment(principal["home_project_id"])
            home_rank = scoring.ENVIRONMENT_RANK.get(home_env or "", prod_rank)
            if home_rank >= prod_rank:
                continue
            prod_projects = {p for p, env in s.env_by_project.items() if env == "Production"}
            prof = self._identity_profile(pid, scope_projects=prod_projects)
            if not prof["grants"] and not prof["reach"]:
                continue
            workloads = s.workloads_by_principal.get(pid, [])
            exposed = sorted(set(workloads) & set(self._exposed_instances()))
            impersonable = sorted({r["target_id"] for r in prof["reach"] if r["access"] == "CAN_IMPERSONATE"})
            privileged_targets = [t for t in impersonable if self._identity_profile(t)["privilege_scope"] >= 1.0]
            path = [principal["home_project_id"], pid, *prof["binding_ids"], *prof["projects"]]
            affected = [pid, *prof["targets"], *prof["projects"], *exposed]
            facts = [f"{s.principal_name(pid)} belongs to {principal['home_project_id']} ({home_env})",
                     f"Holds {', '.join(prof['roles'])} on production project(s) {', '.join(prof['projects'])}",
                     f"Reaches {len(prof['targets'])} production resource(s) via CAN_* edges"]
            if impersonable:
                facts.append(f"Can impersonate {len(impersonable)} production identit(ies): "
                             + ", ".join(s.principal_name(t) for t in impersonable))
            if privileged_targets:
                facts.append("Impersonation reaches identities with IAM-admin capability: "
                             + ", ".join(s.principal_name(t) for t in privileged_targets))
            if exposed:
                facts.append(f"Runs on internet-exposed workload(s): {', '.join(exposed)}")
            out.append(self._finalize(Finding(
                finding_id=f"B::{pid}", scenario="B", detector=DETECTORS["B"],
                title=f"{home_env} identity {s.principal_name(pid)} holds privileges on production",
                classification="potential_exposure",
                primary_resource={"label": "GCPServiceAccount", "id": pid, "name": s.principal_name(pid),
                                  "project_id": principal["home_project_id"], "environment": home_env},
                affected_resource_ids=affected,
                graph_paths=[path] + [[pid, r["target_id"]] for r in prof["reach"][:20]]
                + [[w, pid] for w in workloads],
                facts=facts,
                factors={"exposure": 1.0 if exposed else 0.6,
                         "privilege_scope": 1.0 if privileged_targets else prof["privilege_scope"],
                         "resource_sensitivity": prof["max_target_sensitivity"] or scoring.sensitivity(None, "Production"),
                         "blast_radius": scoring.capped(len(prof["targets"]) + len(prof["projects"]), scoring.BLAST_RADIUS_CAP),
                         "affected_resources": scoring.capped(len(set(affected)), scoring.AFFECTED_CAP),
                         "operational_impact": scoring.capped(len(workloads), scoring.WORKLOAD_CAP)},
                missing_evidence=["Service-account keys held outside Google Cloud (not collected in this dataset)",
                                  "Who can run jobs on the identity's workloads (CI configuration is not in the graph)",
                                  "IAM conditions / VPC Service Controls evaluated at request time"],
                rule_refs=sorted(set(prof["rules"]) | set(s.privileged_by_rule.get(pid, set()))),
                details={"home_project_id": principal["home_project_id"], "home_environment": home_env,
                         "production_projects": prof["projects"], "roles": prof["roles"],
                         "binding_ids": prof["binding_ids"], "reachable_resource_ids": prof["targets"],
                         "impersonable_identity_ids": impersonable, "privileged_impersonation_ids": privileged_targets,
                         "exposed_workloads": exposed, "workloads": workloads})))
        return out

    # -- C ----------------------------------------------------------------------------------
    def detect_public_sensitive_storage(self) -> list[Finding]:
        s, out = self.s, []
        for f in sorted(s.by_fact.get(PUBLIC_BUCKET_FACT, []), key=lambda x: x.resource_id or ""):
            bid = f.resource_id
            cls = s.classification(bid)
            if cls not in SENSITIVE_CLASSES:
                continue
            project = s.project_by_resource.get(bid)
            bindings = [b for b in s.public_bindings.get(bid, []) if not b["has_condition"]]
            perms = {p for b in bindings for p in (b["permissions"] or [])}
            scope_value, tier = scoring.privilege_scope(perms)
            label_id = s.class_by_resource.get(bid, (None, None))[1]
            affected = [bid, *[b["binding_id"] for b in bindings], *([label_id] if label_id else [])]
            out.append(self._finalize(Finding(
                finding_id=f"C::{bid}", scenario="C", detector=DETECTORS["C"],
                title=f"{cls.capitalize()} bucket {bid} is granted to the public",
                classification="potential_exposure",
                primary_resource={"label": "GCPBucket", "id": bid, "name": bid, "project_id": project,
                                  "environment": s.environment(project)},
                affected_resource_ids=affected,
                graph_paths=[[b["binding_id"], bid] for b in bindings],
                facts=[f"Cartography rule object_storage_public flags {bid}",
                       f"Public binding(s): {', '.join(b['role'] + ' -> allUsers/allAuthenticatedUsers' for b in bindings)}",
                       f"Public access prevention: {bindings[0]['public_access_prevention'] if bindings else 'unknown'}",
                       f"Labelled data-classification={cls}"],
                factors={"exposure": 1.0, "privilege_scope": scope_value,
                         "resource_sensitivity": s.resource_sensitivity(bid, project),
                         "blast_radius": scoring.capped(1, scoring.BLAST_RADIUS_CAP),
                         "affected_resources": scoring.capped(len(affected), scoring.AFFECTED_CAP),
                         "operational_impact": 0.0},
                missing_evidence=["Which objects the bucket holds and whether they contain customer data",
                                  "Organisation policies enforcing public access prevention above the project",
                                  "Data-access logs showing whether anonymous reads occurred"],
                rule_refs=["object_storage_public"],
                details={"public_binding_ids": [b["binding_id"] for b in bindings], "public_roles":
                         sorted({b["role"] for b in bindings}), "privilege_tier": tier, "classification": cls,
                         "public_access_prevention": bindings[0]["public_access_prevention"] if bindings else None})))
        return out

    # -- D ----------------------------------------------------------------------------------
    def detect_permissive_network_sensitive_service(self) -> list[Finding]:
        s = self.s
        grouped: dict[str, dict[str, Any]] = {}
        for iid, exp in self._exposed_instances().items():
            for fw, port in exp["firewalls"]:
                if port not in DATA_SERVICE_PORTS:
                    continue
                g = grouped.setdefault(fw, {"instances": set(), "ports": set()})
                g["instances"].add(iid)
                g["ports"].add(port)
        out = []
        for fw, g in sorted(grouped.items()):
            instances = sorted(g["instances"])
            project = s.project_by_resource.get(fw)
            rules = sorted({r["rule_id"] for port in g["ports"] for r in self._open_rule_ids(fw, port)})
            sens = max(s.resource_sensitivity(i) for i in instances)
            principals = sorted({s.workloads[i]["principal_id"] for i in instances if s.workloads[i]["principal_id"]})
            scope_value = max([self._identity_profile(p)["privilege_scope"] for p in principals] or [scoring.BASELINE_PRIVILEGE])
            controls = sorted(fid for fid, rows in s.firewall_rules.items() if fid != fw
                              and s.project_by_resource.get(fid) == project
                              and any(r["protocol"] == "tcp" and (r["from_port"] or 0) <= p <= (r["to_port"] or 0)
                                      for r in rows for p in g["ports"]))
            name = s.firewall_rules[fw][0]["firewall_name"]
            affected = [fw, *rules, "0.0.0.0/0", *instances]
            out.append(self._finalize(Finding(
                finding_id=f"D::{fw}", scenario="D", detector=DETECTORS["D"],
                title=f"Firewall {name} exposes data-service port(s) {sorted(g['ports'])} to the internet",
                classification="potential_exposure",
                primary_resource={"label": "GCPFirewall", "id": fw, "name": name, "project_id": project,
                                  "environment": s.environment(project)},
                affected_resource_ids=affected,
                graph_paths=[["0.0.0.0/0", r, fw, i] for r in rules for i in instances],
                facts=[f"INGRESS rule {name} allows tcp {sorted(g['ports'])} from 0.0.0.0/0",
                       f"Internet-reachable instance(s) on that network (rule compute_instance_exposed): {', '.join(instances)}",
                       *([f"Other rules on this network allow the same port from narrower ranges: {', '.join(controls)}"] if controls else [])],
                factors={"exposure": 1.0, "privilege_scope": scope_value, "resource_sensitivity": sens,
                         "blast_radius": scoring.capped(len(instances), scoring.BLAST_RADIUS_CAP),
                         "affected_resources": scoring.capped(len(affected), scoring.AFFECTED_CAP),
                         "operational_impact": scoring.capped(len(instances), scoring.WORKLOAD_CAP)},
                missing_evidence=["Whether anything listens on the exposed port on each instance",
                                  "Whether the rule's target tags limit it to fewer instances (upstream evaluation is VPC-wide)",
                                  "Authentication and TLS settings of the exposed service"],
                rule_refs=["compute_instance_exposed"],
                details={"firewall_name": name, "ports": sorted(g["ports"]), "ip_rule_ids": rules,
                         "exposed_instance_ids": instances, "narrower_rules_same_port": controls,
                         "principal_ids": principals})))
        return out

    # -- E ----------------------------------------------------------------------------------
    def detect_shared_privileged_identity(self) -> list[Finding]:
        s, out = self.s, []
        exposed_all = set(self._exposed_instances())
        for pid, workloads in sorted(s.workloads_by_principal.items()):
            if len(workloads) < SHARED_IDENTITY_MIN_WORKLOADS:
                continue
            prof = self._identity_profile(pid)
            if prof["privilege_scope"] < PRIVILEGED_SCOPE_THRESHOLD:
                continue
            projects = sorted({s.workloads[w]["project_id"] for w in workloads})
            exposed = sorted(set(workloads) & exposed_all)
            sens = max([s.resource_sensitivity(w) for w in workloads] + [prof["max_target_sensitivity"]])
            affected = [pid, *workloads, *prof["projects"]]
            out.append(self._finalize(Finding(
                finding_id=f"E::{pid}", scenario="E", detector=DETECTORS["E"],
                title=f"{len(workloads)} workloads across {len(projects)} project(s) share privileged identity {s.principal_name(pid)}",
                classification="verified_fact",
                primary_resource={"label": "GCPServiceAccount", "id": pid, "name": s.principal_name(pid),
                                  "project_id": s.principals.get(pid, {}).get("home_project_id"),
                                  "environment": s.environment(s.principals.get(pid, {}).get("home_project_id"))},
                affected_resource_ids=affected,
                graph_paths=[[w, pid] for w in workloads] + [[pid, b] for b in prof["binding_ids"]],
                facts=[f"{len(workloads)} instances RUNS_AS {s.principal_name(pid)} in {', '.join(projects)}",
                       f"The identity holds {', '.join(prof['roles'])} on {', '.join(prof['projects'])} (tier: {prof['privilege_tier']})",
                       "Removing those grants in one change would affect every listed workload at once"],
                factors={"exposure": 1.0 if exposed else 0.4, "privilege_scope": prof["privilege_scope"],
                         "resource_sensitivity": sens,
                         "blast_radius": scoring.capped(len(prof["targets"]) + len(prof["projects"]), scoring.BLAST_RADIUS_CAP),
                         "affected_resources": scoring.capped(len(set(affected)), scoring.AFFECTED_CAP),
                         "operational_impact": scoring.capped(len(workloads), scoring.WORKLOAD_CAP)},
                missing_evidence=["Which permissions each workload actually uses (audit-log or recommender data is not in the graph)",
                                  "Deployment dependencies between the workloads"],
                rule_refs=sorted(prof["rules"]),
                details={"workload_ids": workloads, "workload_projects": projects, "roles": prof["roles"],
                         "binding_ids": prof["binding_ids"], "granted_projects": prof["projects"],
                         "exposed_workloads": exposed, "privilege_tier": prof["privilege_tier"]})))
        return out

    def run(self, scenarios: list[str] | None = None) -> list[Finding]:
        detectors = {
            "A": self.detect_public_workload_privileged_identity, "B": self.detect_cross_environment_privilege,
            "C": self.detect_public_sensitive_storage, "D": self.detect_permissive_network_sensitive_service,
            "E": self.detect_shared_privileged_identity,
        }
        findings: list[Finding] = []
        for key in sorted(detectors):
            if scenarios and key not in scenarios and DETECTORS[key] not in scenarios:
                continue
            findings.extend(detectors[key]())
        return sorted(findings, key=lambda f: (-f.risk_score, f.finding_id))
