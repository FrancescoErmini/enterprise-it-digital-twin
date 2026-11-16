"""Graph integrity: expected counts, no orphans, documented schema, scenario ids present."""

import re

from tests.conftest import DOCS_DIR

EXPECTED_NODE_COUNTS = {
    "GCPOrganization": 1, "GCPFolder": 3, "GCPProject": 7, "GCPInstance": 32, "GCPServiceAccount": 18,
    "GCPBucket": 10, "GCPCloudSQLInstance": 4, "GCPVpc": 7, "GCPSubnet": 9, "GCPFirewall": 13,
    "GCPNetworkInterface": 32, "GCPNicAccessConfig": 6, "GCPRole": 16, "GCPPolicyBinding": 28,
}


def _count(client, label):
    return client.read(f"MATCH (n:`{label}`) RETURN count(n) AS c").rows[0]["c"]


def test_expected_node_counts(client):
    actual = {label: _count(client, label) for label in EXPECTED_NODE_COUNTS}
    assert actual == EXPECTED_NODE_COUNTS


def test_target_environment_shape(client):
    """Brief: 1 org, 3 folders, 6-8 projects, 25-40 instances, 15-25 service accounts."""
    assert 6 <= _count(client, "GCPProject") <= 8
    assert 25 <= _count(client, "GCPInstance") <= 40
    assert 15 <= _count(client, "GCPServiceAccount") <= 25


def test_expected_relationship_counts(client):
    rows = client.read("MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS c").rows
    counts = {r["t"]: r["c"] for r in rows}
    assert counts["RUNS_AS"] == 32
    assert counts["NETWORK_INTERFACE"] == 32
    assert counts["APPLIES_TO"] == 28
    assert counts["GRANTS_ROLE"] == 28
    assert counts["CAN_IMPERSONATE"] > 0 and counts["CAN_WRITE"] > 0


def test_no_orphan_nodes(client):
    rows = client.read("MATCH (n) WHERE NOT (n)--() RETURN labels(n) AS labels, n.id AS id LIMIT 20").rows
    assert rows == [], f"Orphan nodes: {rows}"


def test_every_project_belongs_to_a_folder(client):
    rows = client.read("MATCH (p:GCPProject) WHERE NOT (p)-[:PARENT]->(:GCPFolder) RETURN p.id AS id").rows
    assert rows == []


def test_schema_documented_in_schema_mapping(platform):
    doc = (DOCS_DIR / "schema-mapping.md").read_text()
    schema = platform.schema.get(refresh=True)
    undocumented_labels = [lbl for lbl in schema.labels if not re.search(rf"`{lbl}`", doc)]
    undocumented_rels = [r for r in schema.relationship_types if not re.search(rf"`{r}`|\[:{r}\]", doc)]
    assert not undocumented_labels, f"Labels missing from docs/schema-mapping.md: {undocumented_labels}"
    assert not undocumented_rels, f"Relationship types missing from docs/schema-mapping.md: {undocumented_rels}"
    gcp_props = {p for lbl, props in schema.node_properties.items() if lbl.startswith("GCP") for p in props}
    undocumented_props = sorted(p for p in gcp_props if not re.search(rf"`{re.escape(p)}`", doc))
    assert not undocumented_props, f"Properties missing from docs/schema-mapping.md: {undocumented_props}"


def test_scenario_spec_ids_exist(client, scenario_specs):
    for spec in scenario_specs:
        refs = [spec["primary_resource"], *spec["evidence"], *spec["controls"]]
        for ref in refs:
            c = client.read(f"MATCH (n:`{ref['label']}` {{id: $id}}) RETURN count(n) AS c", {"id": ref["id"]}).rows[0]["c"]
            assert c == 1, f"Scenario {spec['id']}: {ref['label']} {ref['id']} not in graph"
        assert len(spec["remediation_options"]) >= 2
        for opt in spec["remediation_options"]:
            assert opt["operational_consequences"]
        assert spec["missing_evidence"] and spec["expected_agent_behaviour"] and spec["concern"]
