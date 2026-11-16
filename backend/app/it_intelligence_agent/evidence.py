"""Evidence model shared by every tool and every answer.

Each statement the platform makes is tied to graph node ids and carries one of
three classifications. The vocabulary is domain-neutral: a use case decides
which classification a finding deserves.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

INSUFFICIENT_EVIDENCE = "Insufficient evidence to determine this."


class Classification(str, Enum):
    VERIFIED_FACT = "verified_fact"            # read directly from the graph
    POTENTIAL_EXPOSURE = "potential_exposure"  # a configuration that could allow harm; not proven exploitable
    UNVERIFIED_HYPOTHESIS = "unverified_hypothesis"  # an inference the graph cannot confirm


class EvidenceItem(BaseModel):
    statement: str
    classification: Classification
    resource_ids: list[str] = Field(default_factory=list)
    source: str = Field(description="Tool or rule that produced the statement")
    graph_paths: list[list[str]] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


class ToolOutput(BaseModel):
    """Envelope every agent tool returns (serialised to JSON for the model)."""

    ok: bool = True
    tool: str
    summary: str
    data: Any = None
    evidence: list[EvidenceItem] = Field(default_factory=list)
    cypher: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    error: str | None = None

    def evidence_ids(self) -> set[str]:
        return {rid for item in self.evidence for rid in item.resource_ids}
