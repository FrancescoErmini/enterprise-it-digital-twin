"""Generic v1 API. Routes name platform concepts (use cases, graph, agent, data sources), never a provider."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from app.it_intelligence_agent.agent import ITIntelligenceAgent
from app.it_intelligence_agent.llm import LLMUnavailableError
from app.it_intelligence_agent.text2cypher import Text2Cypher
from app.models.api import AskRequest, CypherRequest, InvestigateRequest, Text2CypherRequest
from app.platform import PlatformServices
from app.technology_graph.client import GraphQueryError

router = APIRouter(prefix="/api/v1")


def _platform(request: Request) -> PlatformServices:
    return request.app.state.platform


def _usecase(platform: PlatformServices, usecase_id: str):
    if usecase_id not in platform.usecases:
        raise HTTPException(404, f"Unknown use case '{usecase_id}'")
    return platform.usecases.get(usecase_id)


def _ask(platform: PlatformServices, question: str, usecase_id: str | None) -> dict[str, Any]:
    try:
        agent = ITIntelligenceAgent(platform, platform.settings, platform.llm())
        return agent.ask(question, usecase_id)
    except LLMUnavailableError as exc:
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:  # LLM provider errors (auth, quota, network) surface as 502
        if exc.__class__.__module__.startswith(("openai", "httpx", "langchain")):
            raise HTTPException(502, f"LLM provider error: {exc}") from exc
        raise


# -- use cases ----------------------------------------------------------------------------
@router.get("/usecases")
def list_usecases(request: Request) -> list[dict[str, Any]]:
    platform = _platform(request)
    labels = set(platform.schema.get().labels)
    return [uc.describe(labels) for uc in platform.usecases]


@router.get("/usecases/{usecase_id}")
def get_usecase(usecase_id: str, request: Request) -> dict[str, Any]:
    platform = _platform(request)
    uc = _usecase(platform, usecase_id)
    return {**uc.describe(set(platform.schema.get().labels)), "startup_report": uc.startup_report}


@router.get("/usecases/{usecase_id}/panels/{panel_id}")
def get_panel(usecase_id: str, panel_id: str, request: Request) -> Any:
    platform = _platform(request)
    uc = _usecase(platform, usecase_id)
    try:
        panel = uc.panel(panel_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"panel": panel.describe(), "data": panel.provider(platform)}


@router.post("/usecases/{usecase_id}/investigate")
def investigate(usecase_id: str, body: InvestigateRequest, request: Request) -> dict[str, Any]:
    platform = _platform(request)
    _usecase(platform, usecase_id)
    return _ask(platform, body.question, usecase_id)


# -- agent --------------------------------------------------------------------------------
@router.get("/agent/tools")
def agent_tools(request: Request, usecase_id: str | None = None) -> list[dict[str, Any]]:
    return [t.describe() for t in _platform(request).tools.tools_for(usecase_id)]


@router.post("/agent/ask")
def agent_ask(body: AskRequest, request: Request) -> dict[str, Any]:
    platform = _platform(request)
    if body.usecase_id:
        _usecase(platform, body.usecase_id)
    return _ask(platform, body.question, body.usecase_id)


# -- technology graph ---------------------------------------------------------------------
@router.get("/graph/schema")
def graph_schema(request: Request, refresh: bool = False) -> dict[str, Any]:
    return _platform(request).schema.get(refresh=refresh).to_dict()


@router.post("/graph/query")
def graph_query(body: CypherRequest, request: Request) -> dict[str, Any]:
    return _platform(request).queries.run(body.cypher, body.params, question=body.question, source="api.graph_query")


@router.post("/graph/text2cypher")
def graph_text2cypher(body: Text2CypherRequest, request: Request) -> dict[str, Any]:
    platform = _platform(request)
    try:
        t2c = Text2Cypher(platform.llm(), platform.schema, platform.queries,
                          examples=platform.examples_for(body.usecase_id),
                          synonyms=platform.synonyms_for(body.usecase_id),
                          max_repairs=platform.settings.text2cypher_max_repairs)
        return t2c.run(body.question, source="api.text2cypher").as_dict()
    except LLMUnavailableError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.get("/graph/search")
def graph_search(request: Request, q: str = Query(..., min_length=2), limit: int = Query(25, le=100)) -> list[dict]:
    rows = _platform(request).client.read(
        "MATCH (n) WHERE toLower(n.id) CONTAINS toLower($q) OR toLower(coalesce(n.name, '')) CONTAINS toLower($q) "
        "OR toLower(coalesce(n.instancename, '')) CONTAINS toLower($q) OR toLower(coalesce(n.email, '')) CONTAINS toLower($q) "
        "OR toLower(coalesce(n.displayname, '')) CONTAINS toLower($q) "
        "RETURN n.id AS id, labels(n) AS labels, coalesce(n.instancename, n.email, n.name, n.displayname, n.id) AS name "
        "ORDER BY size(n.id) LIMIT $limit", {"q": q, "limit": limit}, max_rows=limit).rows
    return rows


@router.get("/graph/neighborhood")
def graph_neighborhood(request: Request, node_id: str, depth: int = Query(1, ge=1, le=2),
                       limit: int = Query(80, le=300)) -> dict[str, Any]:
    platform = _platform(request)
    cypher = (f"MATCH (n {{id: $id}}) OPTIONAL MATCH p=(n)-[*1..{depth}]-(m) WITH n, p LIMIT $limit "
              "RETURN n AS center, collect(p) AS paths")
    try:
        result = platform.client.read(cypher, {"id": node_id, "limit": limit}, max_rows=1)
    except GraphQueryError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not result.rows or result.rows[0]["center"] is None:
        raise HTTPException(404, f"No node with id '{node_id}'")
    row = result.rows[0]
    nodes: dict[str, dict] = {}
    edges: set[tuple[str, str, str]] = set()
    center = row["center"]
    nodes[center["id"]] = center
    for path in row["paths"] or []:
        for n in path["nodes"]:
            if n.get("id") is not None:
                nodes[n["id"]] = n
        for r in path["relationships"]:
            if r["_start"] is not None and r["_end"] is not None:
                edges.add((r["_start"], r["_type"], r["_end"]))
    return {"center": center["id"], "nodes": list(nodes.values()),
            "edges": [{"source": s, "type": t, "target": d} for s, t, d in sorted(edges)],
            "cypher": cypher}


# -- data sources and audit -----------------------------------------------------------------
@router.get("/datasources")
def datasources(request: Request) -> dict[str, Any]:
    platform = _platform(request)
    manifest = platform.manifest()
    schema = platform.schema.get()
    sources = []
    for key, entry in sorted(manifest.get("sources", {}).items()):
        root = entry.get("root_label")
        live = platform.client.read(
            f"MATCH (n:`{root}`) RETURN count(n) AS nodes, max(n.lastupdated) AS lastupdated", max_rows=1).rows[0] \
            if root and schema.has_label(root) else {"nodes": 0, "lastupdated": None}
        sources.append({**entry, "key": key, "status": "loaded" if live["nodes"] else "empty",
                        "live_root_nodes": live["nodes"], "graph_lastupdated": live["lastupdated"]})
    totals = platform.client.read("MATCH (n) RETURN count(n) AS nodes", max_rows=1).rows[0]
    return {"sources": sources, "graph": {"nodes": totals["nodes"], "labels": len(schema.labels),
                                          "relationship_types": len(schema.relationship_types)},
            "manifest_updated_at": manifest.get("updated_at"),
            "future_modes": [{"provider_id": "gcp", "ingestion_mode": "cartography", "status": "documented, not implemented",
                              "doc": "docs/architecture.md#future-live-ingestion"}]}


@router.get("/audit/recent")
def audit_recent(request: Request, limit: int = Query(50, le=200)) -> list[dict[str, Any]]:
    return list(reversed(_platform(request).audit.recent[-limit:]))
