# Enterprise IT Digital Twin

A strategic, cross-domain **IT Intelligence Platform** built on a technology knowledge graph. Data sources feed a
Neo4j graph that follows Cartography's schema; an **IT Intelligence Agent** (LangGraph) answers questions from that
graph with evidence; analytical workflows plug in as **use cases**.

**POC 01 - Cloud Security Investigation - Google Cloud** is the first registered use case. It is one use case of
the platform, not the platform itself. It identifies potential compromise scenarios in a Google Cloud organisation,
ranks them with a deterministic score, and recommends remediation that minimises operational disruption.
The platform is read-only: it never changes anything in any cloud.

> **All demo data is synthetic.** The graph holds a fictional organisation (`altamira-group.example (synthetic)`),
> generated deterministically and loaded through Cartography's own GCP transform/load functions. It is **not** a
> real Cartography ingestion run, and no Google Cloud credentials are needed or used anywhere.

## Prerequisites

- Docker Engine with Docker Compose v2 (tested with Docker 29.9 / Compose 5.6)
- About 4 GB of free RAM
- An OpenAI API key for the agent and Text2Cypher (`OPENAI_API_KEY`). Everything else (graph, explorer, rules,
  investigation results, remediation plan, tests) works without it.

## Start

```bash
cp .env.example .env
# set OPENAI_API_KEY in .env
docker compose up --build -d
```

Neo4j credentials are generated locally on first start by the `credentials-init` service into the `secrets`
volume; they are never in `.env` or in git. Startup order: credentials-init -> neo4j (healthy) -> graph-ingestion
(seeds, exits 0) -> digital-twin-api (healthy) -> digital-twin-ui.

| URL | What |
|---|---|
| http://localhost:8501 | Streamlit UI (`digital-twin-ui`) |
| http://localhost:8000/docs | FastAPI docs (`digital-twin-api`) |
| http://localhost:7474 | Neo4j Browser (user `neo4j`, password: `docker compose exec neo4j cat /secrets/neo4j_admin_password`) |

All ports are bound to 127.0.0.1. Check: `docker compose ps` (two one-shot services show `Exited (0)`, the other
three `healthy`) and `curl localhost:8000/health`.

**Behind a TLS-intercepting proxy** (corporate networks): `pip` inside the image build may reject the proxy's
certificate. Put the proxy CA in a gitignored file and add a `docker-compose.override.yml` that passes it as the
optional build secret `extra_ca` (and, for the API's calls to the LLM, mounts it and sets `SSL_CERT_FILE`). The
Dockerfiles use it only if present and never copy it into an image.

## The UI

The home page **Enterprise IT Digital Twin** has four sections, all generically named:

- **IT Intelligence Agent** - ask in natural language; pick a scope (platform tools only, or a use case); example
  prompts and suggested follow-ups.
- **Technology Graph Explorer** - search entities, render a focused subgraph, inspect properties and relationships,
  run validated read-only Cypher, view the live schema and the query audit log.
- **Use Cases** - the registry. One working entry: *Cloud Security Investigation - Google Cloud*, with the
  investigation workflow: 1. Overview (resources, affected projects, rule findings, high-priority scenarios) ->
  2. Investigation results (findings, evidence chain, hypotheses, confidence, missing evidence) -> 3. Prioritized
  remediation plan -> 4. Investigate with the agent; plus the Rules engine view (applicable vs skipped rules).
- **Data Sources** - providers, ingestion mode, status, counts and graph freshness.

A persistent banner marks all data as synthetic. Verified facts, potential exposures and unverified hypotheses
have distinct badges (green, orange, purple).

## Running the five demo questions

In the UI: **Use Cases -> Cloud Security Investigation - Google Cloud -> 4. Investigate with the agent**, click a
question, then **Investigate** (or use the IT Intelligence Agent page with that use case as scope).

1. Identify the three most dangerous potential compromise scenarios in our GCP environment. Explain how you ranked them.
2. A public Compute Engine VM runs with a privileged service account. What other resources could potentially be affected?
3. Compare different remediation strategies for excessive service account privileges. Which option minimizes production risk?
4. Could a less-trusted development project create security exposure for production resources? Investigate the available evidence.
5. Generate a prioritized security improvement plan for our GCP organization. Explain trade-offs and what remains unverified.

With curl:

```bash
curl -s -X POST localhost:8000/api/v1/usecases/cloud-security-gcp/investigate \
  -H 'content-type: application/json' \
  -d '{"question": "Identify the three most dangerous potential compromise scenarios in our GCP environment. Explain how you ranked them."}'

# same agent, explicit scope
curl -s -X POST localhost:8000/api/v1/agent/ask -H 'content-type: application/json' \
  -d '{"question": "Could a less-trusted development project create security exposure for production resources? Investigate the available evidence.", "usecase_id": "cloud-security-gcp"}'
```

Response structure (fields always present):

```json
{
  "status": "answered | insufficient_evidence",
  "answer": "## Answer ... ## Evidence ... ## Unknowns and assumptions ...",
  "evidence": [{"statement": "...", "classification": "potential_exposure", "resource_ids": ["..."],
                "source": "investigate_exposure:public_workload_privileged_identity", "graph_paths": [["..."]]}],
  "evidence_ids": ["131000000000200", "projects/alt-prod-web/zones/europe-west1-b/instances/web-frontend-01", "..."],
  "classification_counts": {"verified_fact": 0, "potential_exposure": 0, "unverified_hypothesis": 0},
  "findings": [{"finding_id": "E::131000000000200", "risk_score": 83.3, "factors": {"exposure": 0.4, "...": 1.0}}],
  "cypher": [{"tool": "investigate_exposure", "cypher": "MATCH ..."}],
  "trace": [{"step": 1, "tool": "investigate_exposure", "ok": true, "summary": "...", "evidence_ids": 42, "duration_ms": 180.2}],
  "unknowns": ["Whether a service actually listens on the exposed ports (runtime state is not in the graph)"],
  "stop_reason": "completed",
  "usage": {"tool_calls": 3, "iterations": 4, "tokens": 0, "seconds": 0.0}
}
```

Reading the panels: **Generated Cypher** lists every query a tool ran (use-case queries and Text2Cypher output);
**Tool execution trace** is the ordered tool sequence with arguments, outcome and evidence count; **Evidence**
groups statements by classification with the graph ids that support them; **Unknowns and assumptions** collects
each tool's missing evidence plus the agent's own list. The agent's private reasoning is never shown.

Without an LLM key, the agent endpoints return `503` (no key) or `502` with the provider's error. The deterministic
parts need no LLM:

```bash
curl -s localhost:8000/api/v1/usecases/cloud-security-gcp/panels/findings     # ranked scenarios + evidence
curl -s localhost:8000/api/v1/usecases/cloud-security-gcp/panels/remediation  # prioritized plan
curl -s localhost:8000/api/v1/usecases/cloud-security-gcp/panels/overview
curl -s localhost:8000/api/v1/usecases/cloud-security-gcp/panels/rules        # applicable vs skipped rules
curl -s -X POST localhost:8000/api/v1/graph/query -H 'content-type: application/json' \
  -d '{"cypher": "MATCH (i:GCPInstance)-[:RUNS_AS]->(sa:GCPServiceAccount) WHERE i.instancename = \"web-frontend-01\" RETURN i.id AS instance_id, sa.email AS email"}'
```

Sample (real) output of the last call, abridged:

```json
{"ok": true, "stage": "complete", "validation": {"verdict": "accepted", "query_type": "r",
 "referenced_labels": ["GCPInstance", "GCPServiceAccount"], "referenced_relationship_types": ["RUNS_AS"]},
 "rows": [{"instance_id": "projects/alt-prod-web/zones/europe-west1-b/instances/web-frontend-01",
           "email": "web-frontend-sa@alt-prod-web.iam.gserviceaccount.com"}],
 "row_count": 1, "truncated": false, "evidence_ids": ["projects/alt-prod-web/zones/europe-west1-b/instances/web-frontend-01"]}
```

A write such as `MATCH (n) DETACH DELETE n` returns `"ok": false, "stage": "validation"` and is never executed.

## Cartography rules against the running graph

The API image contains the official `cartography-rules` CLI and a wrapper, `twin-rules`, that connects as the
read-only agent user:

```bash
docker compose exec digital-twin-api twin-rules list
docker compose exec digital-twin-api twin-rules list compute_instance_exposed
docker compose exec digital-twin-api twin-rules frameworks
docker compose exec digital-twin-api twin-rules run object_storage_public --output json
docker compose exec digital-twin-api twin-rules run compute_instance_exposed --no-experimental --output json
```

Which GCP rule facts can run on this data, and why the others cannot, is printed at API startup
(`docker compose logs digital-twin-api | grep -E "APPLICABLE|SKIPPED"`) and served by
`GET /api/v1/usecases/cloud-security-gcp/panels/rules`.

## Tests

```bash
docker compose exec digital-twin-api pytest -q          # platform + use case (needs the running graph)
docker compose run --rm graph-ingestion pytest -q        # generator determinism, idempotent re-seed
docker compose exec digital-twin-api pytest -q -m live_llm   # end-to-end with the real LLM (skipped without a working key)
```

## Reset and re-seed

```bash
docker compose run --rm graph-ingestion python run_ingestion.py --reset              # wipe + seed
docker compose run --rm graph-ingestion python run_ingestion.py --verify-idempotent  # seed twice, compare counts
docker compose restart digital-twin-api                                              # optional: re-run startup checks
docker compose down -v   # remove everything, including generated credentials and the graph
```

## Repository layout

```
docker-compose.yml  .env.example  scripts/        (credential generator, schema-mapping generator)
backend/            digital-twin-api: app/{core,technology_graph,it_intelligence_agent,usecases,api,models}, tests/
frontend/           digital-twin-ui: app.py, pages/, ui/
ingestion/          graph-ingestion: run_ingestion.py, graph_ingestion/, providers/gcp/synthetic/{generator,loader,scenarios}
docs/               architecture, schema-mapping, security-scenarios, decisions, limitations, roadmap
```

## Documentation

- [docs/architecture.md](docs/architecture.md) - layers, services, data flow (Mermaid), registries, query safety, future live ingestion
- [docs/schema-mapping.md](docs/schema-mapping.md) - every label, relationship and property written, with upstream source; rules inventory
- [docs/security-scenarios.md](docs/security-scenarios.md) - the five scenarios and their controls
- [docs/decisions.md](docs/decisions.md) - pinned versions and design decisions
- [docs/limitations.md](docs/limitations.md) - Cartography gaps, discrepancies, production evolution
- [docs/roadmap.md](docs/roadmap.md) - future use cases (roadmap only)

## Synthetic-data disclaimer

Every organisation, project, instance, identity, bucket, IP address (RFC 5737 documentation ranges) and finding in
this repository is fictional. Findings describe *potential* exposures in a made-up configuration. Nothing here
describes or tests a real environment.
