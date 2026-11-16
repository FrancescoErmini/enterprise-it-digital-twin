"""Cypher owned by the GCP security use case.

Every query uses labels, relationship types and properties that exist in the
Cartography GCP schema (see docs/schema-mapping.md). They are validated against
the live graph at startup like any other query.
"""

PROJECT_ENVIRONMENTS = """
MATCH (p:GCPProject)
OPTIONAL MATCH (p)-[:PARENT]->(f:GCPFolder)
RETURN p.id AS project_id, f.id AS folder_id, f.displayname AS folder_name
ORDER BY project_id
"""

CLASSIFICATIONS = """
MATCH (n)-[:LABELED]->(l:GCPLabel)
WHERE l.key = 'data-classification'
RETURN n.id AS resource_id, l.value AS classification, l.id AS label_id
ORDER BY resource_id
"""

RESOURCE_PROJECTS = """
MATCH (p:GCPProject)-[:RESOURCE]->(n)
WHERE n:GCPInstance OR n:GCPBucket OR n:GCPServiceAccount OR n:GCPCloudSQLInstance OR n:GCPFirewall
RETURN n.id AS resource_id, p.id AS project_id, labels(n) AS labels
ORDER BY resource_id
"""

PRINCIPAL_GRANTS = """
MATCH (sa:GCPServiceAccount)-[:HAS_ALLOW_POLICY]->(b:GCPPolicyBinding)-[:GRANTS_ROLE]->(r:GCPRole)
OPTIONAL MATCH (b)-[:APPLIES_TO]->(scope)
OPTIONAL MATCH (scope_project:GCPProject)-[:RESOURCE]->(scope)
RETURN sa.id AS principal_id, b.id AS binding_id, r.name AS role, r.permissions AS permissions,
       scope.id AS scope_id, labels(scope) AS scope_labels, scope_project.id AS scope_project_id,
       coalesce(b.has_condition, false) AS has_condition
ORDER BY principal_id, binding_id
"""

PRINCIPALS = """
MATCH (home:GCPProject)-[:RESOURCE]->(sa:GCPServiceAccount)
RETURN sa.id AS principal_id, sa.email AS email, sa.display_name AS display_name, home.id AS home_project_id
ORDER BY principal_id
"""

PRINCIPAL_REACH = """
MATCH (sa:GCPServiceAccount)-[e:CAN_READ|CAN_WRITE|CAN_DELETE|CAN_IMPERSONATE]->(t)
OPTIONAL MATCH (tp:GCPProject)-[:RESOURCE]->(t)
RETURN sa.id AS principal_id, type(e) AS access, t.id AS target_id, labels(t) AS target_labels,
       tp.id AS target_project_id
ORDER BY principal_id, target_id, access
"""

WORKLOADS = """
MATCH (p:GCPProject)-[:RESOURCE]->(i:GCPInstance)
OPTIONAL MATCH (i)-[:RUNS_AS]->(sa:GCPServiceAccount)
OPTIONAL MATCH (i)-[:NETWORK_INTERFACE]->(nic:GCPNetworkInterface)
OPTIONAL MATCH (nic)-[:RESOURCE]->(ac:GCPNicAccessConfig)
RETURN i.id AS instance_id, i.instancename AS name, p.id AS project_id, sa.id AS principal_id,
       i.status AS status, collect(DISTINCT nic.id) AS nic_ids, collect(DISTINCT ac.id) AS access_config_ids,
       collect(DISTINCT ac.public_ip) AS public_ips
ORDER BY instance_id
"""

FIREWALL_RULES = """
MATCH (p:GCPProject)-[:RESOURCE]->(fw:GCPFirewall)<-[:ALLOWED_BY]-(rule:GCPIpRule)
OPTIONAL MATCH (rule)<-[:MEMBER_OF_IP_RULE]-(range:GCPIpRange)
OPTIONAL MATCH (vpc:GCPVpc)-[:RESOURCE]->(fw)
RETURN fw.id AS firewall_id, fw.name AS firewall_name, p.id AS project_id, vpc.id AS vpc_id,
       fw.direction AS direction, coalesce(fw.disabled, false) AS disabled, rule.id AS rule_id,
       rule.protocol AS protocol, rule.fromport AS from_port, rule.toport AS to_port,
       collect(DISTINCT range.range) AS source_ranges
ORDER BY firewall_id, rule_id
"""

BUCKET_PUBLIC_BINDINGS = """
MATCH (b:GCPBucket)<-[:APPLIES_TO]-(binding:GCPPolicyBinding)
WHERE binding.is_public = true
OPTIONAL MATCH (binding)-[:GRANTS_ROLE]->(r:GCPRole)
RETURN b.id AS bucket_id, binding.id AS binding_id, binding.role AS role, r.permissions AS permissions,
       b.iam_config_public_access_prevention AS public_access_prevention,
       coalesce(binding.has_condition, false) AS has_condition
ORDER BY bucket_id, binding_id
"""

RESOURCE_CONTEXT = """
MATCH (n {id: $resource_id})
OPTIONAL MATCH (n)-[r]-(m)
RETURN labels(n) AS labels, properties(n) AS properties,
       collect(DISTINCT {direction: CASE WHEN startNode(r) = n THEN 'out' ELSE 'in' END,
                         type: type(r), neighbor_id: m.id, neighbor_labels: labels(m)})[..150] AS neighbors
"""

OVERVIEW_COUNTS = """
MATCH (n)
WHERE n:GCPProject OR n:GCPInstance OR n:GCPServiceAccount OR n:GCPBucket OR n:GCPCloudSQLInstance
   OR n:GCPFirewall OR n:GCPVpc OR n:GCPSubnet OR n:GCPPolicyBinding OR n:GCPRole
RETURN [l IN labels(n) WHERE l STARTS WITH 'GCP'][0] AS label, count(*) AS c
ORDER BY label
"""

ALL_QUERIES = {
    "project_environments": PROJECT_ENVIRONMENTS, "classifications": CLASSIFICATIONS,
    "resource_projects": RESOURCE_PROJECTS, "principal_grants": PRINCIPAL_GRANTS, "principals": PRINCIPALS,
    "principal_reach": PRINCIPAL_REACH, "workloads": WORKLOADS, "firewall_rules": FIREWALL_RULES,
    "bucket_public_bindings": BUCKET_PUBLIC_BINDINGS, "resource_context": RESOURCE_CONTEXT,
    "overview_counts": OVERVIEW_COUNTS,
}
