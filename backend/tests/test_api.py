"""HTTP API: generic routes, registry enumeration, read-only query endpoint."""

import httpx
import pytest


def test_health(api_client):
    r = api_client.get("/health")
    assert r.status_code == 200 and r.json()["graph"] == "reachable"


def test_usecase_registry_enumerated(api_client):
    usecases = api_client.get("/api/v1/usecases").json()
    assert [u["id"] for u in usecases] == ["cloud-security-gcp"]
    uc = usecases[0]
    assert uc["ready"] and len(uc["example_questions"]) == 5
    assert {p["kind"] for p in uc["ui_panels"]} == {"metrics", "findings", "remediation_plan", "rules"}


def test_routes_are_generic(api_client):
    paths = list(api_client.get("/openapi.json").json()["paths"])
    assert "/api/v1/usecases/{usecase_id}/investigate" in paths
    assert not [p for p in paths if "gcp" in p.lower() or "security" in p.lower()]


@pytest.mark.parametrize("panel", ["overview", "findings", "remediation", "rules"])
def test_panels(api_client, panel):
    r = api_client.get(f"/api/v1/usecases/cloud-security-gcp/panels/{panel}")
    assert r.status_code == 200 and r.json()["data"]


def test_unknown_usecase_404(api_client):
    assert api_client.get("/api/v1/usecases/finops/panels/overview").status_code == 404


def test_graph_query_endpoint_rejects_writes(api_client):
    body = api_client.post("/api/v1/graph/query", json={"cypher": "MATCH (n) DETACH DELETE n"}).json()
    assert body["ok"] is False and body["validation"]["verdict"] == "rejected"


def test_graph_explorer_endpoints(api_client):
    hits = api_client.get("/api/v1/graph/search", params={"q": "customer-exports"}).json()
    assert hits and hits[0]["id"] == "alt-prod-customer-exports"
    sub = api_client.get("/api/v1/graph/neighborhood", params={"node_id": "alt-prod-customer-exports"}).json()
    assert sub["edges"] and any(n["id"] == "alt-prod-customer-exports" for n in sub["nodes"])


def test_datasources(api_client):
    body = api_client.get("/api/v1/datasources").json()
    assert body["graph"]["nodes"] > 0
    if body["sources"]:  # present when the ingestion manifest volume is mounted
        assert body["sources"][0]["ingestion_mode"] == "synthetic"


@pytest.mark.asyncio
async def test_async_client_tools_listing(api_client):
    transport = httpx.ASGITransport(app=api_client.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/api/v1/agent/tools", params={"usecase_id": "cloud-security-gcp"})
    assert r.status_code == 200 and len(r.json()) == 6
