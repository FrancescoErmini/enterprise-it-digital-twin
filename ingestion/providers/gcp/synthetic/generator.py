"""Deterministic synthetic Google Cloud environment for a fictional multinational.

The generator emits payloads shaped like the Google Cloud API responses that
Cartography's GCP intel module consumes (Resource Manager, Compute, IAM, Cloud
Storage, Cloud SQL, Cloud Asset Inventory). The loader then runs Cartography's
own ``transform_*`` and ``load_*`` functions over them, so every node label,
property name, id format and relationship comes from upstream code.

Nothing here talks to Google Cloud. All identifiers are fictional, all public
IPs come from documentation ranges (RFC 5737), and the organisation name says
"synthetic" so nothing can be mistaken for a real environment.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

SEED = 20261116
ORG_ID = "482915730216"
ORG_NAME = f"organizations/{ORG_ID}"
DOMAIN = "altamira-group.example"
COMPUTE = "https://www.googleapis.com/compute/v1"
CRM = "//cloudresourcemanager.googleapis.com"

FOLDERS = {
    "folders/100000000101": "Production",
    "folders/100000000102": "Testing",
    "folders/100000000103": "Development",
}

# project_id -> (folder, project number, default region, default zone)
PROJECTS: dict[str, tuple[str, str, str, str]] = {
    "alt-prod-web": ("folders/100000000101", "310000000001", "europe-west1", "europe-west1-b"),
    "alt-prod-payments": ("folders/100000000101", "310000000002", "europe-west1", "europe-west1-c"),
    "alt-prod-data": ("folders/100000000101", "310000000003", "us-central1", "us-central1-a"),
    "alt-test-integration": ("folders/100000000102", "320000000001", "europe-west4", "europe-west4-a"),
    "alt-test-qa": ("folders/100000000102", "320000000002", "europe-west4", "europe-west4-b"),
    "alt-dev-sandbox": ("folders/100000000103", "330000000001", "us-east1", "us-east1-b"),
    "alt-dev-analytics": ("folders/100000000103", "330000000002", "us-east1", "us-east1-c"),
}

# ---------------------------------------------------------------------------
# IAM roles. Permission lists are curated, representative subsets of the real
# predefined roles (the real roles carry hundreds to thousands of permissions).
# See docs/limitations.md.
# ---------------------------------------------------------------------------
_STORAGE_READ = ["storage.buckets.get", "storage.buckets.list", "storage.objects.get", "storage.objects.list"]
_STORAGE_WRITE = ["storage.objects.create", "storage.objects.update", "storage.objects.delete"]
_STORAGE_ADMIN = ["storage.buckets.create", "storage.buckets.delete", "storage.buckets.update",
                  "storage.buckets.getIamPolicy", "storage.buckets.setIamPolicy"]
_COMPUTE_VIEW = ["compute.instances.get", "compute.instances.list", "compute.firewalls.get",
                 "compute.firewalls.list", "compute.networks.get", "compute.networks.list"]
_COMPUTE_ADMIN = ["compute.instances.create", "compute.instances.delete", "compute.instances.setMetadata",
                  "compute.instances.setServiceAccount", "compute.instances.start", "compute.instances.stop"]
_FIREWALL_ADMIN = ["compute.firewalls.create", "compute.firewalls.update", "compute.firewalls.delete"]
_SQL_VIEW = ["cloudsql.instances.get", "cloudsql.instances.list"]
_SQL_CLIENT = ["cloudsql.instances.connect", "cloudsql.instances.get"]
_SQL_ADMIN = ["cloudsql.instances.update", "cloudsql.instances.delete", "cloudsql.instances.export",
              "cloudsql.users.create", "cloudsql.users.update"]
_SA_VIEW = ["iam.serviceAccounts.get", "iam.serviceAccounts.list"]
_SA_USE = ["iam.serviceAccounts.actAs"]
_SA_TOKEN = ["iam.serviceAccounts.getAccessToken", "iam.serviceAccounts.signBlob", "iam.serviceAccounts.signJwt",
             "iam.serviceAccounts.implicitDelegation", "iam.serviceAccounts.getOpenIdToken"]
_SA_ADMIN = ["iam.serviceAccounts.create", "iam.serviceAccounts.update", "iam.serviceAccounts.delete",
             "iam.serviceAccounts.setIamPolicy", "iam.serviceAccountKeys.create"]
_PROJECT_VIEW = ["resourcemanager.projects.get", "resourcemanager.projects.getIamPolicy"]
_PROJECT_IAM_ADMIN = ["resourcemanager.projects.getIamPolicy", "resourcemanager.projects.setIamPolicy"]
_ROLE_ADMIN = ["iam.roles.create", "iam.roles.update", "iam.roles.delete"]

_VIEWER = sorted(set(_PROJECT_VIEW + _COMPUTE_VIEW + _SQL_VIEW + _SA_VIEW
                     + ["storage.buckets.get", "storage.buckets.list"]))
_EDITOR = sorted(set(_VIEWER + _STORAGE_READ + _STORAGE_WRITE + _COMPUTE_ADMIN + _FIREWALL_ADMIN
                     + _SQL_CLIENT + _SQL_ADMIN + _SA_USE + ["iam.serviceAccounts.create",
                     "iam.serviceAccounts.update", "iam.serviceAccounts.delete", "iam.serviceAccountKeys.create"]))
_OWNER = sorted(set(_EDITOR + _PROJECT_IAM_ADMIN + _SA_ADMIN + _STORAGE_ADMIN + _ROLE_ADMIN))

ROLES: dict[str, tuple[str, list[str]]] = {
    "roles/owner": ("Owner", _OWNER),
    "roles/editor": ("Editor", _EDITOR),
    "roles/viewer": ("Viewer", _VIEWER),
    "roles/iam.serviceAccountUser": ("Service Account User", sorted(_SA_VIEW + _SA_USE)),
    "roles/iam.serviceAccountTokenCreator": ("Service Account Token Creator", sorted(_SA_TOKEN)),
    "roles/iam.securityReviewer": ("Security Reviewer", sorted(_PROJECT_VIEW + ["iam.roles.get", "iam.roles.list"]
                                                                + ["storage.buckets.getIamPolicy"])),
    "roles/storage.objectViewer": ("Storage Object Viewer", ["storage.objects.get", "storage.objects.list"]),
    "roles/storage.objectAdmin": ("Storage Object Admin", sorted(["storage.objects.get", "storage.objects.list"]
                                                                  + _STORAGE_WRITE)),
    "roles/storage.admin": ("Storage Admin", sorted(_STORAGE_READ + _STORAGE_WRITE + _STORAGE_ADMIN)),
    "roles/cloudsql.client": ("Cloud SQL Client", sorted(_SQL_CLIENT)),
    "roles/cloudsql.admin": ("Cloud SQL Admin", sorted(set(_SQL_VIEW + _SQL_CLIENT + _SQL_ADMIN))),
    "roles/compute.viewer": ("Compute Viewer", sorted(_COMPUTE_VIEW)),
    "roles/compute.instanceAdmin.v1": ("Compute Instance Admin (v1)", sorted(_COMPUTE_VIEW + _COMPUTE_ADMIN)),
    "roles/resourcemanager.projectIamAdmin": ("Project IAM Admin", sorted(_PROJECT_IAM_ADMIN)),
    "roles/logging.logWriter": ("Logs Writer", ["logging.logEntries.create"]),
    "roles/monitoring.metricWriter": ("Monitoring Metric Writer", ["monitoring.timeSeries.create"]),
}


def sa_email(name: str, project: str) -> str:
    return f"{name}@{project}.iam.gserviceaccount.com"


def default_compute_sa(project: str) -> str:
    return f"{PROJECTS[project][1]}-compute@developer.gserviceaccount.com"


# project -> list of (account name, display name). Default compute SAs added per project.
SERVICE_ACCOUNTS: dict[str, list[tuple[str, str]]] = {
    "alt-prod-web": [("web-frontend-sa", "Web frontend runtime"), ("web-frontend-min-sa", "Web frontend (least privilege)")],
    "alt-prod-payments": [("platform-automation-sa", "Platform automation"), ("payments-api-sa", "Payments API"),
                          ("payments-db-proxy-sa", "Payments DB proxy")],
    "alt-prod-data": [("etl-pipeline-sa", "ETL pipeline"), ("bi-reader-sa", "BI read-only"),
                      ("data-backup-sa", "Backup agent"), ("security-audit-sa", "Security audit (read-only)")],
    "alt-test-integration": [("integration-test-sa", "Integration tests"), ("test-deployer-sa", "Test deployer")],
    "alt-test-qa": [("qa-runner-sa", "QA runner")],
    "alt-dev-sandbox": [("ci-runner-sa", "CI runner"), ("sandbox-dev-sa", "Sandbox developer")],
    "alt-dev-analytics": [("notebook-sa", "Notebook runtime"), ("analytics-dev-sa", "Analytics developer")],
}
PROJECTS_WITH_DEFAULT_SA = ["alt-prod-web", "alt-dev-sandbox"]

# ---------------------------------------------------------------------------
# Networking
# ---------------------------------------------------------------------------
# project -> list of (network name, [(subnet name, region, cidr, flow logs)])
NETWORKS: dict[str, list[tuple[str, list[tuple[str, str, str, bool]]]]] = {
    "alt-prod-web": [("prod-web-vpc", [("web-frontend", "europe-west1", "10.10.0.0/20", True),
                                       ("web-backend", "europe-west1", "10.10.16.0/20", True)])],
    "alt-prod-payments": [("prod-payments-vpc", [("payments-app", "europe-west1", "10.20.0.0/20", True),
                                                 ("payments-data", "europe-west1", "10.20.16.0/20", True)])],
    "alt-prod-data": [("prod-data-vpc", [("data-processing", "us-central1", "10.30.0.0/20", True)])],
    "alt-test-integration": [("test-int-vpc", [("int-apps", "europe-west4", "10.40.0.0/20", False)])],
    "alt-test-qa": [("test-qa-vpc", [("qa-apps", "europe-west4", "10.50.0.0/20", False)])],
    "alt-dev-sandbox": [("default", [("default", "us-east1", "10.142.0.0/20", False)])],
    "alt-dev-analytics": [("dev-analytics-vpc", [("notebooks", "us-east1", "10.60.0.0/20", False)])],
}

# project -> list of firewall specs
FIREWALLS: dict[str, list[dict[str, Any]]] = {
    "alt-prod-web": [
        {"name": "prod-web-allow-internal", "network": "prod-web-vpc", "sourceRanges": ["10.0.0.0/8"],
         "allowed": [{"IPProtocol": "tcp", "ports": ["0-65535"]}, {"IPProtocol": "udp", "ports": ["0-65535"]}]},
        {"name": "prod-web-allow-https", "network": "prod-web-vpc", "sourceRanges": ["0.0.0.0/0"],
         "allowed": [{"IPProtocol": "tcp", "ports": ["443"]}], "targetTags": ["https-server"]},
        # Scenario A: legacy rule left open to the internet on SSH.
        {"name": "prod-web-allow-ssh-legacy", "network": "prod-web-vpc", "sourceRanges": ["0.0.0.0/0"],
         "allowed": [{"IPProtocol": "tcp", "ports": ["22"]}], "targetTags": ["ssh-legacy"]},
    ],
    "alt-prod-payments": [
        {"name": "prod-payments-allow-internal", "network": "prod-payments-vpc", "sourceRanges": ["10.20.0.0/16"],
         "allowed": [{"IPProtocol": "tcp", "ports": ["0-65535"]}]},
        # Control for scenario D: the database port restricted to the payments VPC range.
        {"name": "prod-payments-allow-postgres-internal", "network": "prod-payments-vpc",
         "sourceRanges": ["10.20.0.0/16"], "allowed": [{"IPProtocol": "tcp", "ports": ["5432"]}]},
        # Scenario D: temporary partner rule that opened the database port to everyone.
        {"name": "prod-payments-allow-postgres-partner-temp", "network": "prod-payments-vpc",
         "sourceRanges": ["0.0.0.0/0"], "allowed": [{"IPProtocol": "tcp", "ports": ["5432"]}],
         "targetTags": ["db-proxy"]},
    ],
    "alt-prod-data": [
        {"name": "prod-data-allow-internal", "network": "prod-data-vpc", "sourceRanges": ["10.30.0.0/16"],
         "allowed": [{"IPProtocol": "tcp", "ports": ["0-65535"]}]},
        {"name": "prod-data-allow-iap-ssh", "network": "prod-data-vpc", "sourceRanges": ["35.235.240.0/20"],
         "allowed": [{"IPProtocol": "tcp", "ports": ["22"]}]},
    ],
    "alt-test-integration": [
        {"name": "test-int-allow-internal", "network": "test-int-vpc", "sourceRanges": ["10.40.0.0/16"],
         "allowed": [{"IPProtocol": "all"}]},
    ],
    "alt-test-qa": [
        # Control: RDP restricted to the office range, not the internet.
        {"name": "test-qa-allow-rdp-office", "network": "test-qa-vpc", "sourceRanges": ["198.51.100.0/24"],
         "allowed": [{"IPProtocol": "tcp", "ports": ["3389"]}]},
    ],
    "alt-dev-sandbox": [
        {"name": "default-allow-internal", "network": "default", "sourceRanges": ["10.128.0.0/9"],
         "allowed": [{"IPProtocol": "tcp", "ports": ["0-65535"]}, {"IPProtocol": "udp", "ports": ["0-65535"]},
                     {"IPProtocol": "icmp"}]},
        {"name": "default-allow-ssh", "network": "default", "sourceRanges": ["0.0.0.0/0"],
         "allowed": [{"IPProtocol": "tcp", "ports": ["22"]}]},
    ],
    "alt-dev-analytics": [
        {"name": "dev-analytics-allow-internal", "network": "dev-analytics-vpc", "sourceRanges": ["10.60.0.0/16"],
         "allowed": [{"IPProtocol": "tcp", "ports": ["0-65535"]}]},
    ],
}

# ---------------------------------------------------------------------------
# Compute instances: (name, project, subnet, service account email, public?, labels, extras)
# ---------------------------------------------------------------------------
@dataclass
class InstanceSpec:
    name: str
    project: str
    subnet: str
    service_account: str | None
    public: bool = False
    labels: dict[str, str] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    scopes: list[str] = field(default_factory=lambda: ["https://www.googleapis.com/auth/logging.write",
                                                        "https://www.googleapis.com/auth/monitoring.write"])
    shielded: bool = True
    machine_type: str = "e2-standard-2"


def _prod(app: str, cls: str) -> dict[str, str]:
    return {"env": "prod", "app": app, "data-classification": cls}


CLOUD_PLATFORM = ["https://www.googleapis.com/auth/cloud-platform"]

INSTANCES: list[InstanceSpec] = [
    # alt-prod-web
    InstanceSpec("web-frontend-01", "alt-prod-web", "web-frontend", sa_email("web-frontend-sa", "alt-prod-web"),
                 public=True, labels=_prod("storefront", "internal"), tags=["https-server", "ssh-legacy"],
                 scopes=CLOUD_PLATFORM),
    InstanceSpec("web-frontend-02", "alt-prod-web", "web-frontend", sa_email("web-frontend-min-sa", "alt-prod-web"),
                 public=True, labels=_prod("storefront", "internal"), tags=["https-server"]),
    InstanceSpec("web-frontend-03", "alt-prod-web", "web-frontend", sa_email("web-frontend-min-sa", "alt-prod-web"),
                 labels=_prod("storefront", "internal"), tags=["https-server"]),
    InstanceSpec("web-cache-01", "alt-prod-web", "web-backend", sa_email("web-frontend-min-sa", "alt-prod-web"),
                 labels=_prod("storefront", "internal")),
    InstanceSpec("web-cache-02", "alt-prod-web", "web-backend", sa_email("web-frontend-min-sa", "alt-prod-web"),
                 labels=_prod("storefront", "internal")),
    InstanceSpec("web-batch-01", "alt-prod-web", "web-backend", default_compute_sa("alt-prod-web"),
                 labels=_prod("storefront", "internal"), scopes=CLOUD_PLATFORM),
    # alt-prod-payments (scenario E: five workloads share platform-automation-sa)
    *[InstanceSpec(f"payments-api-0{i}", "alt-prod-payments", "payments-app",
                   sa_email("platform-automation-sa", "alt-prod-payments"),
                   labels=_prod("payments", "restricted"), scopes=CLOUD_PLATFORM) for i in (1, 2, 3)],
    *[InstanceSpec(f"payments-worker-0{i}", "alt-prod-payments", "payments-app",
                   sa_email("platform-automation-sa", "alt-prod-payments"),
                   labels=_prod("payments", "restricted"), scopes=CLOUD_PLATFORM) for i in (1, 2)],
    InstanceSpec("payments-db-proxy-01", "alt-prod-payments", "payments-data",
                 sa_email("payments-db-proxy-sa", "alt-prod-payments"), public=True,
                 labels=_prod("payments", "restricted"), tags=["db-proxy"]),
    InstanceSpec("payments-batch-01", "alt-prod-payments", "payments-app",
                 sa_email("payments-api-sa", "alt-prod-payments"), labels=_prod("payments", "restricted")),
    # alt-prod-data
    InstanceSpec("etl-worker-01", "alt-prod-data", "data-processing", sa_email("etl-pipeline-sa", "alt-prod-data"),
                 labels=_prod("data-platform", "confidential")),
    InstanceSpec("etl-worker-02", "alt-prod-data", "data-processing", sa_email("etl-pipeline-sa", "alt-prod-data"),
                 labels=_prod("data-platform", "confidential")),
    InstanceSpec("warehouse-loader-01", "alt-prod-data", "data-processing",
                 sa_email("platform-automation-sa", "alt-prod-payments"),
                 labels=_prod("data-platform", "confidential"), scopes=CLOUD_PLATFORM),
    InstanceSpec("bi-gateway-01", "alt-prod-data", "data-processing", sa_email("bi-reader-sa", "alt-prod-data"),
                 labels=_prod("data-platform", "confidential")),
    InstanceSpec("backup-agent-01", "alt-prod-data", "data-processing", sa_email("data-backup-sa", "alt-prod-data"),
                 labels=_prod("data-platform", "restricted")),
    # alt-test-integration
    *[InstanceSpec(f"int-app-0{i}", "alt-test-integration", "int-apps",
                   sa_email("integration-test-sa", "alt-test-integration"),
                   labels={"env": "test", "app": "integration", "data-classification": "synthetic-test"},
                   shielded=False) for i in (1, 2, 3)],
    InstanceSpec("int-runner-01", "alt-test-integration", "int-apps",
                 sa_email("test-deployer-sa", "alt-test-integration"),
                 labels={"env": "test", "app": "integration", "data-classification": "synthetic-test"}),
    # alt-test-qa
    InstanceSpec("qa-win-01", "alt-test-qa", "qa-apps", sa_email("qa-runner-sa", "alt-test-qa"), public=True,
                 labels={"env": "test", "app": "qa", "data-classification": "synthetic-test"},
                 machine_type="n2-standard-4"),
    *[InstanceSpec(f"qa-app-0{i}", "alt-test-qa", "qa-apps", sa_email("qa-runner-sa", "alt-test-qa"),
                   labels={"env": "test", "app": "qa", "data-classification": "synthetic-test"}) for i in (1, 2)],
    # alt-dev-sandbox
    *[InstanceSpec(f"dev-vm-0{i}", "alt-dev-sandbox", "default", default_compute_sa("alt-dev-sandbox"),
                   public=(i == 1), labels={"env": "dev", "app": "sandbox", "data-classification": "none"},
                   scopes=CLOUD_PLATFORM, shielded=False) for i in (1, 2, 3)],
    InstanceSpec("ci-runner-01", "alt-dev-sandbox", "default", sa_email("ci-runner-sa", "alt-dev-sandbox"),
                 public=True, labels={"env": "dev", "app": "ci", "data-classification": "none"},
                 scopes=CLOUD_PLATFORM),
    # alt-dev-analytics
    *[InstanceSpec(f"notebook-0{i}", "alt-dev-analytics", "notebooks", sa_email("notebook-sa", "alt-dev-analytics"),
                   labels={"env": "dev", "app": "analytics", "data-classification": "none"}) for i in (1, 2)],
    InstanceSpec("analytics-dev-01", "alt-dev-analytics", "notebooks", sa_email("analytics-dev-sa", "alt-dev-analytics"),
                 labels={"env": "dev", "app": "analytics", "data-classification": "none"}),
]

# ---------------------------------------------------------------------------
# Storage and databases
# ---------------------------------------------------------------------------
# (bucket name, project, classification, public access prevention, uniform access)
BUCKETS: list[tuple[str, str, str, str, bool]] = [
    ("alt-prod-customer-exports", "alt-prod-web", "confidential", "inherited", True),     # scenario C
    ("alt-prod-static-assets", "alt-prod-web", "public", "inherited", True),              # intentionally public
    ("alt-prod-web-logs", "alt-prod-web", "internal", "enforced", True),
    ("alt-prod-payments-ledger", "alt-prod-payments", "restricted", "enforced", True),
    ("alt-prod-payments-reports", "alt-prod-payments", "restricted", "enforced", True),
    ("alt-prod-data-lake", "alt-prod-data", "confidential", "enforced", True),
    ("alt-prod-backups", "alt-prod-data", "restricted", "enforced", True),
    ("alt-test-fixtures", "alt-test-integration", "synthetic-test", "inherited", False),
    ("alt-dev-scratch", "alt-dev-sandbox", "none", "enforced", False),                    # control for C
    ("alt-dev-analytics-notebooks", "alt-dev-analytics", "none", "inherited", True),
]

# (name, project, version, public ip?, authorized networks, ssl mode, classification, service account)
SQL_INSTANCES: list[dict[str, Any]] = [
    {"name": "payments-ledger-db", "project": "alt-prod-payments", "version": "POSTGRES_15", "public": False,
     "authorized": [], "ssl_mode": "ENCRYPTED_ONLY", "classification": "restricted", "network": "prod-payments-vpc"},
    {"name": "warehouse-meta-db", "project": "alt-prod-data", "version": "MYSQL_8_0", "public": False,
     "authorized": [], "ssl_mode": "ENCRYPTED_ONLY", "classification": "confidential", "network": "prod-data-vpc"},
    {"name": "qa-db", "project": "alt-test-qa", "version": "POSTGRES_14", "public": True,
     "authorized": [{"name": "anywhere", "value": "0.0.0.0/0"}],
     "ssl_mode": "ALLOW_UNENCRYPTED_AND_ENCRYPTED", "classification": "synthetic-test", "network": None},
    {"name": "dev-db", "project": "alt-dev-sandbox", "version": "MYSQL_8_0", "public": True,
     "authorized": [{"name": "office", "value": "198.51.100.0/24"}],
     "ssl_mode": "ALLOW_UNENCRYPTED_AND_ENCRYPTED", "classification": "none", "network": None},
]

# ---------------------------------------------------------------------------
# IAM policy bindings: (attached resource, role, members)
# ---------------------------------------------------------------------------
def _sa(name: str, project: str) -> str:
    return f"serviceAccount:{sa_email(name, project)}"


def _proj(project: str) -> str:
    return f"{CRM}/projects/{project}"


def _bucket(name: str) -> str:
    return f"//storage.googleapis.com/buckets/{name}"


ORG_BINDINGS: list[tuple[str, str, list[str]]] = [
    (f"{CRM}/{ORG_NAME}", "roles/iam.securityReviewer", [_sa("security-audit-sa", "alt-prod-data")]),
    (f"{CRM}/{ORG_NAME}", "roles/owner", [f"group:cloud-platform-admins@{DOMAIN}"]),
]
FOLDER_BINDINGS: dict[str, list[tuple[str, str, list[str]]]] = {
    "folders/100000000103": [(f"{CRM}/folders/100000000103", "roles/editor", [f"group:developers@{DOMAIN}"])],
    "folders/100000000101": [(f"{CRM}/folders/100000000101", "roles/viewer", [f"group:sre@{DOMAIN}"])],
}

PROJECT_BINDINGS: dict[str, list[tuple[str, str, list[str]]]] = {
    "alt-prod-web": [
        (_proj("alt-prod-web"), "roles/editor", [_sa("web-frontend-sa", "alt-prod-web"),
                                                 _sa("platform-automation-sa", "alt-prod-payments"),
                                                 f"serviceAccount:{default_compute_sa('alt-prod-web')}"]),
        (_proj("alt-prod-web"), "roles/iam.serviceAccountTokenCreator", [_sa("web-frontend-sa", "alt-prod-web")]),
        (_proj("alt-prod-web"), "roles/logging.logWriter", [_sa("web-frontend-min-sa", "alt-prod-web")]),
        (_proj("alt-prod-web"), "roles/monitoring.metricWriter", [_sa("web-frontend-min-sa", "alt-prod-web")]),
        (_bucket("alt-prod-customer-exports"), "roles/storage.objectViewer", ["allUsers"]),
        (_bucket("alt-prod-static-assets"), "roles/storage.objectViewer", ["allUsers"]),
    ],
    "alt-prod-payments": [
        (_proj("alt-prod-payments"), "roles/owner", [_sa("platform-automation-sa", "alt-prod-payments")]),
        (_proj("alt-prod-payments"), "roles/cloudsql.client", [_sa("payments-api-sa", "alt-prod-payments"),
                                                               _sa("payments-db-proxy-sa", "alt-prod-payments"),
                                                               _sa("ci-runner-sa", "alt-dev-sandbox")]),
        (_proj("alt-prod-payments"), "roles/storage.objectAdmin", [_sa("ci-runner-sa", "alt-dev-sandbox")]),
        (_proj("alt-prod-payments"), "roles/iam.serviceAccountUser", [_sa("ci-runner-sa", "alt-dev-sandbox")]),
        (_proj("alt-prod-payments"), "roles/viewer", [_sa("bi-reader-sa", "alt-prod-data")]),
    ],
    "alt-prod-data": [
        (_proj("alt-prod-data"), "roles/editor", [_sa("platform-automation-sa", "alt-prod-payments")]),
        (_proj("alt-prod-data"), "roles/storage.admin", [_sa("web-frontend-sa", "alt-prod-web")]),
        (_proj("alt-prod-data"), "roles/storage.objectAdmin", [_sa("etl-pipeline-sa", "alt-prod-data")]),
        (_proj("alt-prod-data"), "roles/cloudsql.client", [_sa("etl-pipeline-sa", "alt-prod-data")]),
        (_proj("alt-prod-data"), "roles/storage.objectViewer", [_sa("bi-reader-sa", "alt-prod-data"),
                                                                _sa("data-backup-sa", "alt-prod-data")]),
    ],
    "alt-test-integration": [
        (_proj("alt-test-integration"), "roles/editor", [_sa("test-deployer-sa", "alt-test-integration")]),
        (_proj("alt-test-integration"), "roles/viewer", [_sa("integration-test-sa", "alt-test-integration")]),
    ],
    "alt-test-qa": [
        (_proj("alt-test-qa"), "roles/editor", [_sa("qa-runner-sa", "alt-test-qa")]),
        # Control for scenario B: a development identity reaching a *test* project, not production.
        (_proj("alt-test-qa"), "roles/viewer", [_sa("analytics-dev-sa", "alt-dev-analytics")]),
    ],
    "alt-dev-sandbox": [
        (_proj("alt-dev-sandbox"), "roles/editor", [_sa("ci-runner-sa", "alt-dev-sandbox"),
                                                    _sa("sandbox-dev-sa", "alt-dev-sandbox"),
                                                    f"serviceAccount:{default_compute_sa('alt-dev-sandbox')}"]),
        # Control for scenario C: broad grant, but public access prevention is enforced.
        (_bucket("alt-dev-scratch"), "roles/storage.objectViewer", ["allAuthenticatedUsers"]),
    ],
    "alt-dev-analytics": [
        (_proj("alt-dev-analytics"), "roles/editor", [_sa("notebook-sa", "alt-dev-analytics")]),
        (_proj("alt-dev-analytics"), "roles/viewer", [_sa("analytics-dev-sa", "alt-dev-analytics")]),
    ],
}


# ---------------------------------------------------------------------------
# Payload builders (API-shaped)
# ---------------------------------------------------------------------------
class SyntheticGcpEnvironment:
    """Builds every API-shaped payload. Pure and deterministic: same seed, same output."""

    def __init__(self, seed: int = SEED) -> None:
        self.rng = random.Random(seed)
        self._ip_pool = [f"203.0.113.{i}" for i in range(10, 250)] + [f"198.51.100.{i}" for i in range(10, 250)]
        self.rng.shuffle(self._ip_pool)
        self._internal_counter: dict[str, int] = {}

    # Resource Manager --------------------------------------------------------
    def organizations(self) -> list[dict]:
        return [{"name": ORG_NAME, "displayName": f"{DOMAIN} (synthetic)", "lifecycleState": "ACTIVE"}]

    def folders(self) -> list[dict]:
        return [{"name": fid, "parent": ORG_NAME, "displayName": name, "lifecycleState": "ACTIVE"}
                for fid, name in FOLDERS.items()]

    def projects(self) -> list[dict]:
        return [{"projectId": pid, "projectNumber": num, "name": pid, "parent": folder,
                 "lifecycleState": "ACTIVE"} for pid, (folder, num, _, _) in PROJECTS.items()]

    # IAM ---------------------------------------------------------------------
    def org_roles(self) -> list[dict]:
        return [{"name": name, "title": title, "description": f"{title} (synthetic permission subset)",
                 "includedPermissions": perms, "etag": "BwYsynthetic", "deleted": False}
                for name, (title, perms) in ROLES.items()]

    def service_accounts(self, project: str) -> list[dict]:
        accounts = []
        entries = list(SERVICE_ACCOUNTS.get(project, []))
        for idx, (name, display) in enumerate(entries):
            accounts.append(self._sa_payload(sa_email(name, project), display, project, idx))
        if project in PROJECTS_WITH_DEFAULT_SA:
            accounts.append(self._sa_payload(default_compute_sa(project), "Compute Engine default service account",
                                             project, 99))
        return accounts

    @staticmethod
    def _sa_payload(email: str, display: str, project: str, idx: int) -> dict:
        unique_id = f"1{PROJECTS[project][1]}{idx:02d}"  # deterministic 21-digit-like id
        return {"name": f"projects/{project}/serviceAccounts/{email}", "projectId": project,
                "uniqueId": unique_id, "email": email, "displayName": display,
                "oauth2ClientId": unique_id, "disabled": False}

    # Compute -----------------------------------------------------------------
    def vpcs(self, project: str) -> dict:
        prefix = f"projects/{project}/global/networks"
        return {"id": prefix, "items": [
            {"name": net, "selfLink": f"{COMPUTE}/{prefix}/{net}", "autoCreateSubnetworks": net == "default",
             "routingConfig": {"routingMode": "REGIONAL"}, "description": f"{net} (synthetic)"}
            for net, _ in NETWORKS[project]]}

    def subnets(self, project: str) -> list[dict]:
        out: dict[str, list[dict]] = {}
        for net, subnets in NETWORKS[project]:
            for name, region, cidr, flow_logs in subnets:
                prefix = f"projects/{project}/regions/{region}/subnetworks"
                item = {"name": name, "network": f"{COMPUTE}/projects/{project}/global/networks/{net}",
                        "region": f"{COMPUTE}/projects/{project}/regions/{region}", "ipCidrRange": cidr,
                        "gatewayAddress": cidr.split("/")[0][:-1] + "1",
                        "selfLink": f"{COMPUTE}/{prefix}/{name}", "privateIpGoogleAccess": True,
                        "enableFlowLogs": flow_logs}
                if flow_logs:
                    item["logConfig"] = {"aggregationInterval": "INTERVAL_5_SEC", "flowSampling": 0.5,
                                         "metadata": "INCLUDE_ALL_METADATA"}
                out.setdefault(prefix, []).append(item)
        return [{"id": prefix, "items": items} for prefix, items in sorted(out.items())]

    def firewalls(self, project: str) -> dict:
        prefix = f"projects/{project}/global/firewalls"
        items = []
        for idx, fw in enumerate(FIREWALLS.get(project, [])):
            item = {k: v for k, v in fw.items() if k != "network"}
            item.update({"network": f"{COMPUTE}/projects/{project}/global/networks/{fw['network']}",
                         "direction": "INGRESS", "disabled": False, "priority": 1000 + idx,
                         "selfLink": f"{COMPUTE}/{prefix}/{fw['name']}"})
            items.append(item)
        return {"id": prefix, "items": items}

    def _subnet_lookup(self, project: str, subnet: str) -> tuple[str, str, str]:
        for net, subnets in NETWORKS[project]:
            for name, region, cidr, _ in subnets:
                if name == subnet:
                    return net, region, cidr
        raise KeyError(f"unknown subnet {project}/{subnet}")

    def _next_internal_ip(self, cidr: str) -> str:
        n = self._internal_counter.get(cidr, 10) + 1
        self._internal_counter[cidr] = n
        base = cidr.split("/")[0].rsplit(".", 2)[0]
        return f"{base}.{n // 250}.{n % 250}"

    def instances(self, project: str) -> list[dict]:
        zone = PROJECTS[project][3]
        prefix = f"projects/{project}/zones/{zone}/instances"
        items = []
        for spec in (s for s in INSTANCES if s.project == project):
            net, region, cidr = self._subnet_lookup(project, spec.subnet)
            nic: dict[str, Any] = {
                "name": "nic0",
                "network": f"{COMPUTE}/projects/{project}/global/networks/{net}",
                "subnetwork": f"{COMPUTE}/projects/{project}/regions/{region}/subnetworks/{spec.subnet}",
                "networkIP": self._next_internal_ip(cidr),
            }
            if spec.public:
                nic["accessConfigs"] = [{"type": "ONE_TO_ONE_NAT", "name": "External NAT",
                                         "natIP": self._ip_pool.pop(), "networkTier": "PREMIUM"}]
            item = {
                "name": spec.name,
                "id": str(self.rng.randrange(10**18, 10**19)),
                "selfLink": f"{COMPUTE}/{prefix}/{spec.name}",
                "hostname": f"{spec.name}.{zone}.c.{project}.internal",
                "status": "RUNNING",
                "machineType": f"{COMPUTE}/projects/{project}/zones/{zone}/machineTypes/{spec.machine_type}",
                "creationTimestamp": "2026-03-02T09:00:00.000-08:00",
                "networkInterfaces": [nic],
                "labels": dict(spec.labels),
                "tags": {"items": list(spec.tags)} if spec.tags else {},
                "canIpForward": False,
                "shieldedInstanceConfig": {"enableVtpm": spec.shielded, "enableIntegrityMonitoring": spec.shielded,
                                           "enableSecureBoot": spec.shielded},
                "confidentialInstanceConfig": {"enableConfidentialCompute": False},
                "metadata": {"items": [{"key": "enable-oslogin", "value": "TRUE"},
                                       {"key": "block-project-ssh-keys", "value": "true" if spec.project.startswith("alt-prod") else "false"}]},
            }
            if spec.service_account:
                item["serviceAccounts"] = [{"email": spec.service_account, "scopes": list(spec.scopes)}]
            items.append(item)
        return [{"id": prefix, "items": items}]

    # Storage -----------------------------------------------------------------
    def buckets(self, project: str) -> dict:
        items = []
        for name, proj, cls, pap, uniform in BUCKETS:
            if proj != project:
                continue
            items.append({
                "kind": "storage#bucket", "id": name, "name": name,
                "selfLink": f"https://www.googleapis.com/storage/v1/b/{name}",
                "projectNumber": PROJECTS[project][1], "location": PROJECTS[project][2].upper(),
                "locationType": "region", "storageClass": "STANDARD", "metageneration": "1",
                "timeCreated": "2026-02-10T08:00:00.000Z",
                "iamConfiguration": {"bucketPolicyOnly": {"enabled": uniform},
                                     "uniformBucketLevelAccess": {"enabled": uniform},
                                     "publicAccessPrevention": pap},
                "versioning": {"enabled": cls in ("restricted", "confidential")},
                "labels": {"data-classification": cls, "env": project.split("-")[1]},
            })
        return {"items": items}

    # Cloud SQL ---------------------------------------------------------------
    def sql_instances(self, project: str) -> list[dict]:
        out = []
        region = PROJECTS[project][2]
        for spec in (s for s in SQL_INSTANCES if s["project"] == project):
            self_link = f"https://sqladmin.googleapis.com/sql/v1beta4/projects/{project}/instances/{spec['name']}"
            ip_addresses = [{"type": "PRIVATE", "ipAddress": self._next_internal_ip("10.250.0.0/16")}]
            if spec["public"]:
                ip_addresses.append({"type": "PRIMARY", "ipAddress": self._ip_pool.pop()})
            ip_config: dict[str, Any] = {"ipv4Enabled": spec["public"], "sslMode": spec["ssl_mode"],
                                         "requireSsl": spec["ssl_mode"] == "ENCRYPTED_ONLY",
                                         "authorizedNetworks": spec["authorized"]}
            if spec["network"]:
                ip_config["privateNetwork"] = f"projects/{project}/global/networks/{spec['network']}"
            out.append({
                "selfLink": self_link, "name": spec["name"], "databaseVersion": spec["version"],
                "region": region, "gceZone": PROJECTS[project][3], "state": "RUNNABLE",
                "backendType": "SECOND_GEN", "connectionName": f"{project}:{region}:{spec['name']}",
                "serviceAccountEmailAddress": f"p{PROJECTS[project][1]}-sql@gcp-sa-cloud-sql.iam.gserviceaccount.com",
                "ipAddresses": ip_addresses,
                "settings": {"tier": "db-custom-2-7680", "dataDiskSizeGb": "100", "dataDiskType": "PD_SSD",
                             "availabilityType": "REGIONAL" if project.startswith("alt-prod") else "ZONAL",
                             "backupConfiguration": {"enabled": project.startswith("alt-prod")},
                             "ipConfiguration": ip_config,
                             "userLabels": {"data-classification": spec["classification"]}},
            })
        return out

    # Cloud Asset Inventory ---------------------------------------------------
    def policy_results(self, project: str) -> dict:
        """Shaped like Cartography's get_policy_bindings() output: the effective
        policies for one project, including those inherited from its folder and org."""
        folder = PROJECTS[project][0]
        policies: dict[str, dict[str, list[str]]] = {}
        for resource, role, members in (ORG_BINDINGS + FOLDER_BINDINGS.get(folder, [])
                                        + PROJECT_BINDINGS.get(project, [])):
            policies.setdefault(resource, {}).setdefault(role, []).extend(members)
        results = []
        for resource, roles in policies.items():
            results.append({"policies": [{"attached_resource": resource, "policy": {"bindings": [
                {"role": role, "members": sorted(set(members))} for role, members in roles.items()]}}]})
        return {"project_id": project, "policy_results": results}
