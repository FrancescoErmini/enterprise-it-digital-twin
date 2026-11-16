# Decisions

Each entry: the decision, why, and what it costs. Versions were resolved together with `uv` on Python 3.13 and
verified by running the full test suite against them (see the final report in the README).

## Pinned versions

| Component | Version | Why |
|---|---|---|
| Python | 3.13 (`python:3.13-slim`) | Cartography 0.144 warns it is "tested on Python 3.13+ only"; the brief asks for 3.12+. |
| Neo4j | `neo4j:5.26-community` | 5.26 is the 5.x LTS line. Community, as the brief requires. |
| cartography | 0.144.0 | Latest release at build time; ships the GCP models, the `cartography.rules` engine and `cartography-rules` CLI used here. |
| neo4j (driver) | 6.4.1 | Version resolved with cartography 0.144.0; supports Neo4j 5.x servers, GQL status objects used by the validator. |
| langgraph | 1.2.22 | Current 1.x StateGraph API (`START`, `add_messages`, conditional edges). |
| langchain-core / langchain-openai | 1.8.1 / 1.8.0 | Mutually compatible pair for langgraph 1.2; `bind_tools(tool_choice="none")` used for finalisation. |
| fastapi / uvicorn / pydantic | 0.163.1 / 0.54.0 / 2.14.3 | Pydantic v2 throughout. |
| streamlit / pandas / requests | 1.67.0 / 3.1.1 / 2.34.2 | UI only; `st.page_link`, `st.graphviz_chart` (client-side rendering, no graphviz binary). |
| pytest / pytest-asyncio / httpx | 9.2.0 / 1.5.0 / 0.28.1 | Test stack; httpx is FastAPI's test transport, pytest-asyncio runs the async API test. |
| pyyaml | 6.0.3 | Scenario spec files and Cartography's permission mapping. |

## D-01 The seed goes through Cartography's own transform and load code

The brief requires Cartography-compatible data without inventing labels. Instead of writing Cypher that mimics the
schema, the generator emits API-shaped payloads and the loader calls `cartography.intel.gcp.*` transform/load
functions and the GCP analysis jobs. Labels, properties, id formats, relationship directions and even the
ontology properties (`_ont_*`) are therefore whatever upstream produces. Cost: two private helpers of
`policy_bindings` are called (the public `sync()` performs the Cloud Asset fetch itself), which the version pin
protects. It is still *not* a Cartography ingestion run, and the UI and docs say so.

## D-02 Two-pass load (inventory first, IAM second)

Bindings reference principals in other projects; `HAS_ALLOW_POLICY` and `CAN_*` MatchLinks only attach to nodes
that already exist. The first implementation loaded per project and the idempotency gate caught it (976 vs 1004
relationships between run 1 and run 2). Loading all inventory first makes a single run complete.

## D-03 Upstream indexes, no extra constraints

Cartography's `load()` creates the indexes it needs (`CREATE INDEX ... IF NOT EXISTS`). Upstream defines no
uniqueness constraints, and adding our own would diverge from what a real Cartography graph looks like. The
idempotency gate therefore checks node, relationship *and index* counts across two runs.

## D-04 Read-only enforcement on Neo4j Community

Neo4j Community has no role-based access control: a "read-only role" cannot be granted. The agent still connects
as a dedicated user (`twin_reader`, created at bootstrap, password generated locally), and **every** transaction is
opened in READ access mode; the server rejects writes, schema changes, `CREATE USER` and write procedures with
`Neo.ClientError.Statement.AccessMode` (proven by tests that bypass the validator). On Enterprise Edition the same
user would additionally receive the built-in `reader` role. Recorded as a discrepancy in `limitations.md`.

## D-05 Validator uses the server's planner

Keyword lists alone are not a boundary. The validator combines: a lexical denylist (defence in depth), a procedure
and namespaced-function allowlist, `EXPLAIN` on the server (syntax errors, `query_type` must be `r`, unknown label /
relationship / property notifications) and a check against the live introspected schema.

## D-06 Exposure signals come from `cartography.rules`, not re-implemented checks

Internet exposure of instances, public buckets and privileged principals are read from the official rule facts
(`compute_instance_exposed`, `object_storage_public`, `identity_administration_privileges`,
`delegation_boundary_modifiable`, `policy_administration_privileges`). The use case adds only what the rules do
not do: following `RUNS_AS`, bindings and `CAN_*` edges, environment trust, and shared-identity analysis. The rules
adapter calls the engine's runner (`cartography.rules.runners._run_fact`), which runs fact queries in read
transactions and parses rows with each rule's output model.

## D-07 Environment trust from folders, sensitivity from labels

Cartography has no environment attribute for projects. The folder a project sits in (`Production`, `Testing`,
`Development`) is the trust level; `data-classification` labels give sensitivity. Both are configurable in the use
case (`scoring.py`).

## D-08 Deterministic scoring in the use case

`risk_score = 100 x confidence x sum(weight x factor)` with six factors in [0, 1]. Weights, caps and tiers live in
`usecases/security/gcp/scoring.py` and are returned by `GET /api/v1/usecases/cloud-security-gcp` so the UI and the
agent can explain them. No randomness, stable sort keys, tested for identical output across runs.

## D-09 Agent design

A custom LangGraph `StateGraph` (agent -> tools -> agent, plus a finalize node) rather than the prebuilt ReAct agent,
because the ceilings (tool calls, iterations, tokens, per-tool failures) must be enforced inside the graph and the
trace must be recorded. When a ceiling is hit the model is asked once more, with tools disabled
(`tool_choice="none"`), to answer from the evidence gathered. A deterministic guard marks any answer with no graph
evidence as `insufficient_evidence` and prefixes "Insufficient evidence to determine this.".

## D-10 Credentials

Neo4j credentials are generated on first start by a one-shot `credentials-init` service into a named volume
(`NEO4J_AUTH_FILE` for Neo4j, `*_FILE` variables for clients). Nothing secret is in `.env.example` or in git.
The only user-supplied secret is `OPENAI_API_KEY`.

## D-11 One LLM provider, one seam

`ChatModelProvider` + a registry. OpenAI is the only implementation; Ollama would be one more subclass and
`LLM_PROVIDER=ollama`, with no agent change. No Ollama code is shipped (no speculative abstraction).

## D-12 Default model

`OPENAI_MODEL=gpt-4.1-mini`: tool calling, low cost and latency for a POC. Any tool-calling chat model works.
