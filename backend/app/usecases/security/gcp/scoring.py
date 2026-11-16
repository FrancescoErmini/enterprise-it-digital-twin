"""Deterministic risk scoring for the GCP security use case.

The LLM never produces these numbers: it receives them from tools and explains
them. Same graph in, same scores out (no randomness, no time dependence, stable
ordering).

risk_score = 100 * confidence_factor * sum(weight_i * factor_i), factors in [0, 1]

Factors
  exposure              how reachable the starting point is (internet > cross-environment grant > internal)
  privilege_scope       strongest capability held (IAM admin > impersonation > data write > data read)
  resource_sensitivity  max data-classification x environment weight over affected resources
  blast_radius          distinct resources and projects reachable from the starting point (capped)
  affected_resources    number of resources directly involved (capped)
  operational_impact    workloads that depend on the identity or resource (compromise and fix both hit them)
Confidence
  verified_fact 1.00, potential_exposure 0.90, unverified_hypothesis 0.60
"""

from __future__ import annotations

from typing import Iterable

WEIGHTS: dict[str, float] = {
    "exposure": 0.25,
    "privilege_scope": 0.20,
    "resource_sensitivity": 0.20,
    "blast_radius": 0.15,
    "affected_resources": 0.10,
    "operational_impact": 0.10,
}

CONFIDENCE_FACTOR: dict[str, float] = {
    "verified_fact": 1.0,
    "potential_exposure": 0.9,
    "unverified_hypothesis": 0.6,
}

CLASSIFICATION_WEIGHT: dict[str, float] = {
    "restricted": 1.0, "confidential": 0.8, "internal": 0.5, "public": 0.2, "synthetic-test": 0.2, "none": 0.1,
}
UNLABELLED_WEIGHT = 0.3

# Environment trust comes from the Resource Manager folder a project sits in.
ENVIRONMENT_RANK: dict[str, int] = {"Development": 1, "Testing": 2, "Production": 3}
ENVIRONMENT_WEIGHT: dict[str, float] = {"Production": 1.0, "Testing": 0.5, "Development": 0.3}
UNKNOWN_ENVIRONMENT_WEIGHT = 0.5

BLAST_RADIUS_CAP = 15
AFFECTED_CAP = 12
WORKLOAD_CAP = 6

# Permission tiers used for privilege_scope (first matching tier wins).
PRIVILEGE_TIERS: list[tuple[float, str, tuple[str, ...]]] = [
    (1.0, "iam_admin", ("resourcemanager.projects.setIamPolicy", "resourcemanager.folders.setIamPolicy",
                        "resourcemanager.organizations.setIamPolicy", "iam.roles.create", "iam.roles.update",
                        "iam.serviceAccounts.setIamPolicy", "storage.buckets.setIamPolicy")),
    (0.8, "impersonation", ("iam.serviceAccounts.actAs", "iam.serviceAccounts.getAccessToken",
                            "iam.serviceAccounts.implicitDelegation", "iam.serviceAccounts.signBlob",
                            "iam.serviceAccounts.signJwt", "iam.serviceAccountKeys.create")),
    (0.6, "data_or_infra_write", ("storage.objects.create", "storage.objects.delete", "storage.objects.update",
                                  "cloudsql.instances.update", "cloudsql.instances.export",
                                  "compute.instances.setMetadata", "compute.firewalls.create",
                                  "compute.firewalls.update", "compute.instances.setServiceAccount")),
    (0.3, "data_read", ("storage.objects.get", "cloudsql.instances.connect", "storage.objects.list")),
]
BASELINE_PRIVILEGE = 0.1

DISRUPTION_RANK = {"low": 1, "medium": 2, "high": 3}


def privilege_scope(permissions: Iterable[str]) -> tuple[float, str]:
    perms = set(permissions)
    for value, name, tier in PRIVILEGE_TIERS:
        if perms.intersection(tier):
            return value, name
    return BASELINE_PRIVILEGE, "baseline"


def sensitivity(classification: str | None, environment: str | None) -> float:
    cls = CLASSIFICATION_WEIGHT.get(classification or "", UNLABELLED_WEIGHT)
    env = ENVIRONMENT_WEIGHT.get(environment or "", UNKNOWN_ENVIRONMENT_WEIGHT)
    return round(cls * env, 4)


def capped(n: int, cap: int) -> float:
    return round(min(1.0, n / cap), 4)


def risk_score(factors: dict[str, float], classification: str) -> float:
    missing = set(WEIGHTS) - set(factors)
    if missing:
        raise ValueError(f"Missing scoring factors: {sorted(missing)}")
    weighted = sum(WEIGHTS[k] * max(0.0, min(1.0, factors[k])) for k in WEIGHTS)
    return round(100 * CONFIDENCE_FACTOR[classification] * weighted, 1)


def severity(score: float) -> str:
    if score >= 70:
        return "critical"
    if score >= 55:
        return "high"
    if score >= 40:
        return "medium"
    return "low"


def priority_score(risk: float, recommended_disruption: str) -> float:
    """Remediation priority: highest risk first, cheaper fixes slightly ahead at equal risk."""
    return round(risk - 3.0 * (DISRUPTION_RANK.get(recommended_disruption, 2) - 1), 1)


def profile() -> dict:
    return {"weights": WEIGHTS, "confidence_factor": CONFIDENCE_FACTOR,
            "classification_weight": CLASSIFICATION_WEIGHT, "environment_weight": ENVIRONMENT_WEIGHT,
            "caps": {"blast_radius": BLAST_RADIUS_CAP, "affected_resources": AFFECTED_CAP,
                     "operational_impact_workloads": WORKLOAD_CAP},
            "privilege_tiers": [{"value": v, "tier": n, "permissions": list(p)} for v, n, p in PRIVILEGE_TIERS],
            "severity_thresholds": {"critical": 70, "high": 55, "medium": 40}}
