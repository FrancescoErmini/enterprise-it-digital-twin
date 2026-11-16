"""Text2Cypher: schema selection, few-shot validation, repair loop and clean logged failure."""

from langchain_core.messages import AIMessage

from app.it_intelligence_agent.text2cypher import Text2Cypher, extract_cypher

GOOD = "```cypher\nMATCH (i:GCPInstance)-[:RUNS_AS]->(sa:GCPServiceAccount)\nRETURN i.id AS instance_id, sa.email AS email LIMIT 5\n```"
BAD_LABEL = "```cypher\nMATCH (p:KubernetesPod)-[:RUNS_ON]->(i:GCPInstance) RETURN p.id AS pod_id\n```"
BAD_SYNTAX = "```cypher\nMATCH (i:GCPInstance RETURN i.id AS instance_id\n```"
WRITE = "```cypher\nMATCH (i:GCPInstance) DETACH DELETE i\n```"


def _t2c(platform, model):
    return Text2Cypher(model, platform.schema, platform.queries,
                       examples=platform.examples_for("cloud-security-gcp"),
                       synonyms=platform.synonyms_for("cloud-security-gcp"), max_repairs=2)


def test_few_shot_examples_are_validated(platform):
    uc = platform.usecases.get("cloud-security-gcp")
    assert len(platform.examples_for("cloud-security-gcp")) == len(uc.few_shot_examples)


def test_schema_selection_picks_relevant_labels(platform):
    labels = platform.schema.select_labels("Which VMs run as a service account with access to buckets?",
                                           platform.synonyms_for("cloud-security-gcp"))
    assert {"GCPInstance", "GCPServiceAccount", "GCPBucket"} <= set(labels)
    assert platform.schema.select_labels("What is the weather in Rome?", {}) == []


def test_successful_generation(platform, scripted):
    result = _t2c(platform, scripted([AIMessage(content=GOOD)])).run("Which service accounts do VMs run as?")
    assert result.status == "ok" and result.row_count == 5 and result.evidence_ids
    assert len(result.attempts) == 1


def test_repair_loop_recovers(platform, scripted):
    model = scripted([AIMessage(content=BAD_SYNTAX), AIMessage(content=GOOD)])
    result = _t2c(platform, model).run("Which service accounts do the VMs run as?")
    assert result.status == "ok" and len(result.attempts) == 2
    assert result.attempts[0]["ok"] is False and "planner" in result.attempts[0]["error"]
    # The error was fed back to the model
    assert "failed at the validation stage" in model.seen[1][-1].content


def test_malformed_question_triggers_repair_then_clean_logged_failure(platform, scripted):
    before = len(platform.audit.recent)
    model = scripted([AIMessage(content=BAD_LABEL), AIMessage(content=WRITE), AIMessage(content=BAD_LABEL)])
    result = _t2c(platform, model).run("Which Kubernetes pods run on the VM web-frontend-01?")
    assert result.status == "failed" and len(result.attempts) == 3
    assert all(a["ok"] is False for a in result.attempts)
    entries = platform.audit.recent[before:]
    assert [e["verdict"] for e in entries[:3]] == ["rejected", "rejected", "rejected"]
    assert entries[-1]["outcome"] == "repair_exhausted"


def test_model_declines_when_schema_cannot_answer(platform, scripted):
    model = scripted([AIMessage(content="NO_QUERY: the graph has no exploitation records")])
    result = _t2c(platform, model).run("Was the VM web-frontend-01 exploited?")
    assert result.status == "insufficient_schema" and result.cypher is None


def test_no_matching_schema_skips_generation(platform, scripted):
    model = scripted([])
    result = _t2c(platform, model).run("What is the weather in Rome tomorrow?")
    assert result.status == "insufficient_schema" and model.seen == []


def test_extract_cypher():
    assert extract_cypher(GOOD)[0].startswith("MATCH")
    assert extract_cypher("NO_QUERY: nope") == (None, "nope")
