# Roadmap

Roadmap only. None of these use cases exists in code, in the API or as a clickable UI entry. Each would be a new
package under `backend/app/usecases/` registering its own tools, scoring profile and panels on the same graph,
agent and evidence model.

| Use case | Question it answers | Graph data it needs (Cartography modules) |
|---|---|---|
| Cloud FinOps | Where does spend go, and what is idle or oversized? | Compute, storage, databases + billing export (new source) |
| Identity Intelligence | Who can do what, across clouds and SaaS? | GCP/AWS/Azure IAM, Google Workspace, Okta, Entra |
| IT Modernization | Which workloads are on end-of-life platforms and what depends on them? | Compute, GKE/Kubernetes, `eol_software` rule data |
| Impact Analysis | What breaks if this resource changes or fails? | Network, IAM `RUNS_AS`, load balancers, DNS |
| Resilience | Which critical services lack backups, redundancy or zone spread? | Cloud SQL backup config, instance groups, regions |

Platform roadmap: live `DATA_SOURCE=gcp` ingestion (architecture.md), a second LLM provider, API authentication,
Neo4j Enterprise RBAC, and additional providers in the data-source registry.
