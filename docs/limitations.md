# Limitations

## Cartography gaps (what the graph cannot tell, and what that prevents)

| Gap | Consequence | How the POC handles it |
|---|---|---|
| No environment attribute on `GCPProject`; project labels are not ingested | Trust level cannot be read directly | Inferred from the Resource Manager folder (`GCPFolder.displayname`) |
| No data-sensitivity concept | Sensitivity depends on user labels | `GCPLabel` key `data-classification`; unlabelled resources get a neutral weight |
| Upstream exposure rules evaluate firewalls VPC-wide and ignore target tags / target service accounts | Possible over-reporting of exposed instances (e.g. `web-frontend-02`) | Kept as reported by the official rule; noted in each finding's missing evidence |
| Runtime state (listening services, processes, patch level) is not inventoried | Exposure can never be proven exploitable | Everything reachable-by-configuration is `potential_exposure` |
| Org policies, VPC Service Controls, IAM deny policies, IAM condition evaluation | Access the graph shows may be blocked at request time | Missing-evidence entries; conditional bindings keep `has_condition` |
| Human principals require the Google Workspace module | `user:` / `group:` members have no `HAS_ALLOW_POLICY` edge | Only service-account paths are analysed |
| Legacy `GCPBucketLabel` nodes are keyed by label key only (`GCPBucket_<key>`), so values overwrite each other across buckets | Unreliable for classification | The use case reads `GCPLabel` (`<resource>:<key>:<value>`) instead |
| `GCPIpRange` ids are the CIDR, shared across projects | One `0.0.0.0/0` node fans out to every rule in every project | Harmless for queries; noted for graph readers |
| Policy binding sync needs the Cloud Asset API in live mode | Without it `APPLIES_TO` and `CAN_*` edges are missing | Synthetic mode feeds the transform directly |
| No IAM usage data (Recommender, audit logs) | Cannot tell which granted permissions are used | Remediation favours staged changes with validation and rollback |

## Rules that cannot run on the synthetic data

11 of 51 GCP rule facts are skipped at startup because their labels are absent: GKE (2), Cloud DNS (3), BigQuery
(3), KMS (1), Cloud Run (1), Cloud Functions (1). They are listed with reasons by
`GET /api/v1/usecases/cloud-security-gcp/panels/rules` and in the API startup log. Adding those resource types to
the generator would make them applicable without code changes.

## Discrepancies with the brief (upstream wins)

- **Read-only user on Community Edition.** Neo4j Community has no RBAC, so the dedicated `twin_reader` user cannot
  be restricted by role. Read-only enforcement is server-side through READ access-mode transactions (writes are
  refused by Neo4j, proven by tests that bypass the validator). An attacker with the reader password and a
  client that opens WRITE transactions could write; on Enterprise Edition grant the built-in `reader` role.
- **Cartography and Python 3.12.** Cartography 0.144 warns it is tested on 3.13+; images use 3.13.
- **"Constraint creation is idempotent".** Cartography creates indexes, not constraints. The gate checks that index
  counts are identical across runs.

## Synthetic-data limitations

- Predefined role permission lists are curated subsets of the real roles (which have hundreds to thousands of
  permissions). Scores are therefore a lower bound on real privilege.
- One subnet / NIC per instance, no instance groups, load balancers, GKE, Cloud Run, BigQuery or KMS.
- The same seed always produces the same environment; it does not drift like a real estate.

## Platform limitations and production evolution

| Area | POC state | Production evolution |
|---|---|---|
| Live data | Synthetic only | `DATA_SOURCE=gcp` running Cartography with read-only collector roles (see architecture.md) |
| LLM | OpenAI only; the agent needs a working key | Additional `ChatModelProvider`s (e.g. Ollama for on-prem), response caching of tool results only |
| AuthN/Z on the API | None (bound to 127.0.0.1) | OIDC in front of the API, per-user audit identity |
| Neo4j | Community, single instance | Enterprise with RBAC (`reader` role), backups, TLS on Bolt |
| Audit | JSON lines in a volume | Ship to a SIEM; tamper-evident storage |
| Rules | Cartography GCP facts, experimental included | Configurable maturity filter (`--no-experimental` equivalent exists in the adapter) |
| Scoring | Static weights | Calibrate weights with incident data; per-organisation profiles |
| Private API | `cartography.rules.runners._run_fact` and two `policy_bindings` helpers | Upstream a public runner API, or wrap the CLI's JSON output |
