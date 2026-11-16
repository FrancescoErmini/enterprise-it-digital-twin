"""Load the synthetic GCP environment through Cartography's own transform and load code.

Every write goes through a function from ``cartography.intel.gcp`` (or the
analysis jobs Cartography runs after a GCP sync). This module only decides
*what* to feed those functions; it never writes Cypher of its own, so it cannot
invent a label, property or relationship.
"""

from __future__ import annotations

import logging
from typing import Any

import neo4j
from cartography.analysis.gcp.analysis import GCP_BUCKET_PUBLIC_PROJECTION, GCP_COMPUTE_INSTANCE_VPC_ANALYSIS
from cartography.intel.gcp import iam, labels, permission_relationships, policy_bindings, storage
from cartography.intel.gcp import cloud_sql_instance, compute
from cartography.intel.gcp.crm import folders as crm_folders
from cartography.intel.gcp.crm import orgs as crm_orgs
from cartography.intel.gcp.crm import projects as crm_projects
from cartography.util import run_analysis_job, run_typed_analysis_job

from providers.gcp.synthetic.generator import ORG_NAME, PROJECTS, SyntheticGcpEnvironment

logger = logging.getLogger(__name__)


def _permission_relationships_file() -> str:
    import importlib.resources

    return str(importlib.resources.files("cartography") / "data" / "gcp_permission_relationships.yaml")


def load_synthetic_gcp(session: neo4j.Session, update_tag: int, seed: int | None = None) -> dict[str, Any]:
    env = SyntheticGcpEnvironment() if seed is None else SyntheticGcpEnvironment(seed)
    common = {"UPDATE_TAG": update_tag, "ORG_RESOURCE_NAME": ORG_NAME,
              "gcp_permission_relationships_file": _permission_relationships_file()}

    # Resource hierarchy
    crm_orgs.load_gcp_organizations(session, env.organizations(), update_tag)
    crm_folders.load_gcp_folders(session, crm_folders.transform_gcp_folders(env.folders()), update_tag, ORG_NAME)
    crm_projects.load_gcp_projects(session, crm_projects.transform_gcp_projects(env.projects()), update_tag, ORG_NAME)

    # Organisation-scoped (predefined) roles
    raw_roles = env.org_roles()
    iam.load_org_roles(session, iam.transform_org_roles(raw_roles), ORG_NAME, update_tag)
    role_permissions = iam.build_role_permissions_by_name(raw_roles)

    claim_state = policy_bindings.InheritedPolicyBindingClaimState()
    stats: dict[str, Any] = {"projects": {}}

    # Per-project inventory, in the order Cartography's GCP sync uses.
    for project_id in PROJECTS:
        iam.load_gcp_service_accounts(
            session, iam.transform_gcp_service_accounts(env.service_accounts(project_id), project_id),
            project_id, update_tag)

        compute.load_gcp_vpcs(session, compute.transform_gcp_vpcs(env.vpcs(project_id)), update_tag, project_id)
        for subnet_res in env.subnets(project_id):
            compute.load_gcp_subnets(session, compute.transform_gcp_subnets(subnet_res), update_tag, project_id)
        compute.load_gcp_ingress_firewalls(
            session, compute.transform_gcp_firewall(env.firewalls(project_id)), update_tag, project_id)
        instances = compute.transform_gcp_instances(env.instances(project_id))
        compute.load_gcp_instances(session, instances, update_tag, project_id)
        _load_labels(session, instances, "gcp_instance", project_id, update_tag)

        bucket_res = env.buckets(project_id)
        buckets, bucket_labels = storage.transform_gcp_buckets_and_labels(bucket_res)
        storage.load_gcp_buckets(session, buckets, project_id, update_tag)
        storage.load_gcp_bucket_labels(session, bucket_labels, project_id, update_tag)
        _load_labels(session, bucket_res.get("items", []), "gcp_bucket", project_id, update_tag)

        raw_sql = env.sql_instances(project_id)
        if raw_sql:
            cloud_sql_instance.load_sql_instances(
                session, cloud_sql_instance.transform_sql_instances(raw_sql, project_id), project_id, update_tag)
            cloud_sql_instance.load_authorized_networks(
                session, cloud_sql_instance.transform_authorized_networks(raw_sql), project_id, update_tag)
            _load_labels(session, raw_sql, "cloud_sql_instance", project_id, update_tag)
        stats["projects"][project_id] = {"instances": len(instances), "buckets": len(buckets)}

    # IAM pass. Bindings reference principals from *other* projects (cross-project
    # grants), and HAS_ALLOW_POLICY / CAN_* edges only attach to principals that
    # already exist. Loading every project's inventory first makes a single run
    # complete and a second run a no-op (a live Cartography sync converges the
    # same way, but only after a second sync).
    for project_id in PROJECTS:
        p_common = {**common, "PROJECT_ID": project_id}
        # IAM policy bindings: the post-fetch half of policy_bindings.sync().
        transformed = policy_bindings.transform_bindings(env.policy_results(project_id))
        direct, inherited = policy_bindings._split_bindings_by_graph_scope(transformed)
        permission_context = permission_relationships.build_principals_from_policy_bindings(
            transformed, role_permissions, project_id)
        to_load = policy_bindings._claim_inherited_bindings_for_graph(inherited, claim_state)
        policy_bindings.load_inherited_bindings(session, to_load, update_tag)
        policy_bindings.load_bindings(session, direct, project_id, update_tag)

        # Principal -> resource permission edges (CAN_READ, CAN_WRITE, CAN_IMPERSONATE, ...)
        permission_relationships.sync(session, project_id, update_tag, p_common, permission_context)
        stats["projects"][project_id]["bindings"] = len(transformed)

    # Post-sync analysis Cartography runs for compute, IAM and storage.
    run_analysis_job("gcp_ip_node_label_migration.json", session, common)
    run_typed_analysis_job(GCP_COMPUTE_INSTANCE_VPC_ANALYSIS, session, common)
    run_typed_analysis_job(GCP_BUCKET_PUBLIC_PROJECTION, session, common)
    return stats


def _load_labels(session: neo4j.Session, resources: list[dict], resource_type: str,
                 project_id: str, update_tag: int) -> None:
    data = labels.transform_labels(labels.get_labels(resources, resource_type), resource_type)
    labels.load_labels(session, data, resource_type, project_id, update_tag)
