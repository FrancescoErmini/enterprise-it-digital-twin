"""Validation of Cypher before execution.

Layers applied here, in order:
  1. structural checks (length, single statement)
  2. lexical denial of write / schema / admin clauses and of procedures or
     namespaced functions outside an allowlist (defence in depth only)
  3. the server's own planner via EXPLAIN: syntax errors, the statement's query
     type (must be read-only, ``r``) and unknown labels / types / properties
  4. references checked against the live introspected schema

Keyword checks alone are not the security boundary; the READ access mode
enforced by Neo4j in client.py is.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from app.technology_graph.client import GraphClient
from app.technology_graph.schema import SchemaService

ALLOWED_PROCEDURES = frozenset({
    "db.labels", "db.relationshiptypes", "db.propertykeys",
    "db.schema.visualization", "db.schema.nodetypeproperties", "db.schema.reltypeproperties",
})
ALLOWED_NAMESPACED_FUNCTIONS: frozenset[str] = frozenset()

_DENIED_CLAUSES = [
    (r"\bCREATE\b", "CREATE"), (r"\bMERGE\b", "MERGE"), (r"\bDELETE\b", "DELETE"),
    (r"\bDETACH\b", "DETACH"), (r"\bSET\b", "SET"), (r"\bREMOVE\b", "REMOVE"), (r"\bDROP\b", "DROP"),
    (r"\bLOAD\s+CSV\b", "LOAD CSV"), (r"\bFOREACH\b", "FOREACH"), (r"\bALTER\b", "ALTER"),
    (r"\bGRANT\b", "GRANT"), (r"\bDENY\b", "DENY"), (r"\bREVOKE\b", "REVOKE"),
    (r"\b(START|STOP)\s+DATABASE\b", "START/STOP DATABASE"), (r"\bSHOW\b", "SHOW"),
    (r"\bUSE\b", "USE"), (r"\bTERMINATE\b", "TERMINATE"), (r"\bIN\s+TRANSACTIONS\b", "IN TRANSACTIONS"),
    (r"\bFINISH\b", "FINISH"), (r"\bINSERT\b", "INSERT"),
]

_STRING = re.compile(r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"")
_LINE_COMMENT = re.compile(r"//[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_PROC_CALL = re.compile(r"\bCALL\s+([A-Za-z_][\w.]*)\s*(\(|$|\bYIELD\b)", re.I)
_NAMESPACED_FN = re.compile(r"(?<![\w.$])([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+)\s*\(")
_NODE_LABELS = re.compile(r"\(\s*\w*\s*((?::\s*`?\w+`?\s*[|&]?\s*)+)")
_REL_TYPES = re.compile(r"\[\s*\w*\s*:\s*((?:!?`?\w+`?\s*\|?\s*)+)")
_LABEL_PREDICATE = re.compile(r"\b\w+\s*:\s*`?([A-Z]\w*)`?")
_PROPERTY = re.compile(r"\b[a-z_]\w*\.(\w+)\b(?!\s*[.(])")


@dataclass
class ValidationResult:
    ok: bool
    cypher: str
    reasons: list[str] = field(default_factory=list)
    query_type: str | None = None
    referenced_labels: list[str] = field(default_factory=list)
    referenced_relationship_types: list[str] = field(default_factory=list)
    server_error: str | None = None

    @property
    def verdict(self) -> str:
        return "accepted" if self.ok else "rejected"

    def as_dict(self) -> dict:
        return {**asdict(self), "verdict": self.verdict}


def _strip(cypher: str) -> str:
    text = _BLOCK_COMMENT.sub(" ", cypher)
    text = _STRING.sub("''", text)
    return _LINE_COMMENT.sub(" ", text)


class CypherValidator:
    def __init__(self, client: GraphClient, schema: SchemaService, max_length: int = 6000) -> None:
        self.client = client
        self.schema = schema
        self.max_length = max_length

    def validate(self, cypher: str, params: dict | None = None) -> ValidationResult:
        cypher = (cypher or "").strip()
        result = ValidationResult(ok=False, cypher=cypher)
        if not cypher:
            result.reasons.append("Empty query")
            return result
        if len(cypher) > self.max_length:
            result.reasons.append(f"Query longer than {self.max_length} characters")
            return result

        text = _strip(cypher)
        body = text.rstrip().rstrip(";")
        if ";" in body:
            result.reasons.append("Multiple statements are not allowed")

        upper = body.upper()
        for pattern, name in _DENIED_CLAUSES:
            if re.search(pattern, upper):
                result.reasons.append(f"Clause not allowed in a read-only query: {name}")

        for match in _PROC_CALL.finditer(body):
            proc = match.group(1).lower()
            if proc not in ALLOWED_PROCEDURES:
                result.reasons.append(f"Procedure not allowed: {match.group(1)}")
        for match in _NAMESPACED_FN.finditer(body):
            name = match.group(1)
            if name.lower() in ALLOWED_PROCEDURES or name.lower() in ALLOWED_NAMESPACED_FUNCTIONS:
                continue
            if re.search(rf"\bCALL\s+{re.escape(name)}", body, re.I):
                continue  # reported as a procedure above
            result.reasons.append(f"Function not allowed: {name}")

        if result.reasons:
            return result

        # Server-side planning: syntax, query type and unknown schema elements.
        explained = self.client.explain(cypher, params)
        if not explained.ok:
            result.server_error = f"{explained.error_code}: {explained.error_message}"
            result.reasons.append(f"Rejected by the database planner: {explained.error_message}")
            return result
        result.query_type = explained.query_type
        if explained.query_type != "r":
            result.reasons.append(f"Statement is not read-only (query type '{explained.query_type}')")
        for lbl in explained.unknown_labels:
            result.reasons.append(f"Unknown label: {lbl}")
        for rel in explained.unknown_relationship_types:
            result.reasons.append(f"Unknown relationship type: {rel}")
        for prop in explained.unknown_properties:
            result.reasons.append(f"Unknown property: {prop}")

        # References against the introspected schema (labels/types with data in them).
        schema = self.schema.get()
        labels: set[str] = set()
        for group in _NODE_LABELS.findall(body):
            labels.update(x.strip("` ") for x in re.split(r"[:|&]", group) if x.strip("` "))
        without_maps = re.sub(r"\{[^{}]*\}", "{}", body)
        for match in _LABEL_PREDICATE.finditer(re.sub(r"\[[^\[\]]*:[^\[\]]*\]", "[]", without_maps)):
            if match.group(1) in schema.labels or match.group(1)[0].isupper():
                labels.add(match.group(1))
        rel_types: set[str] = set()
        for group in _REL_TYPES.findall(body):
            rel_types.update(x.strip("`! ") for x in group.split("|") if x.strip("`! "))
        result.referenced_labels = sorted(labels)
        result.referenced_relationship_types = sorted(rel_types)
        for lbl in sorted(labels):
            if not schema.has_label(lbl) and f"Unknown label: {lbl}" not in result.reasons:
                result.reasons.append(f"Label not present in the graph schema: {lbl}")
        for rel in sorted(rel_types):
            if not schema.has_relationship_type(rel) and f"Unknown relationship type: {rel}" not in result.reasons:
                result.reasons.append(f"Relationship type not present in the graph schema: {rel}")
        props = set(_PROPERTY.findall(body))
        for prop in sorted(props):
            if prop not in schema.property_keys and f"Unknown property: {prop}" not in result.reasons:
                result.reasons.append(f"Property not present in the graph schema: {prop}")

        result.ok = not result.reasons
        return result
