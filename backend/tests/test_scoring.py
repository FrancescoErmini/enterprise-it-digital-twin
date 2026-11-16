"""Deterministic scoring."""

import pytest

from app.usecases.security.gcp import scoring


def test_identical_scores_across_repeated_runs(gcp_service):
    runs = []
    for _ in range(3):
        findings, _ = gcp_service.investigate()
        runs.append([(f.finding_id, f.risk_score, tuple(sorted(f.factors.items()))) for f in findings])
    assert runs[0] == runs[1] == runs[2]
    assert len(runs[0]) >= 5


def test_remediation_plan_is_deterministic(gcp_service):
    a = gcp_service.remediation_plan()
    b = gcp_service.remediation_plan()
    assert [(i["finding_id"], i["priority_score"]) for i in a["items"]] == \
        [(i["finding_id"], i["priority_score"]) for i in b["items"]]


def test_weights_sum_to_one():
    assert sum(scoring.WEIGHTS.values()) == pytest.approx(1.0)


def test_risk_score_formula():
    factors = {k: 1.0 for k in scoring.WEIGHTS}
    assert scoring.risk_score(factors, "verified_fact") == 100.0
    assert scoring.risk_score(factors, "potential_exposure") == 90.0
    assert scoring.risk_score(factors, "unverified_hypothesis") == 60.0
    with pytest.raises(ValueError):
        scoring.risk_score({"exposure": 1.0}, "verified_fact")


def test_privilege_tiers():
    assert scoring.privilege_scope(["resourcemanager.projects.setIamPolicy"]) == (1.0, "iam_admin")
    assert scoring.privilege_scope(["iam.serviceAccounts.actAs"]) == (0.8, "impersonation")
    assert scoring.privilege_scope(["storage.objects.delete"]) == (0.6, "data_or_infra_write")
    assert scoring.privilege_scope(["storage.objects.get"]) == (0.3, "data_read")
    assert scoring.privilege_scope(["logging.logEntries.create"]) == (0.1, "baseline")


def test_sensitivity_uses_environment():
    assert scoring.sensitivity("restricted", "Production") == 1.0
    assert scoring.sensitivity("restricted", "Development") == 0.3
    assert scoring.sensitivity(None, None) == scoring.UNLABELLED_WEIGHT * scoring.UNKNOWN_ENVIRONMENT_WEIGHT
