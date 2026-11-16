"""The IT Intelligence Agent: a LangGraph ReAct loop with hard ceilings.

agent (LLM chooses tools) -> tools (execute, observe) -> agent ... -> END
                                       \\-> finalize (budget reached: answer from evidence so far)

Ceilings: total tool calls, agent iterations (investigation depth), tokens, and
per-tool retries after failures. The response exposes the tool sequence and
the evidence; the model's private reasoning is never returned.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from typing import TYPE_CHECKING, Annotated, Any, TypedDict

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from app.core.config import Settings
from app.it_intelligence_agent.evidence import INSUFFICIENT_EVIDENCE, Classification, EvidenceItem
from app.it_intelligence_agent.tools import AgentTool, ToolContext

if TYPE_CHECKING:
    from app.platform import PlatformServices

logger = logging.getLogger(__name__)

PLATFORM_SYSTEM_PROMPT = """You are the IT Intelligence Agent of the Enterprise IT Digital Twin.
You answer questions about an organisation's technology estate using only the tools provided; they read a
technology knowledge graph built from inventory data.

Rules:
- Investigate with tools: plan, call a tool, read the result, refine, and stop as soon as you can answer.
- Every factual statement must come from a tool result. Cite node ids in backticks.
- Numbers, scores and rankings come from tools. Never invent, recompute or adjust them; explain them.
- Keep each statement's classification exactly as the tool gave it: verified_fact, potential_exposure or
  unverified_hypothesis. Never upgrade a potential exposure or a hypothesis to a fact.
- If the tools cannot provide the evidence, write exactly "{insufficient}" and say which data would be needed.
- Give concise factual explanations. Do not narrate your private reasoning.

Write the final answer in markdown with these sections:
## Answer
## Evidence
## Unknowns and assumptions
"""

FINALIZE_PROMPT = ("The investigation budget is exhausted ({reason}). Do not call tools. Write the final answer now "
                   "from the tool results above, in the required sections, and list what remains unverified.")


class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    tool_calls: int
    iterations: int
    tokens: int
    stop_reason: str | None


def _tokens(message: Any) -> int:
    usage = getattr(message, "usage_metadata", None) or {}
    return int(usage.get("total_tokens", 0))


def _section(text: str, heading: str) -> list[str]:
    match = re.search(rf"^##\s*{heading}[^\n]*\n(.*?)(?=^##\s|\Z)", text, re.S | re.M | re.I)
    if not match:
        return []
    return [re.sub(r"^[-*\d.\s]+", "", line).strip() for line in match.group(1).splitlines()
            if line.strip() and line.strip() not in ("-", "*")]


class ITIntelligenceAgent:
    def __init__(self, platform: "PlatformServices", settings: Settings, llm: BaseChatModel) -> None:
        self.platform = platform
        self.settings = settings
        self.llm = llm

    def _build_graph(self, tools: list[AgentTool], ctx: ToolContext):
        s = self.settings
        lc_tools = [t.as_langchain(ctx) for t in tools]
        by_name = {t.name: t for t in tools}
        model = self.llm.bind_tools(lc_tools)
        failures: dict[str, int] = {}

        def agent_node(state: AgentState) -> dict:
            if state["iterations"] >= s.agent_max_iterations:
                return {"stop_reason": "max_iterations"}
            if state["tokens"] >= s.agent_max_tokens:
                return {"stop_reason": "max_tokens"}
            reply = model.invoke(state["messages"])
            return {"messages": [reply], "iterations": state["iterations"] + 1,
                    "tokens": state["tokens"] + _tokens(reply)}

        def tools_node(state: AgentState) -> dict:
            last = state["messages"][-1]
            used = state["tool_calls"]
            results: list[ToolMessage] = []
            for call in getattr(last, "tool_calls", []) or []:
                name = call["name"]
                if used >= s.agent_max_tool_calls:
                    content = json.dumps({"ok": False, "error": "Tool-call budget exhausted; answer now."})
                elif name not in by_name:
                    content = json.dumps({"ok": False, "error": f"Unknown tool {name}"})
                elif failures.get(name, 0) > s.agent_max_tool_retries:
                    content = json.dumps({"ok": False, "error": f"{name} failed repeatedly; stop retrying it."})
                else:
                    used += 1
                    output = by_name[name].invoke(ctx, call.get("args") or {})
                    if not output.ok:
                        failures[name] = failures.get(name, 0) + 1
                    content = json.dumps(output.model_dump(mode="json"), default=str)[:24000]
                results.append(ToolMessage(content=content, tool_call_id=call["id"], name=name))
            update: dict = {"messages": results, "tool_calls": used}
            if used >= s.agent_max_tool_calls:
                update["stop_reason"] = "max_tool_calls"
            return update

        def finalize_node(state: AgentState) -> dict:
            final_model = self.llm.bind_tools(lc_tools, tool_choice="none")
            reply = final_model.invoke(state["messages"] + [HumanMessage(
                content=FINALIZE_PROMPT.format(reason=state["stop_reason"]))])
            return {"messages": [reply], "tokens": state["tokens"] + _tokens(reply)}

        def after_agent(state: AgentState) -> str:
            if state.get("stop_reason"):
                return "finalize"
            return "tools" if getattr(state["messages"][-1], "tool_calls", None) else END

        def after_tools(state: AgentState) -> str:
            return "finalize" if state.get("stop_reason") else "agent"

        graph = StateGraph(AgentState)
        graph.add_node("agent", agent_node)
        graph.add_node("tools", tools_node)
        graph.add_node("finalize", finalize_node)
        graph.add_edge(START, "agent")
        graph.add_conditional_edges("agent", after_agent, {"tools": "tools", "finalize": "finalize", END: END})
        graph.add_conditional_edges("tools", after_tools, {"agent": "agent", "finalize": "finalize"})
        graph.add_edge("finalize", END)
        return graph.compile()

    def ask(self, question: str, usecase_id: str | None = None) -> dict[str, Any]:
        started = time.perf_counter()
        ctx = ToolContext(platform=self.platform, usecase_id=usecase_id, question=question)
        tools = self.platform.tools.tools_for(usecase_id)
        system = PLATFORM_SYSTEM_PROMPT.format(insufficient=INSUFFICIENT_EVIDENCE)
        usecase_prompt = self.platform.prompt_for(usecase_id)
        if usecase_prompt:
            system += "\n" + usecase_prompt
        graph = self._build_graph(tools, ctx)
        state = graph.invoke(
            {"messages": [SystemMessage(content=system), HumanMessage(content=question)],
             "tool_calls": 0, "iterations": 0, "tokens": 0, "stop_reason": None},
            {"recursion_limit": 4 * self.settings.agent_max_iterations + 10})
        final = state["messages"][-1]
        answer = final.content if isinstance(final.content, str) else str(final.content)
        return self._assemble(question, usecase_id, answer, ctx, state, time.perf_counter() - started)

    @staticmethod
    def _assemble(question: str, usecase_id: str | None, answer: str, ctx: ToolContext, state: dict,
                  elapsed: float) -> dict[str, Any]:
        evidence: list[EvidenceItem] = []
        seen: set[tuple[str, str]] = set()
        cypher: list[dict[str, str]] = []
        findings: list[dict[str, Any]] = []
        unknowns: list[str] = []
        for out in ctx.outputs:
            for item in out.evidence:
                key = (item.statement, item.classification.value)
                if key not in seen:
                    seen.add(key)
                    evidence.append(item)
            cypher.extend({"tool": out.tool, "cypher": c} for c in out.cypher)
            if isinstance(out.data, dict) and isinstance(out.data.get("findings"), list):
                for f in out.data["findings"]:
                    if isinstance(f, dict) and f.get("finding_id") not in {x.get("finding_id") for x in findings}:
                        findings.append(f)
            unknowns.extend(m for m in out.missing_evidence if m not in unknowns)
        unknowns.extend(u for u in _section(answer, "Unknowns") if u not in unknowns)

        evidence_ids = sorted({rid for item in evidence for rid in item.resource_ids})
        status = "answered"
        if INSUFFICIENT_EVIDENCE.lower() in answer.lower():
            status = "insufficient_evidence"
        elif not evidence_ids:
            # Deterministic guard: an answer that no tool result supports is not presented as one.
            status = "insufficient_evidence"
            answer = f"{INSUFFICIENT_EVIDENCE}\n\nNo tool returned graph evidence for this question.\n\n{answer}"
        counts = {c.value: sum(1 for e in evidence if e.classification == c) for c in Classification}
        return {
            "id": str(uuid.uuid4()), "question": question, "usecase_id": usecase_id, "status": status,
            "answer": answer, "evidence": [e.model_dump(mode="json") for e in evidence],
            "evidence_ids": evidence_ids, "classification_counts": counts, "findings": findings,
            "cypher": cypher, "trace": ctx.trace, "unknowns": unknowns,
            "stop_reason": state.get("stop_reason") or "completed",
            "usage": {"tool_calls": state.get("tool_calls", 0), "iterations": state.get("iterations", 0),
                      "tokens": state.get("tokens", 0), "seconds": round(elapsed, 2)},
        }
