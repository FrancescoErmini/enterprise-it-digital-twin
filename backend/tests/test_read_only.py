"""Read-only enforcement: validator rejections AND refusals from the Neo4j server itself."""

import pytest

from app.technology_graph.client import GraphQueryError

WRITES = [
    "CREATE (n:GCPInstance {id: 'evil'}) RETURN n",
    "MATCH (n:GCPInstance) SET n.status = 'TERMINATED' RETURN count(n) AS c",
    "MATCH (n:GCPBucket) DETACH DELETE n",
    "MERGE (n:GCPProject {id: 'evil'}) RETURN n",
    "MATCH (n:GCPInstance) REMOVE n.status RETURN count(n) AS c",
]
SCHEMA_CHANGES = [
    "CREATE INDEX evil IF NOT EXISTS FOR (n:GCPInstance) ON (n.status)",
    "CREATE CONSTRAINT evil IF NOT EXISTS FOR (n:GCPInstance) REQUIRE n.id IS UNIQUE",
    "DROP INDEX evil IF EXISTS",
]
ADMIN_AND_PROCEDURES = [
    "CALL dbms.components()",
    "CALL dbms.security.listUsers()",
    "CALL db.createLabel('Evil')",
    "CALL apoc.cypher.runWrite('CREATE (n) RETURN n', {})",
    "CREATE USER evil SET PASSWORD 'xxxxxxxxxxxx'",
    "SHOW USERS",
    "LOAD CSV FROM 'http://example.com/x.csv' AS row RETURN row",
    "MATCH (n) RETURN n; MATCH (m) DETACH DELETE m",
    "MATCH (n) CALL (n) { DETACH DELETE n } IN TRANSACTIONS",
]


@pytest.mark.parametrize("cypher", WRITES + SCHEMA_CHANGES + ADMIN_AND_PROCEDURES)
def test_validator_rejects(platform, cypher):
    result = platform.validator.validate(cypher)
    assert not result.ok, f"Validator accepted: {cypher}"


@pytest.mark.parametrize("cypher", WRITES + SCHEMA_CHANGES + ["CALL db.createLabel('Evil')",
                                                              "CREATE USER evil SET PASSWORD 'xxxxxxxxxxxx'"])
def test_server_refuses_writes_even_without_validator(client, cypher):
    """Bypass the validator entirely: the READ transaction must be refused by Neo4j."""
    with pytest.raises(GraphQueryError) as exc:
        client.read(cypher)
    assert exc.value.code in {"Neo.ClientError.Statement.AccessMode",
                              "Neo.ClientError.Statement.SyntaxError"} or "AccessMode" in (exc.value.code or ""), exc.value.code
    # The graph is untouched
    assert client.read("MATCH (n {id: 'evil'}) RETURN count(n) AS c").rows[0]["c"] == 0


def test_server_refuses_unknown_admin_procedures(client):
    with pytest.raises(GraphQueryError) as exc:
        client.read("CALL apoc.cypher.runWrite('CREATE (n) RETURN n', {})")
    assert "ProcedureNotFound" in exc.value.code


def test_explain_reports_write_query_type(client):
    assert client.explain("CREATE (n:GCPInstance {id: 'x'})").query_type == "w"
    assert client.explain("MATCH (n:GCPInstance) RETURN count(n) AS c").query_type == "r"


def test_query_service_never_executes_rejected_queries(platform):
    before = len(platform.audit.recent)
    out = platform.queries.run("MATCH (n:GCPInstance) DETACH DELETE n", question="test", source="test")
    assert not out["ok"] and out["stage"] == "validation"
    entry = platform.audit.recent[before]
    assert entry["verdict"] == "rejected" and entry["outcome"] == "not_executed"
    assert "password" not in str(entry).lower()


def test_validator_rejects_unknown_schema(platform):
    for cypher in ["MATCH (p:KubernetesPod) RETURN p.id AS id",
                   "MATCH (i:GCPInstance)-[:EXPLOITED_BY]->(x) RETURN i.id AS id",
                   "MATCH (i:GCPInstance) RETURN i.business_criticality AS c"]:
        result = platform.validator.validate(cypher)
        assert not result.ok and result.reasons, cypher


def test_validator_accepts_legitimate_reads(platform):
    for cypher in ["MATCH (i:GCPInstance)-[:RUNS_AS]->(sa:GCPServiceAccount) RETURN i.id AS instance_id, sa.email AS email",
                   "MATCH (sa:GCPServiceAccount)-[e:CAN_READ|CAN_WRITE]->(b:GCPBucket) RETURN sa.id AS sa_id, type(e) AS t, b.id AS bucket_id",
                   "CALL db.labels() YIELD label RETURN label"]:
        result = platform.validator.validate(cypher)
        assert result.ok, (cypher, result.reasons)
