# Security scenarios (POC 01)

All data is synthetic. Each scenario has a declarative spec in
`ingestion/providers/gcp/synthetic/scenarios/` (evidence node ids, concern, missing evidence, expected agent
behaviour, remediation options with operational consequences, and control resources). This page summarises them;
the YAML files are authoritative and are checked by `tests/test_scenarios.py` and `tests/test_graph_integrity.py`.

Fictional organisation: `organizations/482915730216` ("altamira-group.example (synthetic)"), folders Production,
Testing, Development, seven projects (`alt-prod-web`, `alt-prod-payments`, `alt-prod-data`, `alt-test-integration`,
`alt-test-qa`, `alt-dev-sandbox`, `alt-dev-analytics`), 32 instances, 18 service accounts, 10 buckets, 4 Cloud SQL
instances, 13 firewalls. Public IPs use RFC 5737 documentation ranges.

| | Scenario | Detector | Primary resource | Classification | Upstream rules used |
|---|---|---|---|---|---|
| A | Public VM with excessive IAM privileges | `public_workload_privileged_identity` | `web-frontend-01` | potential_exposure | `compute_instance_exposed`, privilege rules |
| B | Cross-project privilege exposure | `cross_environment_privilege` | `ci-runner-sa@alt-dev-sandbox` (`133000000000100`) | potential_exposure | privilege rules |
| C | Publicly accessible sensitive storage | `public_sensitive_storage` | `alt-prod-customer-exports` | potential_exposure | `object_storage_public` |
| D | Excessively permissive network configuration | `permissive_network_sensitive_service` | `prod-payments-allow-postgres-partner-temp` | potential_exposure | `compute_instance_exposed` |
| E | Risky remediation dependency | `shared_privileged_identity` | `platform-automation-sa@alt-prod-payments` (`131000000000200`) | verified_fact | privilege rules |

## A - Public VM with excessive IAM privileges
`web-frontend-01` (Production, public IP, `prod-web-allow-ssh-legacy` allows tcp/22 from 0.0.0.0/0) runs as
`web-frontend-sa`, which holds `roles/editor` and `roles/iam.serviceAccountTokenCreator` on `alt-prod-web` and
`roles/storage.admin` on `alt-prod-data`. **Missing evidence:** whether sshd listens, host-level controls,
vulnerabilities, tag scoping. **Options:** close SSH to IAP only (low), dedicated least-privilege identity (medium),
remove broad grants (medium). **Control:** `web-frontend-02` is equally public but runs as `web-frontend-min-sa`
(logging/monitoring writer only).

## B - Cross-project privilege exposure
`ci-runner-sa` belongs to `alt-dev-sandbox` (Development) and holds `storage.objectAdmin`, `cloudsql.client` and
`iam.serviceAccountUser` on `alt-prod-payments`; `CAN_IMPERSONATE` reaches `platform-automation-sa` (owner of
payments), an *unverified hypothesis* escalation path. It also runs on the public `ci-runner-01`.
**Missing evidence:** keys outside GCP, CI trigger rights, request-time conditions. **Options:** production-owned
deployer identity (medium), immediate revocation (high), conditional grants while migrating (low, partial).
**Controls:** `analytics-dev-sa` (dev identity with viewer on a *Testing* project), `bi-reader-sa` (prod to prod).

## C - Publicly accessible sensitive storage
`alt-prod-customer-exports` is labelled `data-classification=confidential`, has `roles/storage.objectViewer` for
`allUsers` and public access prevention `inherited`. **Missing evidence:** object contents, org policy, access logs.
**Options:** remove binding + enforce PAP (medium), signed URLs (medium). **Controls:** `alt-prod-static-assets`
(public by design, classification `public`; the upstream rule still reports it), `alt-dev-scratch`
(`allAuthenticatedUsers` binding but PAP enforced).

## D - Excessively permissive network configuration
`prod-payments-allow-postgres-partner-temp` allows tcp/5432 from 0.0.0.0/0 on `prod-payments-vpc`, where
`payments-db-proxy-01` (restricted, public IP) sits. **Missing evidence:** listener on 5432, tag scoping, DB auth.
**Options:** restrict to partner CIDRs (low), remove and use private connectivity (high). **Controls:**
`prod-payments-allow-postgres-internal` (10.20.0.0/16), `test-qa-allow-rdp-office` (198.51.100.0/24).

## E - Risky remediation dependency
Six production workloads in two projects (`payments-api-01..03`, `payments-worker-01..02`, `warehouse-loader-01`)
run as `platform-automation-sa`, which holds `roles/owner` on `alt-prod-payments` and `roles/editor` on
`alt-prod-web` and `alt-prod-data`. **Missing evidence:** per-workload permission usage, deployment dependencies.
**Options:** immediate revocation (high: six workloads at once), per-workload identities then revoke (low per step,
recommended), deny policy on the most dangerous permissions first (low, partial). **Control:**
`web-frontend-min-sa`, shared by four workloads but low privilege.

## Other findings the detectors report

The detectors also surface genuine but lower-ranked findings that are not one of the five scripted scenarios:
`ci-runner-01` and `dev-vm-01` (public dev VMs whose identities hold editor), and shared editor identities in
`alt-dev-sandbox` (default compute service account) and `alt-test-qa` (`qa-runner-sa`). They score lower because
their environments weigh less.
