"""End-to-end checks with the real LLM. Skipped unless OPENAI_API_KEY works.

    docker compose exec digital-twin-api pytest -m live_llm -v
"""

import pytest

from app.it_intelligence_agent.agent import ITIntelligenceAgent
from app.it_intelligence_agent.evidence import INSUFFICIENT_EVIDENCE
from app.it_intelligence_agent.llm import LLMUnavailableError
from app.usecases.security.gcp.prompts import EXAMPLE_QUESTIONS

pytestmark = pytest.mark.live_llm


@pytest.fixture(scope="module")
def agent(platform, settings):
    try:
        llm = platform.llm()
        llm.invoke("Reply with OK.")
    except LLMUnavailableError as exc:
        pytest.skip(str(exc))
    except Exception as exc:  # noqa: BLE001 - expired key, quota, network
        pytest.skip(f"LLM not usable: {exc}")
    return ITIntelligenceAgent(platform, settings, llm)


@pytest.mark.parametrize("question", EXAMPLE_QUESTIONS)
def test_demo_question(agent, question):
    resp = agent.ask(question, "cloud-security-gcp")
    assert resp["status"] == "answered", resp["answer"]
    assert resp["usage"]["tool_calls"] >= 1 and resp["evidence_ids"]


def test_live_agent_uses_multiple_tools(agent):
    resp = agent.ask(EXAMPLE_QUESTIONS[2], "cloud-security-gcp")
    assert len({s["tool"] for s in resp["trace"]}) >= 2


def test_live_insufficient_evidence(agent):
    resp = agent.ask("Has payments-api-01 actually been exploited, and how business-critical is it?",
                     "cloud-security-gcp")
    assert INSUFFICIENT_EVIDENCE.lower() in resp["answer"].lower()
