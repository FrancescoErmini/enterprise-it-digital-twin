"""Shared fixtures. Tests run against the live technology graph (the compose stack or a local Neo4j)."""

from __future__ import annotations

import glob
import os
from pathlib import Path
from typing import Any, Callable

import pytest
import yaml
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.core.config import Settings
from app.core.logging import configure_logging
from app.platform import PlatformServices

REPO = Path(__file__).resolve().parents[2]
SPEC_DIR = Path(os.environ.get("SCENARIO_SPEC_DIR", REPO / "ingestion/providers/gcp/synthetic/scenarios"))
DOCS_DIR = Path(os.environ.get("DOCS_DIR", REPO / "docs"))


@pytest.fixture(scope="session")
def settings(tmp_path_factory) -> Settings:
    os.environ.setdefault("AUDIT_LOG_PATH", str(tmp_path_factory.mktemp("audit") / "queries.jsonl"))
    configure_logging()
    return Settings()


@pytest.fixture(scope="session")
def platform(settings) -> PlatformServices:
    p = PlatformServices(settings)
    if not p.client.ping():
        pytest.skip("Technology graph not reachable; start the compose stack first")
    p.install_usecases()
    p.startup()
    return p


@pytest.fixture(scope="session")
def client(platform):
    return platform.client


@pytest.fixture(scope="session")
def scenario_specs() -> list[dict[str, Any]]:
    files = sorted(glob.glob(str(SPEC_DIR / "*.yaml")))
    assert files, f"No scenario specs found in {SPEC_DIR}"
    return [yaml.safe_load(Path(f).read_text()) for f in files]


@pytest.fixture(scope="session")
def gcp_service(platform):
    from app.usecases.security.gcp.service import GcpSecurityService

    return GcpSecurityService(platform)


class ScriptedChatModel(BaseChatModel):
    """Deterministic stand-in for an LLM, used to test agent and Text2Cypher mechanics without network access.

    Each scripted step is an AIMessage or a callable(messages) -> AIMessage.
    """

    script: list[Any]
    seen: list[list[BaseMessage]] = []

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001 - LangChain signature
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.seen.append(list(messages))
        if not self.script:
            step: Any = AIMessage(content="## Answer\nScript exhausted.\n## Evidence\n## Unknowns and assumptions\n")
        else:
            step = self.script.pop(0)
        message = step(messages) if callable(step) else step
        return ChatResult(generations=[ChatGeneration(message=message)])


def tool_call(name: str, args: dict, call_id: str) -> dict:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


@pytest.fixture
def scripted() -> Callable[[list[Any]], ScriptedChatModel]:
    return lambda script: ScriptedChatModel(script=list(script), seen=[])


@pytest.fixture(scope="session")
def api_client(platform):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as tc:
        yield tc
