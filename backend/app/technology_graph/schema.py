"""Live schema introspection of the technology graph.

The schema is read from the running database (labels, relationship types,
property keys and observed label->type->label patterns), never from a
hand-written description, so prompts and validation always reflect what is
actually loaded.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Iterable

from app.technology_graph.client import GraphClient


@dataclass(frozen=True)
class Pattern:
    source: str
    rel_type: str
    target: str
    count: int

    def render(self) -> str:
        return f"(:{self.source})-[:{self.rel_type}]->(:{self.target})"


@dataclass
class GraphSchema:
    labels: dict[str, int] = field(default_factory=dict)
    relationship_types: dict[str, int] = field(default_factory=dict)
    node_properties: dict[str, list[str]] = field(default_factory=dict)
    relationship_properties: dict[str, list[str]] = field(default_factory=dict)
    property_keys: set[str] = field(default_factory=set)
    patterns: list[Pattern] = field(default_factory=list)
    introspected_at: float = 0.0

    def has_label(self, label: str) -> bool:
        return self.labels.get(label, 0) > 0

    def has_relationship_type(self, rel_type: str) -> bool:
        return self.relationship_types.get(rel_type, 0) > 0

    def to_dict(self, labels: Iterable[str] | None = None) -> dict:
        selected = set(labels) if labels is not None else set(self.labels)
        return {
            "labels": {lbl: {"count": self.labels[lbl], "properties": self.node_properties.get(lbl, [])}
                       for lbl in sorted(selected) if lbl in self.labels},
            "relationship_types": {t: {"count": c, "properties": self.relationship_properties.get(t, [])}
                                   for t, c in sorted(self.relationship_types.items())},
            "patterns": [p.render() for p in self.patterns if p.source in selected or p.target in selected],
        }

    def render_for_prompt(self, labels: Iterable[str]) -> str:
        selected = sorted(set(labels) & set(self.labels))
        lines = ["Node labels and their properties:"]
        for lbl in selected:
            props = ", ".join(p for p in self.node_properties.get(lbl, []) if p != "lastupdated")
            lines.append(f"- {lbl} ({self.labels[lbl]} nodes): {props}")
        rels = [p for p in self.patterns if p.source in selected and p.target in selected]
        lines.append("Relationship patterns (direction matters):")
        lines.extend(f"- {p.render()}" for p in rels)
        return "\n".join(lines)


def _split_identifier(name: str) -> set[str]:
    parts = re.findall(r"[A-Z]+(?=[A-Z][a-z]|\b)|[A-Z]?[a-z]+|[A-Z]+|\d+", name)
    return {p.lower() for p in parts}


class SchemaService:
    """Introspects the schema and caches it for a short TTL."""

    def __init__(self, client: GraphClient, ttl_s: float = 60.0) -> None:
        self.client = client
        self.ttl_s = ttl_s
        self._schema: GraphSchema | None = None
        self._lock = threading.Lock()
        # Labels that represent the primary identity of a node (supplied by registered use
        # cases). Used to render readable patterns when a node carries several labels.
        self.primary_labels: set[str] = set()

    def get(self, refresh: bool = False) -> GraphSchema:
        with self._lock:
            if refresh or self._schema is None or time.time() - self._schema.introspected_at > self.ttl_s:
                self._schema = self._introspect()
            return self._schema

    def _primary(self, labels: list[str]) -> str:
        preferred = [lbl for lbl in labels if lbl in self.primary_labels]
        if preferred:
            return sorted(preferred)[0]
        return ":".join(sorted(labels))

    def _introspect(self) -> GraphSchema:
        schema = GraphSchema(introspected_at=time.time())
        q = self.client.read
        for row in q("MATCH (n) UNWIND labels(n) AS label RETURN label, count(*) AS c", max_rows=5000).rows:
            schema.labels[row["label"]] = row["c"]
        for row in q("MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS c", max_rows=5000).rows:
            schema.relationship_types[row["t"]] = row["c"]
        for row in q("CALL db.propertyKeys() YIELD propertyKey RETURN propertyKey", max_rows=10000).rows:
            schema.property_keys.add(row["propertyKey"])
        node_props: dict[str, set[str]] = {}
        for row in q("CALL db.schema.nodeTypeProperties() YIELD nodeLabels, propertyName "
                     "RETURN nodeLabels, propertyName", max_rows=20000).rows:
            if row["propertyName"] is None:
                continue
            for lbl in row["nodeLabels"]:
                node_props.setdefault(lbl, set()).add(row["propertyName"])
        schema.node_properties = {k: sorted(v) for k, v in node_props.items()}
        rel_props: dict[str, set[str]] = {}
        for row in q("CALL db.schema.relTypeProperties() YIELD relType, propertyName "
                     "RETURN relType, propertyName", max_rows=20000).rows:
            if row["propertyName"] is None:
                continue
            rel_props.setdefault(row["relType"].strip(":`"), set()).add(row["propertyName"])
        schema.relationship_properties = {k: sorted(v) for k, v in rel_props.items()}

        counts: dict[tuple[str, str, str], int] = {}
        for row in q("MATCH (a)-[r]->(b) RETURN labels(a) AS la, type(r) AS t, labels(b) AS lb, count(*) AS c",
                     max_rows=20000).rows:
            key = (self._primary(row["la"]), row["t"], self._primary(row["lb"]))
            counts[key] = counts.get(key, 0) + row["c"]
        schema.patterns = [Pattern(s, t, d, c) for (s, t, d), c in sorted(counts.items())]
        return schema

    # -- schema selection for Text2Cypher --------------------------------------------------
    def select_labels(self, question: str, synonyms: dict[str, list[str]] | None = None,
                      max_labels: int = 18) -> list[str]:
        """Choose the labels relevant to a question: direct name/synonym matches plus 1-hop neighbours."""
        schema = self.get()
        text = question.lower()
        words = set(re.findall(r"[a-z0-9]+", text))
        candidates = [lbl for lbl in schema.labels if lbl in self.primary_labels] or list(schema.labels)
        matched: list[str] = []
        for phrase, labels in (synonyms or {}).items():
            if re.search(rf"\b{re.escape(phrase.lower())}", text):
                matched.extend(lbl for lbl in labels if schema.has_label(lbl))
        for lbl in candidates:
            tokens = _split_identifier(lbl)
            if lbl.lower() in text or (tokens and tokens <= words and len(tokens) > 0
                                       and any(len(t) > 3 for t in tokens)):
                matched.append(lbl)
        seen: list[str] = []
        for lbl in matched:
            if lbl not in seen:
                seen.append(lbl)
        if not seen:
            return []
        neighbours: list[str] = []
        for p in schema.patterns:
            if p.source in seen and p.target not in seen and p.target in candidates:
                neighbours.append(p.target)
            if p.target in seen and p.source not in seen and p.source in candidates:
                neighbours.append(p.source)
        for lbl in neighbours:
            if lbl not in seen and len(seen) < max_labels:
                seen.append(lbl)
        return seen[:max_labels]
