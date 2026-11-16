"""Rules adapter over the official cartography.rules engine."""

from cartography.rules.data.rules import RULES
from cartography.rules.spec.model import Module


def test_catalogue_covers_every_installed_gcp_fact(gcp_service, platform):
    catalogue = gcp_service.rules.catalogue(platform.schema.get())
    installed = {(r.id, f.id) for r in RULES.values() for f in r.facts if f.module == Module.GCP}
    assert {(c.rule_id, c.fact_id) for c in catalogue} == installed


def test_applicable_and_skipped_with_reasons(gcp_service):
    status = gcp_service.rules_status()
    assert status["counts"]["applicable"] > 0 and status["counts"]["skipped"] > 0
    for s in status["skipped"]:
        assert s["reason"].startswith("Graph cannot satisfy this fact") and s["reason"].count("label") >= 1
    skipped = {s["fact_id"] for s in status["skipped"]}
    assert "gcp_gke_control_plane_exposed" in skipped  # no GKE data in the synthetic organisation


def test_applicable_rules_execute_and_normalise(gcp_service, platform):
    result = gcp_service.rules.run(platform.schema.get())
    assert all(e["ok"] for e in result["executed"]), [e for e in result["executed"] if not e["ok"]]
    by_fact = {}
    for f in result["findings"]:
        by_fact.setdefault(f.fact_id, set()).add(f.resource_id)
    assert by_fact["gcp_bucket_public"] == {"alt-prod-customer-exports", "alt-prod-static-assets"}
    assert "projects/alt-prod-web/zones/europe-west1-b/instances/web-frontend-01" in by_fact["gcp_instance_internet_exposed"]
    assert "projects/alt-prod-web/global/firewalls/prod-web-allow-ssh-legacy" in by_fact["gcp_unrestricted_ssh"]
    for f in result["findings"]:
        assert f.rule_id and f.fact_id and f.source == "cartography-rules" and f.evidence


def test_rules_endpoint_lists_applicable_and_skipped(api_client):
    body = api_client.get("/api/v1/usecases/cloud-security-gcp/panels/rules").json()["data"]
    assert body["counts"]["applicable"] == len(body["applicable"])
    assert body["counts"]["skipped"] == len(body["skipped"])
