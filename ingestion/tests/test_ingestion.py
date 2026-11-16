"""graph-ingestion: deterministic generator, idempotent re-seed, reader user bootstrap.

    docker compose run --rm graph-ingestion pytest -q
"""

import json
import os
import time

import neo4j
import pytest

from graph_ingestion.manifest import graph_counts
from providers.gcp.synthetic.generator import INSTANCES, PROJECTS, SyntheticGcpEnvironment
from providers.gcp.synthetic.loader import load_synthetic_gcp


def _secret(name):
    path = os.environ.get(f"{name}_FILE")
    if path and os.path.exists(path):
        return open(path).read().strip()
    return os.environ.get(name, "")


@pytest.fixture(scope="module")
def driver():
    d = neo4j.GraphDatabase.driver(os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
                                   auth=(os.environ.get("NEO4J_USER", "neo4j"), _secret("NEO4J_PASSWORD")))
    try:
        d.verify_connectivity()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Neo4j not reachable: {exc}")
    yield d
    d.close()


def _payload_dump(env: SyntheticGcpEnvironment) -> str:
    out = {"orgs": env.organizations(), "folders": env.folders(), "projects": env.projects(),
           "roles": env.org_roles()}
    for p in PROJECTS:
        out[p] = {"sa": env.service_accounts(p), "vpcs": env.vpcs(p), "subnets": env.subnets(p),
                  "fw": env.firewalls(p), "instances": env.instances(p), "buckets": env.buckets(p),
                  "sql": env.sql_instances(p), "iam": env.policy_results(p)}
    return json.dumps(out, sort_keys=True)


def test_generator_is_deterministic():
    assert _payload_dump(SyntheticGcpEnvironment()) == _payload_dump(SyntheticGcpEnvironment())


def test_generator_marks_data_synthetic():
    env = SyntheticGcpEnvironment()
    assert "synthetic" in env.organizations()[0]["displayName"]
    ips = [ac["natIP"] for p in PROJECTS for res in env.instances(p) for i in res["items"]
           for nic in i["networkInterfaces"] for ac in nic.get("accessConfigs", [])]
    assert ips and all(ip.startswith(("203.0.113.", "198.51.100.")) for ip in ips)  # RFC 5737 documentation ranges


def test_target_shape():
    assert 6 <= len(PROJECTS) <= 8 and 25 <= len(INSTANCES) <= 40


def test_reseed_is_idempotent(driver):
    with driver.session() as session:
        load_synthetic_gcp(session, int(time.time()))
        first = graph_counts(session)
        load_synthetic_gcp(session, int(time.time()) + 1)
        second = graph_counts(session)
    for key in ("nodes", "relationships", "indexes", "nodes_by_label", "relationships_by_type"):
        assert first[key] == second[key], key


def test_reader_user_exists(driver):
    with driver.session(database="system") as session:
        users = [r["user"] for r in session.run("SHOW USERS YIELD user RETURN user")]
    assert os.environ.get("NEO4J_READER_USER", "twin_reader") in users
