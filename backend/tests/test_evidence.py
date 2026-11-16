"""Evidence: every finding and every tool result cites resource ids that exist in the graph."""

from app.it_intelligence_agent.tools import ToolContext


def _missing_ids(client, ids):
    ids = sorted(set(ids))
    rows = client.read("UNWIND $ids AS id OPTIONAL MATCH (n {id: id}) WITH id, n WHERE n IS NULL RETURN id",
                       {"ids": ids}, max_rows=5000).rows
    return [r["id"] for r in rows]


def test_every_finding_cites_graph_ids(gcp_service, client):
    findings, _ = gcp_service.investigate()
    for f in findings:
        assert f.affected_resource_ids, f.finding_id
        assert _missing_ids(client, f.affected_resource_ids) == [], f.finding_id
        assert _missing_ids(client, [n for p in f.graph_paths for n in p]) == [], f.finding_id


def test_tool_outputs_carry_traceable_evidence(platform, client):
    ctx = ToolContext(platform=platform, usecase_id="cloud-security-gcp", question="test")
    for name, args in [("investigate_exposure", {"top_n": 5}), ("run_security_rules", {}),
                       ("get_resource_context", {"resource_id": "web-frontend-01"}),
                       ("compare_remediation_options", {"finding_id": "E::131000000000200"})]:
        out = platform.tools.get(name).invoke(ctx, args)
        assert out.ok, (name, out.error)
        ids = out.evidence_ids()
        assert ids, name
        assert _missing_ids(client, ids) == [], name
        for item in out.evidence:
            assert item.classification.value in {"verified_fact", "potential_exposure", "unverified_hypothesis"}


def test_three_classifications_are_produced(platform):
    ctx = ToolContext(platform=platform, usecase_id="cloud-security-gcp", question="test")
    out = platform.tools.get("investigate_exposure").invoke(ctx, {"top_n": 10})
    assert {e.classification.value for e in out.evidence} == {"verified_fact", "potential_exposure",
                                                               "unverified_hypothesis"}


def test_cypher_results_return_evidence_ids(platform):
    out = platform.queries.run("MATCH (i:GCPInstance)-[:RUNS_AS]->(sa:GCPServiceAccount) "
                               "RETURN i.id AS instance_id, sa AS sa LIMIT 3", question="test")
    assert out["ok"] and len(out["evidence_ids"]) >= 4
