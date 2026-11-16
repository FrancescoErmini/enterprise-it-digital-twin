"""Schema conformance against the Cartography package actually installed (not against documentation)."""

from tests.cartography_schema import CARTOGRAPHY_MANAGED_PROPERTIES, ONTOLOGY_PROPERTY_PREFIX, declared_schema


def test_every_label_is_declared_by_cartography(platform):
    declared = declared_schema()
    schema = platform.schema.get(refresh=True)
    unknown = sorted(lbl for lbl in schema.labels if lbl not in declared.labels)
    assert not unknown, f"Labels not declared by any installed Cartography model: {unknown}"


def test_every_relationship_type_is_declared_by_cartography(platform):
    declared = declared_schema()
    schema = platform.schema.get()
    unknown = sorted(t for t in schema.relationship_types if t not in declared.rel_types)
    assert not unknown, f"Relationship types not declared by Cartography: {unknown}"


def test_relationship_directions_match_cartography(client):
    """Every (label)-[type]->(label) edge must match a declared pattern, in the declared direction."""
    declared = declared_schema()
    rows = client.read("MATCH (a)-[r]->(b) RETURN DISTINCT labels(a) AS la, type(r) AS t, labels(b) AS lb",
                       max_rows=5000).rows
    violations = []
    for row in rows:
        ok = any((src, row["t"], dst) in declared.patterns for src in row["la"] for dst in row["lb"])
        if not ok:
            violations.append((row["la"], row["t"], row["lb"]))
    assert not violations, f"Edges with no matching Cartography declaration/direction: {violations}"


def test_node_properties_are_declared(client):
    declared = declared_schema()
    rows = client.read("MATCH (n) UNWIND keys(n) AS k RETURN DISTINCT labels(n) AS labels, k AS prop",
                       max_rows=20000).rows
    violations = []
    for row in rows:
        prop = row["prop"]
        if prop in CARTOGRAPHY_MANAGED_PROPERTIES or prop.startswith(ONTOLOGY_PROPERTY_PREFIX):
            continue
        if not any(prop in declared.properties_by_label.get(lbl, set()) for lbl in row["labels"]):
            violations.append((row["labels"], prop))
    assert not violations, f"Properties not declared by Cartography for their labels: {violations}"


def test_id_formats_follow_cartography(client):
    checks = {
        "MATCH (n:GCPInstance) RETURN n.id AS id": r"^projects/[\w-]+/zones/[\w-]+/instances/[\w-]+$",
        "MATCH (n:GCPFirewall) RETURN n.id AS id": r"^projects/[\w-]+/global/firewalls/[\w-]+$",
        "MATCH (n:GCPVpc) RETURN n.id AS id": r"^projects/[\w-]+/global/networks/[\w-]+$",
        "MATCH (n:GCPFolder) RETURN n.id AS id": r"^folders/\d+$",
        "MATCH (n:GCPOrganization) RETURN n.id AS id": r"^organizations/\d+$",
        "MATCH (n:GCPServiceAccount) RETURN n.id AS id": r"^\d+$",
        "MATCH (n:GCPCloudSQLInstance) RETURN n.id AS id": r"^https://sqladmin\.googleapis\.com/sql/v1beta4/projects/",
        "MATCH (n:GCPPolicyBinding) RETURN n.id AS id": r"^//[\w.]+\.googleapis\.com/.+_roles/[\w.]+$",
    }
    import re

    for cypher, pattern in checks.items():
        ids = [r["id"] for r in client.read(cypher).rows]
        assert ids, cypher
        bad = [i for i in ids if not re.match(pattern, i)]
        assert not bad, f"{cypher}: unexpected id format {bad[:3]}"
