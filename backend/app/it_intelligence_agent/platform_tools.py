"""Platform-level agent tools: schema discovery and validated read-only Cypher."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.it_intelligence_agent.evidence import Classification, EvidenceItem, ToolOutput
from app.it_intelligence_agent.text2cypher import Text2Cypher
from app.it_intelligence_agent.tools import PLATFORM_OWNER, AgentTool, ToolContext, ToolRegistry


class GetGraphSchemaInput(BaseModel):
    question: str | None = Field(None, description="Optional question; returns only the schema relevant to it")
    labels: list[str] | None = Field(None, description="Optional explicit list of node labels to describe")


class ExecuteCypherInput(BaseModel):
    question: str = Field(..., description="The question this query answers (always required, for auditing)")
    cypher: str | None = Field(None, description=(
        "Optional read-only Cypher. If omitted, the platform generates it from the question "
        "with Text2Cypher. Either way it is validated before it runs."))


def _get_graph_schema(ctx: ToolContext, args: GetGraphSchemaInput) -> ToolOutput:
    service = ctx.platform.schema
    schema = service.get()
    labels = args.labels
    if not labels and args.question:
        labels = service.select_labels(args.question, ctx.platform.synonyms_for(ctx.usecase_id))
    if not labels:
        labels = [lbl for lbl in schema.labels if lbl in service.primary_labels] or list(schema.labels)
    data = schema.to_dict(labels)
    return ToolOutput(tool="get_graph_schema",
                      summary=f"{len(data['labels'])} labels, {len(data['patterns'])} relationship patterns",
                      data=data)


def _execute_readonly_cypher(ctx: ToolContext, args: ExecuteCypherInput) -> ToolOutput:
    if args.cypher:
        outcome = ctx.platform.queries.run(args.cypher, question=args.question, source="agent")
        attempts = [{"attempt": 1, "cypher": args.cypher, "outcome": outcome["stage"], "ok": outcome["ok"],
                     "error": outcome.get("error")}]
        status = ("ok" if outcome.get("row_count") else "no_results") if outcome["ok"] else "failed"
        rows, ids, cypher = outcome.get("rows", []), outcome.get("evidence_ids", []), args.cypher
        message = outcome.get("error") or f"{outcome.get('row_count', 0)} rows"
    else:
        t2c = Text2Cypher(ctx.platform.llm(), ctx.platform.schema, ctx.platform.queries,
                          examples=ctx.platform.examples_for(ctx.usecase_id),
                          synonyms=ctx.platform.synonyms_for(ctx.usecase_id),
                          max_repairs=ctx.platform.settings.text2cypher_max_repairs)
        result = t2c.run(args.question, source="agent.text2cypher")
        attempts, status, rows = result.attempts, result.status, result.rows
        ids, cypher, message = result.evidence_ids, result.cypher, result.message

    executed = [a["cypher"] for a in attempts if a.get("cypher")]
    evidence = []
    if status == "ok":
        evidence.append(EvidenceItem(statement=f"Graph query result for: {args.question}",
                                     classification=Classification.VERIFIED_FACT, resource_ids=ids[:200],
                                     source="execute_readonly_cypher"))
    missing = [] if status == "ok" else [f"The graph returned no answer for: {args.question} ({message})"]
    return ToolOutput(ok=status in ("ok", "no_results"), tool="execute_readonly_cypher",
                      summary=f"{status}: {message}", data={"status": status, "rows": rows[:100],
                                                            "row_count": len(rows), "attempts": attempts},
                      evidence=evidence, cypher=executed, missing_evidence=missing,
                      error=None if status in ("ok", "no_results") else message)


def register_platform_tools(registry: ToolRegistry) -> None:
    registry.add(AgentTool(
        name="get_graph_schema",
        description=("Describe the live technology graph: node labels with properties and relationship "
                     "patterns. Call it before writing Cypher."),
        input_model=GetGraphSchemaInput, owner=PLATFORM_OWNER, handler=_get_graph_schema))
    registry.add(AgentTool(
        name="execute_readonly_cypher",
        description=("Answer a factual question from the graph with a validated read-only Cypher query. "
                     "Pass `question`, and optionally `cypher`; returns rows plus the node ids used as evidence."),
        input_model=ExecuteCypherInput, owner=PLATFORM_OWNER, handler=_execute_readonly_cypher))
