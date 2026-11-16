"""Request models for the public API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=2000)
    usecase_id: str | None = Field(None, description="Restrict the agent to the platform tools plus this use case's tools")


class InvestigateRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=2000)


class CypherRequest(BaseModel):
    cypher: str = Field(..., min_length=1, max_length=6000)
    params: dict[str, Any] = Field(default_factory=dict)
    question: str | None = Field(None, description="Optional question recorded in the audit log")


class Text2CypherRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=2000)
    usecase_id: str | None = None
