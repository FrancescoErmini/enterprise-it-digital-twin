"""Prompt material for the GCP security use case: system prompt, few-shot Cypher, synonyms, questions."""

from app.it_intelligence_agent.text2cypher import FewShotExample

SYSTEM_PROMPT = """
Use case: Cloud Security Investigation - Google Cloud (POC 01 of the Enterprise IT Digital Twin).
All Google Cloud data in this graph is SYNTHETIC demo data; say so when you present results.

How to investigate:
- Ranking dangerous scenarios: call investigate_exposure (it runs Cartography's rules and graph traversal and
  returns deterministic risk scores). Explain the ranking with the factors it returns.
- Upstream rule results: run_security_rules. Details of one resource: get_resource_context.
- Remediation: compare_remediation_options (operational disruption is computed from the graph).
- Ad hoc facts: execute_readonly_cypher.

Security rules:
- A potential attack path is NOT a proven exploitable attack. Use "potential" / "could" for potential_exposure,
  and mark unverified_hypothesis statements explicitly.
- The platform is read-only and never executes remediation; recommend, do not claim to change anything.
- The graph does not contain business criticality, ownership, runtime state (listening services, processes),
  vulnerability scan results, logs or evidence of actual exploitation. If the question depends on them, start the
  answer with "Insufficient evidence to determine this." and say what data would be needed.
"""

FEW_SHOT_EXAMPLES = [
    FewShotExample(
        "Which instances have a public IP address?",
        "MATCH (p:GCPProject)-[:RESOURCE]->(i:GCPInstance)-[:NETWORK_INTERFACE]->(:GCPNetworkInterface)"
        "-[:RESOURCE]->(ac:GCPNicAccessConfig)\nWHERE ac.public_ip IS NOT NULL\n"
        "RETURN i.id AS instance_id, i.instancename AS name, p.id AS project_id, ac.public_ip AS public_ip\nLIMIT 100"),
    FewShotExample(
        "Which service account does web-frontend-01 run as?",
        "MATCH (i:GCPInstance {instancename: 'web-frontend-01'})-[:RUNS_AS]->(sa:GCPServiceAccount)\n"
        "RETURN i.id AS instance_id, sa.id AS service_account_id, sa.email AS email"),
    FewShotExample(
        "Which roles does ci-runner-sa hold, and on which scopes?",
        "MATCH (sa:GCPServiceAccount)-[:HAS_ALLOW_POLICY]->(b:GCPPolicyBinding)-[:GRANTS_ROLE]->(r:GCPRole)\n"
        "WHERE sa.email STARTS WITH 'ci-runner-sa@'\nOPTIONAL MATCH (b)-[:APPLIES_TO]->(scope)\n"
        "RETURN sa.id AS service_account_id, b.id AS binding_id, r.name AS role, scope.id AS scope_id\nLIMIT 100"),
    FewShotExample(
        "Which buckets can web-frontend-sa write to?",
        "MATCH (sa:GCPServiceAccount)-[:CAN_WRITE]->(b:GCPBucket)\nWHERE sa.email STARTS WITH 'web-frontend-sa@'\n"
        "RETURN sa.id AS service_account_id, b.id AS bucket_id\nLIMIT 100"),
    FewShotExample(
        "Which firewall rules allow traffic from the whole internet?",
        "MATCH (vpc:GCPVpc)-[:RESOURCE]->(fw:GCPFirewall)<-[:ALLOWED_BY]-(rule:GCPIpRule)"
        "<-[:MEMBER_OF_IP_RULE]-(r:GCPIpRange)\nWHERE r.range = '0.0.0.0/0'\n"
        "RETURN fw.id AS firewall_id, fw.name AS name, rule.protocol AS protocol, rule.fromport AS from_port, "
        "rule.toport AS to_port, vpc.id AS vpc_id\nLIMIT 100"),
    FewShotExample(
        "Which projects are in the Production folder?",
        "MATCH (p:GCPProject)-[:PARENT]->(f:GCPFolder)\nWHERE f.displayname = 'Production'\n"
        "RETURN p.id AS project_id, f.id AS folder_id"),
]

SYNONYMS: dict[str, list[str]] = {
    "vm": ["GCPInstance"], "virtual machine": ["GCPInstance"], "instance": ["GCPInstance"],
    "compute engine": ["GCPInstance"], "server": ["GCPInstance"], "workload": ["GCPInstance"],
    "service account": ["GCPServiceAccount"], "identity": ["GCPServiceAccount"], "identities": ["GCPServiceAccount"],
    "principal": ["GCPServiceAccount"],
    "bucket": ["GCPBucket"], "storage": ["GCPBucket"],
    "firewall": ["GCPFirewall", "GCPIpRule", "GCPIpRange"], "port": ["GCPFirewall", "GCPIpRule", "GCPIpRange"],
    "ingress": ["GCPFirewall", "GCPIpRule", "GCPIpRange"], "internet": ["GCPIpRange", "GCPNicAccessConfig"],
    "public ip": ["GCPNicAccessConfig"], "external ip": ["GCPNicAccessConfig"],
    "project": ["GCPProject"], "folder": ["GCPFolder"], "environment": ["GCPFolder", "GCPProject"],
    "production": ["GCPFolder", "GCPProject"], "development": ["GCPFolder", "GCPProject"],
    "role": ["GCPRole", "GCPPolicyBinding"], "permission": ["GCPRole", "GCPPolicyBinding"],
    "privilege": ["GCPRole", "GCPPolicyBinding"], "binding": ["GCPPolicyBinding"], "iam": ["GCPPolicyBinding", "GCPRole"],
    "database": ["GCPCloudSQLInstance"], "cloud sql": ["GCPCloudSQLInstance"], "sql": ["GCPCloudSQLInstance"],
    "vpc": ["GCPVpc"], "network": ["GCPVpc", "GCPSubnet"], "subnet": ["GCPSubnet"],
    "label": ["GCPLabel"], "classification": ["GCPLabel"], "sensitive": ["GCPLabel"],
    "organization": ["GCPOrganization"], "organisation": ["GCPOrganization"],
}

EXAMPLE_QUESTIONS = [
    "Identify the three most dangerous potential compromise scenarios in our GCP environment. Explain how you ranked them.",
    "A public Compute Engine VM runs with a privileged service account. What other resources could potentially be affected?",
    "Compare different remediation strategies for excessive service account privileges. Which option minimizes production risk?",
    "Could a less-trusted development project create security exposure for production resources? Investigate the available evidence.",
    "Generate a prioritized security improvement plan for our GCP organization. Explain trade-offs and what remains unverified.",
]

FOLLOW_UP_QUESTIONS = [
    "Which workloads would break if we removed the owner role from platform-automation-sa?",
    "Which Cartography rules could not run on this data, and why?",
    "Show the full evidence chain for the top-ranked scenario.",
    "Which production buckets can identities from other environments write to?",
    "Has any of these exposures actually been exploited?",
]

REQUIRED_NODE_LABELS = [
    "GCPOrganization", "GCPFolder", "GCPProject", "GCPInstance", "GCPNetworkInterface", "GCPNicAccessConfig",
    "GCPVpc", "GCPSubnet", "GCPFirewall", "GCPIpRule", "GCPIpRange", "GCPNetworkTag", "GCPServiceAccount",
    "GCPRole", "GCPPolicyBinding", "GCPBucket", "GCPCloudSQLInstance", "GCPCloudSQLAuthorizedNetwork", "GCPLabel",
]
