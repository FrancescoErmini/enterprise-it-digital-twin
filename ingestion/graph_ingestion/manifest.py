"""Ingestion manifest: freshness metadata written next to the graph, never into it.

The graph holds only Cartography-schema data. Run metadata (mode, timestamps,
counts) is written to a small JSON file on a shared volume, which the API reads
for the Data Sources view.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import neo4j


def graph_counts(session: neo4j.Session) -> dict[str, Any]:
    nodes = session.run("MATCH (n) RETURN count(n) AS c").single()["c"]
    rels = session.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
    by_label = {r["label"]: r["c"] for r in session.run(
        "MATCH (n) UNWIND labels(n) AS label RETURN label, count(*) AS c ORDER BY label")}
    by_type = {r["type"]: r["c"] for r in session.run(
        "MATCH ()-[r]->() RETURN type(r) AS type, count(*) AS c ORDER BY type")}
    indexes = session.run("SHOW INDEXES YIELD name RETURN count(name) AS c").single()["c"]
    return {"nodes": nodes, "relationships": rels, "indexes": indexes,
            "nodes_by_label": by_label, "relationships_by_type": by_type}


def write_manifest(path: str, entry: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, Any] = {}
    if target.exists():
        try:
            existing = json.loads(target.read_text())
        except json.JSONDecodeError:
            existing = {}
    sources = existing.get("sources", {})
    sources[f"{entry['provider_id']}/{entry['ingestion_mode']}"] = entry
    payload = {"updated_at": datetime.now(timezone.utc).isoformat(), "sources": sources}
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
    os.replace(tmp, target)
