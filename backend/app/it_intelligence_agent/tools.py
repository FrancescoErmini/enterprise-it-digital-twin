"""Agent tool registry.

A tool declares its name, description, JSON input schema (a pydantic model), JSON
output schema (the shared ToolOutput envelope) and owner ("platform" or a use-case
id). The agent only ever sees the tools registered for the platform plus the use
case it is investigating.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from app.core.registry import Registry
from app.it_intelligence_agent.evidence import ToolOutput

if TYPE_CHECKING:
    from app.platform import PlatformServices

PLATFORM_OWNER = "platform"


@dataclass
class ToolContext:
    platform: "PlatformServices"
    usecase_id: str | None = None
    question: str = ""
    trace: list[dict[str, Any]] = field(default_factory=list)
    outputs: list[ToolOutput] = field(default_factory=list)


@dataclass(frozen=True)
class AgentTool:
    name: str
    description: str
    input_model: type[BaseModel]
    owner: str
    handler: Callable[[ToolContext, BaseModel], ToolOutput]

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "owner": self.owner,
                "input_schema": self.input_model.model_json_schema(),
                "output_schema": ToolOutput.model_json_schema()}

    def invoke(self, ctx: ToolContext, arguments: dict[str, Any]) -> ToolOutput:
        started = time.perf_counter()
        try:
            parsed = self.input_model.model_validate(arguments or {})
            output = self.handler(ctx, parsed)
        except Exception as exc:  # noqa: BLE001 - tool failures are reported to the model, not raised
            output = ToolOutput(ok=False, tool=self.name, summary=f"{self.name} failed", error=str(exc))
        ctx.outputs.append(output)
        ctx.trace.append({
            "step": len(ctx.trace) + 1, "tool": self.name, "owner": self.owner, "arguments": arguments,
            "ok": output.ok, "summary": output.summary, "error": output.error,
            "evidence_ids": len(output.evidence_ids()), "cypher": output.cypher,
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        })
        return output

    def as_langchain(self, ctx: ToolContext) -> StructuredTool:
        def _run(**kwargs: Any) -> str:
            return json.dumps(self.invoke(ctx, kwargs).model_dump(mode="json"), default=str)[:24000]

        return StructuredTool.from_function(func=_run, name=self.name, description=self.description,
                                            args_schema=self.input_model)


class ToolRegistry(Registry[AgentTool]):
    def __init__(self) -> None:
        super().__init__("agent tool")

    def add(self, tool: AgentTool) -> AgentTool:
        return self.register(tool.name, tool)

    def tools_for(self, usecase_id: str | None) -> list[AgentTool]:
        return [t for t in self if t.owner == PLATFORM_OWNER or (usecase_id and t.owner == usecase_id)]
