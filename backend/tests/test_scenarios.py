"""Scenario detection: all five scenarios found from live graph + rules; control resources not flagged."""

import pytest


@pytest.fixture(scope="module")
def findings(gcp_service):
    found, _ = gcp_service.investigate()
    return found


def test_all_five_scenarios_detected(findings, scenario_specs):
    for spec in scenario_specs:
        matches = [f for f in findings if f.scenario == spec["id"] and f.primary_resource["id"] == spec["primary_resource"]["id"]]
        assert matches, f"Scenario {spec['id']} ({spec['title']}) not detected for {spec['primary_resource']['id']}"
        finding = matches[0]
        assert finding.classification == spec["classification"], spec["id"]
        assert finding.detector == spec["detector"]


def test_scenario_evidence_reaches_spec_resources(findings, scenario_specs):
    """The evidence the spec declares must appear in the finding's affected resources or graph paths."""
    for spec in scenario_specs:
        finding = next(f for f in findings if f.scenario == spec["id"]
                       and f.primary_resource["id"] == spec["primary_resource"]["id"])
        cited = set(finding.affected_resource_ids) | {n for p in finding.graph_paths for n in p}
        cited |= {v for v in finding.details.values() if isinstance(v, str)}
        cited |= {x for v in finding.details.values() if isinstance(v, list) for x in v if isinstance(x, str)}
        missing = [e["id"] for e in spec["evidence"] if e["id"] not in cited]
        assert not missing, f"Scenario {spec['id']}: evidence not cited by the finding: {missing}"


def test_controls_not_flagged(findings, scenario_specs):
    for spec in scenario_specs:
        flagged = {f.primary_resource["id"] for f in findings if f.scenario == spec["id"]}
        for control in spec["controls"]:
            assert control["id"] not in flagged, f"Control {control['id']} wrongly flagged as scenario {spec['id']}"


def test_classifications_are_explicit(findings):
    assert {f.classification for f in findings} <= {"verified_fact", "potential_exposure", "unverified_hypothesis"}
    for f in findings:
        assert f.missing_evidence, f"{f.finding_id} has no missing-evidence statement"


def test_potential_paths_never_called_proven(findings):
    for f in findings:
        text = " ".join([f.title, *f.facts]).lower()
        assert "exploited" not in text and "proven" not in text and "confirmed attack" not in text


def test_every_remediation_assessment_is_complete(gcp_service):
    plan = gcp_service.remediation_plan()
    assert plan["items"]
    required = {"problem", "affected_resources", "evidence_paths", "potential_impact", "recommended_mitigation",
                "alternative_mitigation", "trade_offs", "validation_steps", "rollback", "confidence",
                "missing_information"}
    for item in plan["items"]:
        assert required <= set(item), item["finding_id"]
        assert item["recommended_mitigation"] and item["alternative_mitigation"], item["finding_id"]
        assert item["executes_changes"] is False


def test_shared_identity_remediation_prefers_staged_split(gcp_service):
    findings, snap = gcp_service.investigate(["E"])
    from app.usecases.security.gcp import remediation

    e = next(f for f in findings if f.primary_resource["id"] == "131000000000200")
    a = remediation.assess(e, snap)
    assert a["recommended_mitigation"]["id"] == "E2"
    immediate = next(o for o in a["options"] if o["id"] == "E1")
    assert immediate["disruption"] == "high" and len(immediate["workloads_affected"]) == 6
