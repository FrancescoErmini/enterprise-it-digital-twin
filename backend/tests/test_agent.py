"""Agent mechanics with a scripted model (no network): multi-tool ReAct loop, ceilings, insufficient evidence.

The scripted model only decides *which* tools to call; every tool result comes from the live graph and the
rules engine. Tests that need a real LLM are in test_live_llm.py.
"""

import dataclasses

from langchain_core.messages import AIMessage

from app.it_intelligence_agent.agent import ITIntelligenceAgent
from app.it_intelligence_agent.evidence import INSUFFICIENT_EVIDENCE
from tests.conftest import tool_call

FINAL = ("## Answer\nThe top scenario is `E::131000000000200` (potential_exposure and verified facts as returned).\n"
         "## Evidence\n- `131000000000200`\n## Unknowns and assumptions\n- Actual permission usage is unknown\n")


def test_question_drives_multiple_distinct_tool_calls(platform, settings, scripted):
    model = scripted([
        AIMessage(content="", tool_calls=[tool_call("get_graph_schema", {"question": "service account privileges"}, "c1")]),
        AIMessage(content="", tool_calls=[tool_call("investigate_exposure", {"top_n": 3}, "c2")]),
        AIMessage(content="", tool_calls=[tool_call("compare_remediation_options",
                                                    {"finding_id": "E::131000000000200"}, "c3")]),
        AIMessage(content=FINAL),
    ])
    resp = ITIntelligenceAgent(platform, settings, model).ask(
        "Compare remediation strategies for excessive service account privileges.", "cloud-security-gcp")
    tools = [step["tool"] for step in resp["trace"]]
    assert tools == ["get_graph_schema", "investigate_exposure", "compare_remediation_options"]
    assert len(set(tools)) >= 3 and all(step["ok"] for step in resp["trace"])
    assert resp["status"] == "answered" and resp["evidence_ids"] and resp["cypher"] and resp["findings"]
    assert "Actual permission usage is unknown" in resp["unknowns"]
    assert resp["usage"]["tool_calls"] == 3 and resp["stop_reason"] == "completed"
    # Tool results (not the model) carry the scores
    assert all("risk_score" in f for f in resp["findings"])


def test_unanswerable_question_yields_insufficient_evidence(platform, settings, scripted):
    model = scripted([
        AIMessage(content="", tool_calls=[tool_call("execute_readonly_cypher", {
            "question": "Has web-frontend-01 actually been exploited?",
            "cypher": "MATCH (i:GCPInstance)-[:EXPLOITED_BY]->(a) RETURN i.id AS instance_id"}, "c1")]),
        # A model that ignores the failed tool and hallucinates must not be presented as an answer.
        AIMessage(content="## Answer\nYes, logs show the VM was breached last week.\n"),
    ])
    resp = ITIntelligenceAgent(platform, settings, model).ask("Has web-frontend-01 actually been exploited?",
                                                              "cloud-security-gcp")
    assert resp["status"] == "insufficient_evidence"
    assert resp["answer"].startswith(INSUFFICIENT_EVIDENCE)
    assert resp["evidence_ids"] == []
    assert resp["trace"][0]["ok"] is False


def test_model_stating_insufficient_evidence_is_respected(platform, settings, scripted):
    model = scripted([
        AIMessage(content="", tool_calls=[tool_call("get_resource_context", {"resource_id": "payments-api-01"}, "c1")]),
        AIMessage(content=f"## Answer\n{INSUFFICIENT_EVIDENCE} The graph holds no business-criticality data.\n"
                          "## Unknowns and assumptions\n- Business criticality of payments-api-01\n"),
    ])
    resp = ITIntelligenceAgent(platform, settings, model).ask("How business-critical is payments-api-01?",
                                                              "cloud-security-gcp")
    assert resp["status"] == "insufficient_evidence"
    assert any("Business criticality" in u for u in resp["unknowns"])


def test_tool_call_ceiling_forces_finalisation(platform, settings, scripted):
    capped = dataclasses.replace(settings, agent_max_tool_calls=2)
    model = scripted([
        AIMessage(content="", tool_calls=[tool_call("run_security_rules", {"max_findings": 5}, "c1")]),
        AIMessage(content="", tool_calls=[tool_call("investigate_exposure", {"top_n": 2}, "c2")]),
        AIMessage(content="", tool_calls=[tool_call("get_resource_context", {"resource_id": "web-frontend-01"}, "c3")]),
        AIMessage(content=FINAL),
    ])
    resp = ITIntelligenceAgent(platform, capped, model).ask("Rank the risks.", "cloud-security-gcp")
    assert resp["usage"]["tool_calls"] == 2
    assert resp["stop_reason"] == "max_tool_calls"
    assert len(resp["trace"]) == 2


def test_iteration_ceiling(platform, settings, scripted):
    capped = dataclasses.replace(settings, agent_max_iterations=1)
    model = scripted([
        AIMessage(content="", tool_calls=[tool_call("investigate_exposure", {"top_n": 1}, "c1")]),
        AIMessage(content=FINAL),
    ])
    resp = ITIntelligenceAgent(platform, capped, model).ask("Rank the risks.", "cloud-security-gcp")
    assert resp["stop_reason"] == "max_iterations" and resp["usage"]["iterations"] == 1


def test_repeated_tool_failures_are_cut_off(platform, settings, scripted):
    bad = {"question": "x", "cypher": "MATCH (n:NoSuchLabel) RETURN n.id AS id"}
    model = scripted([AIMessage(content="", tool_calls=[tool_call("execute_readonly_cypher", bad, f"c{i}")])
                      for i in range(5)] + [AIMessage(content=FINAL)])
    resp = ITIntelligenceAgent(platform, settings, model).ask("x?", None)
    executed = [s for s in resp["trace"] if s["tool"] == "execute_readonly_cypher"]
    assert len(executed) == settings.agent_max_tool_retries + 1


def test_platform_scope_has_only_platform_tools(platform):
    names = {t.name for t in platform.tools.tools_for(None)}
    assert names == {"get_graph_schema", "execute_readonly_cypher"}
    uc_names = {t.name for t in platform.tools.tools_for("cloud-security-gcp")}
    assert uc_names == names | {"run_security_rules", "get_resource_context", "investigate_exposure",
                                "compare_remediation_options"}
    for tool in platform.tools:
        d = tool.describe()
        assert d["input_schema"]["type"] == "object" and d["output_schema"]["type"] == "object" and d["owner"]
