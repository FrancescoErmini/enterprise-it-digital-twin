"""Runtime configuration, read from environment variables only."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


@dataclass(frozen=True)
class Settings:
    neo4j_uri: str = field(default_factory=lambda: os.environ.get("NEO4J_URI", "bolt://localhost:7687"))
    neo4j_database: str = field(default_factory=lambda: os.environ.get("NEO4J_DATABASE", "neo4j"))
    neo4j_reader_user: str = field(default_factory=lambda: os.environ.get("NEO4J_READER_USER", "twin_reader"))
    neo4j_reader_password: str = field(default_factory=lambda: os.environ.get("NEO4J_READER_PASSWORD", ""))

    # Query execution limits
    query_timeout_s: float = field(default_factory=lambda: _float("QUERY_TIMEOUT_SECONDS", 10.0))
    query_max_rows: int = field(default_factory=lambda: _int("QUERY_MAX_ROWS", 500))
    query_max_result_bytes: int = field(default_factory=lambda: _int("QUERY_MAX_RESULT_BYTES", 1_000_000))
    query_max_concurrency: int = field(default_factory=lambda: _int("QUERY_MAX_CONCURRENCY", 4))
    query_max_length: int = field(default_factory=lambda: _int("QUERY_MAX_LENGTH", 6000))

    # LLM
    llm_provider: str = field(default_factory=lambda: os.environ.get("LLM_PROVIDER", "openai"))
    openai_api_key: str = field(default_factory=lambda: os.environ.get("OPENAI_API_KEY", ""))
    openai_model: str = field(default_factory=lambda: os.environ.get("OPENAI_MODEL", "gpt-4.1-mini"))
    llm_temperature: float = field(default_factory=lambda: _float("LLM_TEMPERATURE", 0.0))
    llm_request_timeout_s: float = field(default_factory=lambda: _float("LLM_REQUEST_TIMEOUT_SECONDS", 60.0))

    # Agent ceilings
    agent_max_tool_calls: int = field(default_factory=lambda: _int("AGENT_MAX_TOOL_CALLS", 12))
    agent_max_iterations: int = field(default_factory=lambda: _int("AGENT_MAX_ITERATIONS", 8))
    agent_max_tokens: int = field(default_factory=lambda: _int("AGENT_MAX_TOKENS", 120_000))
    agent_max_tool_retries: int = field(default_factory=lambda: _int("AGENT_MAX_TOOL_RETRIES", 2))
    text2cypher_max_repairs: int = field(default_factory=lambda: _int("TEXT2CYPHER_MAX_REPAIRS", 2))

    # Files
    ingestion_manifest_path: str = field(
        default_factory=lambda: os.environ.get("INGESTION_MANIFEST_PATH", "/data/manifest/ingestion.json"))
    audit_log_path: str = field(default_factory=lambda: os.environ.get("AUDIT_LOG_PATH", "/data/audit/queries.jsonl"))

    installed_usecases: tuple[str, ...] = field(default_factory=lambda: tuple(
        u.strip() for u in os.environ.get("INSTALLED_USECASES", "app.usecases.security.gcp").split(",") if u.strip()))


def get_settings() -> Settings:
    return Settings()
