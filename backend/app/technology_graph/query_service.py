"""Validated, audited, read-only query execution: the one door to the graph for untrusted Cypher."""

from __future__ import annotations

from typing import Any

from app.core.logging import AuditLog
from app.technology_graph.client import GraphClient, GraphQueryError
from app.technology_graph.validator import CypherValidator


class QueryService:
    def __init__(self, client: GraphClient, validator: CypherValidator, audit: AuditLog) -> None:
        self.client = client
        self.validator = validator
        self.audit = audit

    def run(self, cypher: str, params: dict[str, Any] | None = None, *, question: str | None = None,
            source: str = "api", max_rows: int | None = None) -> dict[str, Any]:
        validation = self.validator.validate(cypher, params)
        if not validation.ok:
            self.audit.record(source=source, question=question, cypher=cypher, verdict="rejected",
                              reasons=validation.reasons, outcome="not_executed", duration_ms=0, row_count=0)
            return {"ok": False, "stage": "validation", "cypher": cypher, "validation": validation.as_dict(),
                    "error": "; ".join(validation.reasons)}
        try:
            result = self.client.read(cypher, params, max_rows=max_rows)
        except GraphQueryError as exc:
            self.audit.record(source=source, question=question, cypher=cypher, verdict="accepted",
                              outcome="error", error_code=exc.code, error=str(exc), duration_ms=0, row_count=0)
            return {"ok": False, "stage": "execution", "cypher": cypher, "validation": validation.as_dict(),
                    "error": f"{exc.code}: {exc}"}
        self.audit.record(source=source, question=question, cypher=cypher, verdict="accepted", outcome="ok",
                          duration_ms=round(result.duration_ms, 1), row_count=result.row_count,
                          truncated=result.truncated)
        return {"ok": True, "stage": "complete", "cypher": cypher, "validation": validation.as_dict(),
                **result.as_dict()}
