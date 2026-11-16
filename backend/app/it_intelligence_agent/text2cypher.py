"""Text2Cypher pipeline.

introspect live schema -> select relevant labels -> prompt with that subset and
validated few-shot examples -> generate -> validate -> execute -> on failure a
bounded repair loop -> structured result with evidence ids. Every attempt is
audited by the QueryService.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.technology_graph.query_service import QueryService
from app.technology_graph.schema import SchemaService
from app.technology_graph.validator import CypherValidator

logger = logging.getLogger(__name__)

_CODE_BLOCK = re.compile(r"```(?:cypher)?\s*(.*?)```", re.S | re.I)

SYSTEM_PROMPT = """You translate questions into a single read-only Neo4j Cypher query.
Rules:
- Use ONLY the labels, relationship types, directions and properties listed in the schema below.
- Never write: no CREATE, MERGE, SET, DELETE, REMOVE, LOAD CSV, CALL of procedures, or schema commands.
- Always return the `id` property of every node you report, aliased with a name ending in `_id`.
- Prefer explicit columns over whole nodes. Add LIMIT 100 unless the question asks for a count.
- If the schema cannot answer the question, return exactly: NO_QUERY: <one-line reason>
Answer with the Cypher inside a ```cypher code block and nothing else.

{schema}
"""


@dataclass(frozen=True)
class FewShotExample:
    question: str
    cypher: str


@dataclass
class Text2CypherResult:
    status: str  # ok | no_results | failed | insufficient_schema
    question: str
    cypher: str | None = None
    rows: list[dict[str, Any]] = field(default_factory=list)
    row_count: int = 0
    evidence_ids: list[str] = field(default_factory=list)
    attempts: list[dict[str, Any]] = field(default_factory=list)
    selected_labels: list[str] = field(default_factory=list)
    message: str = ""

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def validate_examples(examples: list[FewShotExample], validator: CypherValidator) -> list[FewShotExample]:
    """Keep only examples whose Cypher passes validation against the live graph."""
    valid = []
    for ex in examples:
        verdict = validator.validate(ex.cypher)
        if verdict.ok:
            valid.append(ex)
        else:
            logger.warning("Dropping few-shot example %r: %s", ex.question, verdict.reasons)
    return valid


def extract_cypher(text: str) -> tuple[str | None, str | None]:
    """Return (cypher, refusal_reason)."""
    text = (text or "").strip()
    if text.startswith("NO_QUERY"):
        return None, text.partition(":")[2].strip() or "The schema cannot answer this question."
    match = _CODE_BLOCK.search(text)
    candidate = (match.group(1) if match else text).strip()
    if candidate.startswith("NO_QUERY"):
        return None, candidate.partition(":")[2].strip()
    return (candidate or None), None


class Text2Cypher:
    def __init__(self, llm: BaseChatModel, schema: SchemaService, queries: QueryService,
                 examples: list[FewShotExample] | None = None, synonyms: dict[str, list[str]] | None = None,
                 max_repairs: int = 2) -> None:
        self.llm = llm
        self.schema = schema
        self.queries = queries
        self.examples = examples or []
        self.synonyms = synonyms or {}
        self.max_repairs = max_repairs

    def _prompt(self, labels: list[str]) -> list:
        schema_text = self.schema.get().render_for_prompt(labels)
        messages: list = [SystemMessage(content=SYSTEM_PROMPT.format(schema=schema_text))]
        for ex in self.examples[:6]:
            messages.append(HumanMessage(content=ex.question))
            messages.append(AIMessage(content=f"```cypher\n{ex.cypher}\n```"))
        return messages

    def run(self, question: str, source: str = "text2cypher") -> Text2CypherResult:
        labels = self.schema.select_labels(question, self.synonyms)
        result = Text2CypherResult(status="failed", question=question, selected_labels=labels)
        if not labels:
            result.status = "insufficient_schema"
            result.message = "No part of the graph schema matches this question."
            self.queries.audit.record(source=source, question=question, cypher=None, verdict="not_generated",
                                      outcome="insufficient_schema", duration_ms=0, row_count=0)
            return result

        messages = self._prompt(labels) + [HumanMessage(content=question)]
        for attempt in range(1 + self.max_repairs):
            reply = self.llm.invoke(messages)
            content = reply.content if isinstance(reply.content, str) else str(reply.content)
            cypher, refusal = extract_cypher(content)
            if refusal is not None:
                result.attempts.append({"attempt": attempt + 1, "cypher": None, "outcome": "refused",
                                        "error": refusal})
                result.status = "insufficient_schema"
                result.message = refusal
                self.queries.audit.record(source=source, question=question, cypher=None, verdict="not_generated",
                                          outcome="model_declined", reason=refusal, duration_ms=0, row_count=0)
                return result
            outcome = self.queries.run(cypher or "", question=question, source=source, max_rows=200)
            result.attempts.append({"attempt": attempt + 1, "cypher": cypher, "outcome": outcome["stage"],
                                    "ok": outcome["ok"], "error": outcome.get("error")})
            result.cypher = cypher
            if outcome["ok"]:
                result.rows = outcome["rows"]
                result.row_count = outcome["row_count"]
                result.evidence_ids = outcome["evidence_ids"]
                result.status = "ok" if outcome["row_count"] else "no_results"
                result.message = "Query executed" if outcome["row_count"] else "Query returned no rows"
                return result
            if attempt < self.max_repairs:
                messages = messages + [AIMessage(content=content), HumanMessage(content=(
                    f"That query failed at the {outcome['stage']} stage: {outcome['error']}\n"
                    "Return a corrected query that uses only the schema above, or NO_QUERY: <reason>."))]
        result.status = "failed"
        result.message = f"No valid query after {1 + self.max_repairs} attempts"
        logger.warning("Text2Cypher failed for %r after %d attempts", question, len(result.attempts))
        self.queries.audit.record(source=source, question=question, cypher=result.cypher, verdict="rejected",
                                  outcome="repair_exhausted", attempts=len(result.attempts), duration_ms=0,
                                  row_count=0)
        return result
