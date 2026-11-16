# Schema mapping

Phase 0 gate output. Everything the synthetic seed writes comes from the installed Cartography package
(`cartography==0.144.0`), which is authoritative over the online documentation.

## How the seed stays inside the upstream schema

The seed does not write Cypher. `ingestion/providers/gcp/synthetic/generator.py` emits payloads shaped like the
Google Cloud API responses Cartography consumes, and `ingestion/providers/gcp/synthetic/loader.py` passes them
through Cartography's own functions:

| Data | Upstream function(s) used | Upstream source file |
|---|---|---|
| Organisation | `load_gcp_organizations` | `cartography/intel/gcp/crm/orgs.py` |
| Folders | `transform_gcp_folders`, `load_gcp_folders` | `cartography/intel/gcp/crm/folders.py` |
| Projects | `transform_gcp_projects`, `load_gcp_projects` | `cartography/intel/gcp/crm/projects.py` |
| Predefined roles | `transform_org_roles`, `load_org_roles`, `build_role_permissions_by_name` | `cartography/intel/gcp/iam.py` |
| Service accounts | `transform_gcp_service_accounts`, `load_gcp_service_accounts` | `cartography/intel/gcp/iam.py` |
| VPCs, subnets | `transform_gcp_vpcs`, `load_gcp_vpcs`, `transform_gcp_subnets`, `load_gcp_subnets` | `cartography/intel/gcp/compute.py` |
| Firewalls, IP rules, IP ranges | `transform_gcp_firewall`, `load_gcp_ingress_firewalls` | `cartography/intel/gcp/compute.py` |
| Instances, NICs, access configs, network tags | `transform_gcp_instances`, `load_gcp_instances` | `cartography/intel/gcp/compute.py` |
| Labels | `get_labels`, `transform_labels`, `load_labels` | `cartography/intel/gcp/labels.py` |
| Buckets (+ legacy bucket labels) | `transform_gcp_buckets_and_labels`, `load_gcp_buckets`, `load_gcp_bucket_labels` | `cartography/intel/gcp/storage.py` |
| Cloud SQL instances, authorized networks | `transform_sql_instances`, `load_sql_instances`, `transform_authorized_networks`, `load_authorized_networks` | `cartography/intel/gcp/cloud_sql_instance.py` |
| IAM policy bindings + `APPLIES_TO` | `transform_bindings`, `load_bindings`, `load_inherited_bindings` (the post-fetch half of `policy_bindings.sync`) | `cartography/intel/gcp/policy_bindings.py` |
| `CAN_READ` / `CAN_WRITE` / `CAN_DELETE` / `CAN_IMPERSONATE` | `build_principals_from_policy_bindings`, `permission_relationships.sync` with the packaged mapping file | `cartography/intel/gcp/permission_relationships.py`, `cartography/data/gcp_permission_relationships.yaml` |
| `MEMBER_OF_GCP_VPC`, `_ont_public`, IP label migration | `run_typed_analysis_job(GCP_COMPUTE_INSTANCE_VPC_ANALYSIS / GCP_BUCKET_PUBLIC_PROJECTION)`, `run_analysis_job("gcp_ip_node_label_migration.json")` | `cartography/analysis/gcp/analysis.py`, `cartography/data/jobs/analysis/` |

Two private helpers are called (`policy_bindings._split_bindings_by_graph_scope`,
`policy_bindings._claim_inherited_bindings_for_graph`): the public `policy_bindings.sync` fetches from the Cloud
Asset API itself, so the fetch-free half has to be assembled from its parts. The version pin protects this.

## Identifier formats (from the upstream transforms)

| Label | `id` format | Upstream origin |
|---|---|---|
| `GCPOrganization` | `organizations/<number>` | `org["name"]` in `load_gcp_organizations` |
| `GCPFolder` | `folders/<number>` | `PropertyRef("name")` in `GCPFolderSchema` |
| `GCPProject` | project id, e.g. `alt-prod-web` | `PropertyRef("projectId")` |
| `GCPInstance` | `projects/<p>/zones/<z>/instances/<name>` | `partial_uri` built in `transform_gcp_instances` |
| `GCPNetworkInterface` | `<instance id>/networkinterfaces/<nic>` | `_transform_nics` |
| `GCPNicAccessConfig` | `<nic id>/accessconfigs/<type>` | `_transform_access_configs` |
| `GCPVpc` | `projects/<p>/global/networks/<name>` | `transform_gcp_vpcs` |
| `GCPSubnet` | `projects/<p>/regions/<r>/subnetworks/<name>` | `transform_gcp_subnets` |
| `GCPFirewall` | `projects/<p>/global/firewalls/<name>` | `transform_gcp_firewall` |
| `GCPIpRule` | `<firewall id>/allow/<ports><protocol>` | `_transform_fw_entry` |
| `GCPIpRange` | the CIDR itself, e.g. `0.0.0.0/0` (shared across projects) | `IpRangeSchema` |
| `GCPServiceAccount` | numeric `uniqueId` | `transform_gcp_service_accounts` |
| `GCPRole` | role name, e.g. `roles/editor` | `PropertyRef("name")` |
| `GCPPolicyBinding` | `<full resource name>_<role>` | `transform_bindings` |
| `GCPBucket` | bucket name | `transform_gcp_buckets_and_labels` |
| `GCPCloudSQLInstance` | instance `selfLink` URL | `PropertyRef("selfLink")` |
| `GCPCloudSQLAuthorizedNetwork` | `<instance selfLink>/authorizedNetworks/<cidr>` | `transform_authorized_networks` |
| `GCPLabel` | `<resource id>:<key>:<value>` | `labels.get_labels` |
| `GCPBucketLabel` (legacy) | `GCPBucket_<key>` (one node per key, value overwritten) | `transform_gcp_buckets_and_labels` |

## Edge direction notes

- Sub-resource edges point from the owner: `(:GCPProject)-[:RESOURCE]->(:GCPInstance)`.
- `(:GCPInstance)-[:NETWORK_INTERFACE]->(:GCPNetworkInterface)-[:PART_OF_SUBNET]->(:GCPSubnet)<-[:HAS]-(:GCPVpc)`.
- `(:GCPNetworkInterface)-[:RESOURCE]->(:GCPNicAccessConfig)` carries the public IP (`public_ip`).
- `(:GCPVpc)-[:RESOURCE]->(:GCPFirewall)<-[:ALLOWED_BY]-(:GCPIpRule)<-[:MEMBER_OF_IP_RULE]-(:GCPIpRange)`.
- `(:GCPInstance)-[:RUNS_AS]->(:GCPServiceAccount)` (matched by email).
- `(:GCPPrincipal)-[:HAS_ALLOW_POLICY]->(:GCPPolicyBinding)-[:GRANTS_ROLE]->(:GCPRole)` and
  `(:GCPPolicyBinding)-[:APPLIES_TO]->(scope)`.
- `(:GCPPrincipal)-[:CAN_READ|CAN_WRITE|CAN_DELETE]->(:GCPBucket)`, `(:GCPPrincipal)-[:CAN_IMPERSONATE]->(:GCPServiceAccount)`.
- `(:GCPProject)-[:PARENT]->(:GCPFolder)-[:PARENT]->(:GCPOrganization)`.
- Labels: `(:GCPInstance)-[:LABELED]->(:GCPLabel)` (also `TAGGED` for the ontology).

## Not available upstream

| Wanted | Why it is not in the graph | Security consequence | Workaround used |
|---|---|---|---|
| Project environment (prod / test / dev) | Cartography has no label/environment property on `GCPProject` and does not ingest project labels | Environment trust must be inferred | The Resource Manager folder (`GCPFolder.displayname`) the project sits in: Production, Testing, Development |
| Data sensitivity of a resource | No first-class property | Sensitivity cannot be proven, only read from user labels | `GCPLabel` with key `data-classification` on instances, buckets and Cloud SQL instances |
| Human principals (users, groups) | Only created by the Google Workspace module, which is not seeded | Bindings to `user:`/`group:` members keep their `members` list but have no `HAS_ALLOW_POLICY` edge; human access paths are invisible | Documented; analysis covers service accounts |
| Service-account keys | Seed does not generate `GCPServiceAccountKey` | Cannot tell whether a key exists outside Google Cloud | Listed as missing evidence in scenario B |
| Runtime state (listening ports, processes, OS patch level) | Cartography inventories configuration, not runtime | Exposure is "reachable by configuration", never "exploitable" | Classified `potential_exposure`; listed as missing evidence |
| Firewall target-tag scoping in rule evaluation | Tags are modelled (`TARGET_TAG`, `TAGGED`) but the upstream exposure rules evaluate the whole VPC | Possible over-reporting of exposed instances | Kept as-is (rules are not re-implemented); noted in each finding's missing evidence |
| Org policies, VPC Service Controls, IAM deny policies | Not ingested by this Cartography version | Access that config allows may be blocked at request time | Listed as missing evidence |
| Actual permission usage (IAM Recommender, audit logs) | Not ingested | Cannot tell which granted permissions are used | Remediation proposes staged migration and validation steps |
| Business criticality, ownership | Not an inventory concept | Agent must answer "Insufficient evidence to determine this." | Tested |
| Full predefined-role permission lists | The seed carries curated subsets | Real roles grant more; scores are a lower bound | Documented in `limitations.md` |

<!-- GENERATED:START -->

Generated from the live graph and the installed `cartography==0.144.0` package by `scripts/generate_schema_mapping.py`.

### Node labels

| Label set (primary first) | Nodes | Declared by (upstream schema class) | Example `id` |
|---|---|---|---|
| `GCPBucket`, `ObjectStorage` | 10 | `cartography.models.gcp.storage.bucket.GCPBucketSchema` | `alt-dev-analytics-notebooks` |
| `GCPBucketLabel` | 2 | `cartography.models.gcp.storage.bucket.GCPBucketLabelSchema` | `GCPBucket_data-classification` |
| `GCPCloudSQLAuthorizedNetwork` | 2 | `cartography.models.gcp.cloudsql.authorized_network.GCPCloudSQLAuthorizedNetworkSchema` | `https://sqladmin.googleapis.com/sql/v1beta4/projects/alt-dev-sandbox/instances/dev-db/authorizedNetworks/198.51.100.0/24` |
| `GCPCloudSQLInstance`, `Database` | 4 | `cartography.models.gcp.cloudsql.instance.GCPSqlInstanceSchema` | `https://sqladmin.googleapis.com/sql/v1beta4/projects/alt-dev-sandbox/instances/dev-db` |
| `GCPFirewall`, `NetworkAccessControl` | 13 | `cartography.models.gcp.compute.firewall.GCPFirewallSchema` | `projects/alt-dev-analytics/global/firewalls/dev-analytics-allow-internal` |
| `GCPFolder` | 3 | `cartography.models.gcp.crm.folders.GCPFolderSchema` | `folders/100000000101` |
| `GCPInstance`, `ComputeInstance`, `Instance` | 32 | `cartography.models.gcp.compute.instance.GCPInstanceSchema` | `projects/alt-dev-analytics/zones/us-east1-c/instances/analytics-dev-01` |
| `GCPIpRange`, `IpRange` | 9 | `cartography.models.gcp.compute.ip_range.IpRangeSchema` | `0.0.0.0/0` |
| `GCPIpRule`, `IpPermissionInbound`, `IpRule` | 16 | `cartography.models.gcp.compute.ip_rule.GCPIpRuleAllowedSchema`; `cartography.models.gcp.compute.ip_rule.GCPIpRuleDeniedSchema` | `projects/alt-dev-analytics/global/firewalls/dev-analytics-allow-internal/allow/0to65535tcp` |
| `GCPLabel`, `Label`, `Tag` | 96 | `cartography.models.gcp.labels.label.GCPBigtableInstanceGCPLabelSchema`; `cartography.models.gcp.labels.label.GCPBucketGCPLabelSchema`; `cartography.models.gcp.labels.label.GCPCloudFunctionGCPLabelSchema`; `cartography.models.gcp.labels.label.GCPCloudRunJobGCPLabelSchema`; `cartography.models.gcp.labels.label.GCPCloudRunServiceGCPLabelSchema`; `cartography.models.gcp.labels.label.GCPCloudSQLInstanceGCPLabelSchema`; `cartography.models.gcp.labels.label.GCPDNSZoneGCPLabelSchema`; `cartography.models.gcp.labels.label.GCPInstanceGCPLabelSchema`; `cartography.models.gcp.labels.label.GCPSecretManagerSecretGCPLabelSchema`; `cartography.models.gcp.labels.label.GKEClusterGCPLabelSchema` | `projects/alt-dev-analytics/zones/us-east1-c/instances/analytics-dev-01:app:analytics` |
| `GCPLabel`, `GCPBucketLabel`, `Label`, `Tag` | 20 | `cartography.models.gcp.labels.label.GCPBigtableInstanceGCPLabelSchema`; `cartography.models.gcp.labels.label.GCPBucketGCPLabelSchema`; `cartography.models.gcp.labels.label.GCPCloudFunctionGCPLabelSchema`; `cartography.models.gcp.labels.label.GCPCloudRunJobGCPLabelSchema`; `cartography.models.gcp.labels.label.GCPCloudRunServiceGCPLabelSchema`; `cartography.models.gcp.labels.label.GCPCloudSQLInstanceGCPLabelSchema`; `cartography.models.gcp.labels.label.GCPDNSZoneGCPLabelSchema`; `cartography.models.gcp.labels.label.GCPInstanceGCPLabelSchema`; `cartography.models.gcp.labels.label.GCPSecretManagerSecretGCPLabelSchema`; `cartography.models.gcp.labels.label.GKEClusterGCPLabelSchema` | `alt-dev-analytics-notebooks:data-classification:none` |
| `GCPNetworkInterface`, `NetworkInterface` | 32 | `cartography.models.gcp.compute.network_interface.GCPNetworkInterfaceSchema` | `projects/alt-dev-analytics/zones/us-east1-c/instances/analytics-dev-01/networkinterfaces/nic0` |
| `GCPNetworkTag` | 3 | `cartography.models.gcp.compute.firewall_target_tag.GCPFirewallTargetTagSchema`; `cartography.models.gcp.compute.network_tag.GCPNetworkTagSchema` | `projects/alt-prod-payments/global/networks/prod-payments-vpc/tags/db-proxy` |
| `GCPNicAccessConfig` | 6 | `cartography.models.gcp.compute.nic_access_config.GCPNicAccessConfigSchema` | `projects/alt-dev-sandbox/zones/us-east1-b/instances/ci-runner-01/networkinterfaces/nic0/accessconfigs/ONE_TO_ONE_NAT` |
| `GCPOrganization`, `Tenant` | 1 | `cartography.models.gcp.crm.organizations.GCPOrganizationSchema` | `organizations/482915730216` |
| `GCPPolicyBinding` | 28 | `cartography.models.gcp.policy_bindings.GCPFolderPolicyBindingSchema`; `cartography.models.gcp.policy_bindings.GCPOrganizationPolicyBindingSchema`; `cartography.models.gcp.policy_bindings.GCPPolicyBindingSchema` | `//cloudresourcemanager.googleapis.com/folders/100000000101_roles/viewer` |
| `GCPProject`, `Tenant` | 7 | `cartography.models.gcp.compute.project.GCPProjectComputeMetadataSchema`; `cartography.models.gcp.crm.projects.GCPProjectSchema` | `alt-dev-analytics` |
| `GCPRole`, `PermissionRole` | 16 | `cartography.models.gcp.iam.GCPOrgRoleSchema`; `cartography.models.gcp.iam.GCPProjectRoleSchema` | `roles/cloudsql.admin` |
| `GCPServiceAccount`, `GCPPrincipal`, `ServiceAccount` | 18 | `cartography.models.gcp.iam.GCPServiceAccountSchema` | `131000000000100` |
| `GCPSubnet`, `Subnet` | 9 | `cartography.models.gcp.compute.subnet.GCPSubnetSchema`; `cartography.models.gcp.compute.subnet_stub.GCPSubnetStubSchema` | `projects/alt-dev-analytics/regions/us-east1/subnetworks/notebooks` |
| `GCPVpc`, `VirtualNetwork` | 7 | `cartography.models.gcp.compute.vpc.GCPVpcSchema`; `cartography.models.gcp.compute.vpc_stub.GCPVpcStubSchema` | `projects/alt-dev-analytics/global/networks/dev-analytics-vpc` |

Every label in the graph, with its node count (a node can carry several labels):

`ComputeInstance` (32), `Database` (4), `GCPBucket` (10), `GCPBucketLabel` (22), `GCPCloudSQLAuthorizedNetwork` (2), `GCPCloudSQLInstance` (4), `GCPFirewall` (13), `GCPFolder` (3), `GCPInstance` (32), `GCPIpRange` (9), `GCPIpRule` (16), `GCPLabel` (116), `GCPNetworkInterface` (32), `GCPNetworkTag` (3), `GCPNicAccessConfig` (6), `GCPOrganization` (1), `GCPPolicyBinding` (28), `GCPPrincipal` (18), `GCPProject` (7), `GCPRole` (16), `GCPServiceAccount` (18), `GCPSubnet` (9), `GCPVpc` (7), `Instance` (32), `IpPermissionInbound` (16), `IpRange` (9), `IpRule` (16), `Label` (116), `NetworkAccessControl` (13), `NetworkInterface` (32), `ObjectStorage` (10), `PermissionRole` (16), `ServiceAccount` (18), `Subnet` (9), `Tag` (116), `Tenant` (8), `VirtualNetwork` (7)

### Properties written, by primary label

`property <- upstream field` is the `PropertyRef` in the Cartography node schema: the key of the transformed API payload the loader reads. `firstseen`, `lastupdated`, `_module_name`, `_module_version` and `_ont_*` are written by Cartography's loader and ontology layer.

- **`GCPBucket`**: `_module_name`, `_module_version`, `_ont_encrypted`, `_ont_location`, `_ont_name`, `_ont_public`, `_ont_source`, `_ont_versioning`, `acl_public`, `bucket_id`, `firstseen`, `iam_config_bucket_policy_only`, `iam_config_public_access_prevention`, `id`, `kind`, `lastupdated`, `location`, `location_type`, `meta_generation`, `project_number`, `self_link`, `storage_class`, `time_created`, `versioning_enabled`
- **`GCPBucketLabel`**: `_module_name`, `_module_version`, `firstseen`, `id`, `key`, `lastupdated`, `value`
- **`GCPCloudSQLAuthorizedNetwork`**: `_module_name`, `_module_version`, `firstseen`, `id`, `instance_id`, `lastupdated`, `name`, `value`
- **`GCPCloudSQLInstance`**: `_module_name`, `_module_version`, `_ont_location`, `_ont_name`, `_ont_source`, `_ont_type`, `_ont_version`, `authorized_networks`, `availability_type`, `backend_type` <- `backendType`, `backup_configuration`, `backup_enabled`, `connection_name` <- `connectionName`, `database_engine`, `database_version` <- `databaseVersion`, `disk_size_gb`, `disk_type`, `firstseen`, `gce_zone` <- `gceZone`, `id` <- `selfLink`, `ip_addresses`, `lastupdated`, `name`, `network_id`, `region`, `require_ssl`, `service_account_email`, `ssl_mode`, `state`, `tier`
- **`GCPFirewall`**: `_module_name`, `_module_version`, `_ont_direction`, `_ont_name`, `_ont_source`, `direction`, `disabled`, `firstseen`, `has_target_service_accounts`, `id`, `lastupdated`, `name`, `priority`, `self_link` <- `selfLink`
- **`GCPFolder`**: `_module_name`, `_module_version`, `displayname` <- `displayName`, `firstseen`, `foldername` <- `name`, `id` <- `name`, `lastupdated`, `lifecyclestate` <- `lifecycleState`, `parent_org`
- **`GCPInstance`**: `_module_name`, `_module_version`, `_ont_created_at`, `_ont_name`, `_ont_private_ip_address`, `_ont_public_ip_address`, `_ont_region`, `_ont_source`, `_ont_state`, `_ont_type`, `block_project_ssh_keys`, `can_ip_forward`, `creation_timestamp`, `enable_confidential_compute`, `enable_integrity_monitoring`, `enable_oslogin_metadata`, `enable_vtpm`, `firstseen`, `hostname`, `id` <- `partial_uri`, `instancename` <- `name`, `lastupdated`, `machine_type`, `private_ip`, `project_id`, `public_ip`, `self_link` <- `selfLink`, `service_account_email`, `service_account_scopes`, `status`, `zone_name`
- **`GCPIpRange`**: `_module_name`, `_module_version`, `firstseen`, `id` <- `range`, `lastupdated`, `range`
- **`GCPIpRule`**: `_module_name`, `_module_version`, `firstseen`, `fromport`, `id` <- `ruleid`, `lastupdated`, `protocol`, `toport`
- **`GCPLabel`**: `_module_name`, `_module_version`, `_ont_source`, `firstseen`, `id`, `key`, `lastupdated`, `resource_type`, `value`
- **`GCPNetworkInterface`**: `_module_name`, `_module_version`, `firstseen`, `id` <- `nic_id`, `lastupdated`, `name`, `private_ip` <- `networkIP`
- **`GCPNetworkTag`**: `_module_name`, `_module_version`, `firstseen`, `id` <- `tag_id`, `lastupdated`, `tag_id`, `value`
- **`GCPNicAccessConfig`**: `_module_name`, `_module_version`, `firstseen`, `id` <- `access_config_id`, `lastupdated`, `name`, `network_tier` <- `networkTier`, `public_ip` <- `natIP`, `type`
- **`GCPOrganization`**: `_module_name`, `_module_version`, `_ont_name`, `_ont_source`, `_ont_status`, `displayname` <- `displayName`, `firstseen`, `id`, `lastupdated`, `lifecyclestate` <- `lifecycleState`, `orgname` <- `name`
- **`GCPPolicyBinding`**: `_module_name`, `_module_version`, `domains`, `firstseen`, `has_condition`, `id`, `is_public`, `lastupdated`, `members`, `raw_members`, `resource`, `resource_type`, `role`, `wif_pools`
- **`GCPProject`**: `_module_name`, `_module_version`, `_ont_name`, `_ont_source`, `_ont_status`, `displayname` <- `name`, `firstseen`, `id`, `lastupdated`, `lifecyclestate` <- `lifecycleState`, `parent_folder`, `projectnumber` <- `projectNumber`
- **`GCPRole`**: `_module_name`, `_module_version`, `_ont_name`, `_ont_scope`, `_ont_source`, `_ont_type`, `deleted`, `description`, `etag`, `firstseen`, `id` <- `name`, `lastupdated`, `name`, `organization_id` <- `organizationId`, `permissions` <- `includedPermissions`, `role_type` <- `roleType`, `scope`, `title`
- **`GCPServiceAccount`**: `_module_name`, `_module_version`, `_ont_active`, `_ont_email`, `_ont_name`, `_ont_source`, `disabled`, `display_name` <- `displayName`, `email`, `firstseen`, `id`, `lastupdated`, `oauth2_client_id` <- `oauth2ClientId`, `project_id` <- `projectId`, `unique_id` <- `uniqueId`
- **`GCPSubnet`**: `_module_name`, `_module_version`, `_ont_cidr_block`, `_ont_name`, `_ont_region`, `_ont_source`, `firstseen`, `flow_logs_aggregation_interval`, `flow_logs_enabled`, `flow_logs_metadata`, `flow_logs_sampling`, `gateway_address`, `id` <- `partial_uri`, `ip_cidr_range`, `lastupdated`, `name`, `partial_uri`, `private_ip_google_access`, `project_id`, `region`, `self_link`, `vpc_partial_uri`
- **`GCPVpc`**: `_module_name`, `_module_version`, `_ont_name`, `_ont_source`, `auto_create_subnetworks`, `description`, `firstseen`, `id` <- `partial_uri`, `lastupdated` <- `LASTUPDATED`, `name`, `partial_uri`, `project_id` <- `PROJECT_ID`, `routing_config_routing_mode`, `self_link`

### Relationships (graph direction)

| Pattern | Count | Declared by |
|---|---|---|
| `(:GCPBucket)-[:LABELED]->(:GCPBucketLabel)` | 20 | cartography.models.gcp.labels.label.GCPBucketGCPLabelSchema; cartography.models.gcp.storage.bucket.GCPBucketLabelSchema |
| `(:GCPBucket)-[:LABELED]->(:GCPLabel)` | 20 | cartography.models.gcp.labels.label.GCPBucketGCPLabelSchema; cartography.models.gcp.storage.bucket.GCPBucketLabelSchema |
| `(:GCPBucket)-[:TAGGED]->(:GCPLabel)` | 20 | cartography.models.gcp.labels.label.GCPBucketGCPLabelSchema |
| `(:GCPCloudSQLInstance)-[:ASSOCIATED_WITH]->(:GCPVpc)` | 2 | cartography.models.gcp.cloudsql.instance.GCPSqlInstanceSchema |
| `(:GCPCloudSQLInstance)-[:AUTHORIZED_NETWORK]->(:GCPCloudSQLAuthorizedNetwork)` | 2 | cartography.models.gcp.cloudsql.authorized_network.GCPCloudSQLAuthorizedNetworkSchema |
| `(:GCPFirewall)-[:TARGET_TAG]->(:GCPNetworkTag)` | 3 | cartography.models.gcp.compute.firewall_target_tag.GCPFirewallTargetTagSchema |
| `(:GCPFolder)-[:PARENT]->(:GCPOrganization)` | 3 | cartography.models.gcp.crm.folders.GCPFolderSchema |
| `(:GCPFolder)-[:RESOURCE]->(:GCPPolicyBinding)` | 2 | cartography.models.gcp.policy_bindings.GCPFolderPolicyBindingSchema |
| `(:GCPInstance)-[:LABELED]->(:GCPLabel)` | 96 | cartography.models.gcp.labels.label.GCPInstanceGCPLabelSchema |
| `(:GCPInstance)-[:MEMBER_OF_GCP_VPC]->(:GCPVpc)` | 32 | cartography.analysis.gcp.analysis (analysis job) |
| `(:GCPInstance)-[:NETWORK_INTERFACE]->(:GCPNetworkInterface)` | 32 | cartography.models.gcp.compute.network_interface.GCPNetworkInterfaceSchema |
| `(:GCPInstance)-[:RUNS_AS]->(:GCPServiceAccount)` | 32 | cartography.models.gcp.compute.instance.GCPInstanceSchema |
| `(:GCPInstance)-[:TAGGED]->(:GCPLabel)` | 96 | cartography.models.gcp.labels.label.GCPInstanceGCPLabelSchema |
| `(:GCPInstance)-[:TAGGED]->(:GCPNetworkTag)` | 5 | cartography.models.gcp.compute.network_tag.GCPNetworkTagSchema |
| `(:GCPIpRange)-[:MEMBER_OF_IP_RULE]->(:GCPIpRule)` | 16 | cartography.models.gcp.compute.ip_range.IpRangeSchema |
| `(:GCPIpRule)-[:ALLOWED_BY]->(:GCPFirewall)` | 16 | cartography.models.gcp.compute.ip_rule.GCPIpRuleAllowedSchema |
| `(:GCPNetworkInterface)-[:PART_OF_SUBNET]->(:GCPSubnet)` | 32 | cartography.models.gcp.compute.network_interface.GCPNetworkInterfaceSchema |
| `(:GCPNetworkInterface)-[:RESOURCE]->(:GCPNicAccessConfig)` | 6 | cartography.models.gcp.compute.nic_access_config.GCPNicAccessConfigSchema |
| `(:GCPNetworkTag)-[:DEFINED_IN]->(:GCPVpc)` | 3 | cartography.models.gcp.compute.firewall_target_tag.GCPFirewallTargetTagSchema; cartography.models.gcp.compute.network_tag.GCPNetworkTagSchema |
| `(:GCPOrganization)-[:RESOURCE]->(:GCPFolder)` | 3 | cartography.models.gcp.crm.folders.GCPFolderSchema |
| `(:GCPOrganization)-[:RESOURCE]->(:GCPPolicyBinding)` | 2 | cartography.models.gcp.policy_bindings.GCPOrganizationPolicyBindingSchema |
| `(:GCPOrganization)-[:RESOURCE]->(:GCPProject)` | 7 | cartography.models.gcp.crm.projects.GCPProjectSchema |
| `(:GCPOrganization)-[:RESOURCE]->(:GCPRole)` | 16 | cartography.models.gcp.iam.GCPOrgRoleSchema |
| `(:GCPPolicyBinding)-[:APPLIES_TO]->(:GCPBucket)` | 3 | cartography.models.gcp.policy_bindings.GCPPolicyBindingAppliesToMatchLink |
| `(:GCPPolicyBinding)-[:APPLIES_TO]->(:GCPFolder)` | 2 | cartography.models.gcp.policy_bindings.GCPPolicyBindingAppliesToMatchLink |
| `(:GCPPolicyBinding)-[:APPLIES_TO]->(:GCPOrganization)` | 2 | cartography.models.gcp.policy_bindings.GCPPolicyBindingAppliesToMatchLink |
| `(:GCPPolicyBinding)-[:APPLIES_TO]->(:GCPProject)` | 21 | cartography.models.gcp.policy_bindings.GCPPolicyBindingAppliesToMatchLink |
| `(:GCPPolicyBinding)-[:GRANTS_ROLE]->(:GCPRole)` | 28 | cartography.models.gcp.policy_bindings.GCPFolderPolicyBindingSchema; cartography.models.gcp.policy_bindings.GCPOrganizationPolicyBindingSchema; cartography.models.gcp.policy_bindings.GCPPolicyBindingSchema |
| `(:GCPProject)-[:PARENT]->(:GCPFolder)` | 7 | cartography.models.gcp.crm.projects.GCPProjectSchema |
| `(:GCPProject)-[:RESOURCE]->(:GCPBucket)` | 10 | cartography.models.gcp.storage.bucket.GCPBucketSchema |
| `(:GCPProject)-[:RESOURCE]->(:GCPBucketLabel)` | 12 | cartography.models.gcp.labels.label.GCPBigtableInstanceGCPLabelSchema; cartography.models.gcp.labels.label.GCPBucketGCPLabelSchema; cartography.models.gcp.labels.label.GCPCloudFunctionGCPLabelSchema; cartography.models.gcp.labels.label.GCPCloudRunJobGCPLabelSchema; cartography.models.gcp.labels.label.GCPCloudRunServiceGCPLabelSchema; cartography.models.gcp.labels.label.GCPCloudSQLInstanceGCPLabelSchema; cartography.models.gcp.labels.label.GCPDNSZoneGCPLabelSchema; cartography.models.gcp.labels.label.GCPInstanceGCPLabelSchema; cartography.models.gcp.labels.label.GCPSecretManagerSecretGCPLabelSchema; cartography.models.gcp.labels.label.GKEClusterGCPLabelSchema; cartography.models.gcp.storage.bucket.GCPBucketLabelSchema |
| `(:GCPProject)-[:RESOURCE]->(:GCPCloudSQLAuthorizedNetwork)` | 2 | cartography.models.gcp.cloudsql.authorized_network.GCPCloudSQLAuthorizedNetworkSchema |
| `(:GCPProject)-[:RESOURCE]->(:GCPCloudSQLInstance)` | 4 | cartography.models.gcp.cloudsql.instance.GCPSqlInstanceSchema |
| `(:GCPProject)-[:RESOURCE]->(:GCPFirewall)` | 13 | cartography.models.gcp.compute.firewall.GCPFirewallSchema |
| `(:GCPProject)-[:RESOURCE]->(:GCPInstance)` | 32 | cartography.models.gcp.compute.instance.GCPInstanceSchema |
| `(:GCPProject)-[:RESOURCE]->(:GCPIpRange)` | 11 | cartography.models.gcp.compute.ip_range.IpRangeSchema |
| `(:GCPProject)-[:RESOURCE]->(:GCPIpRule)` | 16 | cartography.models.gcp.compute.ip_rule.GCPIpRuleAllowedSchema; cartography.models.gcp.compute.ip_rule.GCPIpRuleDeniedSchema |
| `(:GCPProject)-[:RESOURCE]->(:GCPLabel)` | 116 | cartography.models.gcp.labels.label.GCPBigtableInstanceGCPLabelSchema; cartography.models.gcp.labels.label.GCPBucketGCPLabelSchema; cartography.models.gcp.labels.label.GCPCloudFunctionGCPLabelSchema; cartography.models.gcp.labels.label.GCPCloudRunJobGCPLabelSchema; cartography.models.gcp.labels.label.GCPCloudRunServiceGCPLabelSchema; cartography.models.gcp.labels.label.GCPCloudSQLInstanceGCPLabelSchema; cartography.models.gcp.labels.label.GCPDNSZoneGCPLabelSchema; cartography.models.gcp.labels.label.GCPInstanceGCPLabelSchema; cartography.models.gcp.labels.label.GCPSecretManagerSecretGCPLabelSchema; cartography.models.gcp.labels.label.GKEClusterGCPLabelSchema; cartography.models.gcp.storage.bucket.GCPBucketLabelSchema |
| `(:GCPProject)-[:RESOURCE]->(:GCPNetworkInterface)` | 32 | cartography.models.gcp.compute.network_interface.GCPNetworkInterfaceSchema |
| `(:GCPProject)-[:RESOURCE]->(:GCPNetworkTag)` | 3 | cartography.models.gcp.compute.firewall_target_tag.GCPFirewallTargetTagSchema; cartography.models.gcp.compute.network_tag.GCPNetworkTagSchema |
| `(:GCPProject)-[:RESOURCE]->(:GCPNicAccessConfig)` | 6 | cartography.models.gcp.compute.nic_access_config.GCPNicAccessConfigSchema |
| `(:GCPProject)-[:RESOURCE]->(:GCPPolicyBinding)` | 24 | cartography.models.gcp.policy_bindings.GCPPolicyBindingSchema |
| `(:GCPProject)-[:RESOURCE]->(:GCPServiceAccount)` | 18 | cartography.models.gcp.iam.GCPServiceAccountSchema |
| `(:GCPProject)-[:RESOURCE]->(:GCPSubnet)` | 9 | cartography.models.gcp.compute.subnet.GCPSubnetSchema; cartography.models.gcp.compute.subnet_stub.GCPSubnetStubSchema |
| `(:GCPProject)-[:RESOURCE]->(:GCPVpc)` | 7 | cartography.models.gcp.compute.vpc.GCPVpcSchema; cartography.models.gcp.compute.vpc_stub.GCPVpcStubSchema |
| `(:GCPServiceAccount)-[:CAN_DELETE]->(:GCPBucket)` | 24 | cartography/data/gcp_permission_relationships.yaml (GCPPermissionMatchLink) |
| `(:GCPServiceAccount)-[:CAN_IMPERSONATE]->(:GCPServiceAccount)` | 33 | cartography/data/gcp_permission_relationships.yaml (GCPPermissionMatchLink) |
| `(:GCPServiceAccount)-[:CAN_READ]->(:GCPBucket)` | 28 | cartography/data/gcp_permission_relationships.yaml (GCPPermissionMatchLink) |
| `(:GCPServiceAccount)-[:CAN_WRITE]->(:GCPBucket)` | 24 | cartography/data/gcp_permission_relationships.yaml (GCPPermissionMatchLink) |
| `(:GCPServiceAccount)-[:HAS_ALLOW_POLICY]->(:GCPPolicyBinding)` | 29 | cartography.models.gcp.policy_bindings.GCPFolderPolicyBindingSchema; cartography.models.gcp.policy_bindings.GCPOrganizationPolicyBindingSchema; cartography.models.gcp.policy_bindings.GCPPolicyBindingSchema |
| `(:GCPVpc)-[:HAS]->(:GCPSubnet)` | 9 | cartography.models.gcp.compute.subnet.GCPSubnetSchema |
| `(:GCPVpc)-[:RESOURCE]->(:GCPFirewall)` | 13 | cartography.models.gcp.compute.firewall.GCPFirewallSchema |

### Cartography rules inventory (GCP facts)

| Rule | Fact | Maturity | Requires (labels) | Graph has data? |
|---|---|---|---|---|
| `compute_instance_exposed` | `gcp_instance_internet_exposed` | EXPERIMENTAL | `GCPFirewall`, `GCPInstance`, `GCPIpRange`, `GCPIpRule`, `GCPNetworkInterface`, `GCPNicAccessConfig`, `GCPProject`, `GCPSubnet`, `GCPVpc` | yes |
| `database_instance_exposed` | `gcp_cloud_sql_public_access` | EXPERIMENTAL | `GCPCloudSQLAuthorizedNetwork`, `GCPCloudSQLInstance` | yes |
| `delegation_boundary_modifiable` | `gcp_trust_relationship_manipulation` | EXPERIMENTAL | `GCPPolicyBinding`, `GCPPrincipal`, `GCPRole` | yes |
| `eol_software` | `gke_cluster_kubernetes_version_eol` | EXPERIMENTAL | `GKECluster` | no: missing `GKECluster` |
| `gcp_bigquery_datasets_publicly_accessible` | `gcp_bigquery_dataset_public` | STABLE | `GCPBigQueryDataset`, `GCPProject` | no: missing `GCPBigQueryDataset` |
| `gcp_bigquery_datasets_without_default_cmek` | `gcp_bigquery_dataset_cmek_missing` | STABLE | `GCPBigQueryDataset`, `GCPProject` | no: missing `GCPBigQueryDataset` |
| `gcp_bigquery_tables_without_cmek` | `gcp_bigquery_table_cmek_missing` | STABLE | `GCPBigQueryDataset`, `GCPBigQueryTable`, `GCPProject` | no: missing `GCPBigQueryDataset`, `GCPBigQueryTable` |
| `gcp_bucket_uniform_access_disabled` | `gcp_bucket_uniform_access_disabled` | STABLE | `GCPBucket`, `GCPProject` | yes |
| `gcp_cloud_dns_dnssec_disabled` | `gcp_dnssec_disabled` | STABLE | `GCPDNSZone`, `GCPProject` | no: missing `GCPDNSZone` |
| `gcp_cloud_dns_dnssec_key_signing_uses_rsasha1` | `gcp_dnssec_weak_ksk` | STABLE | `GCPDNSZone`, `GCPProject` | no: missing `GCPDNSZone` |
| `gcp_cloud_dns_dnssec_zone_signing_uses_rsasha1` | `gcp_dnssec_weak_zsk` | STABLE | `GCPDNSZone`, `GCPProject` | no: missing `GCPDNSZone` |
| `gcp_cloudsql_authorized_networks_open_to_internet` | `gcp_cloudsql_authorized_networks_open` | STABLE | `GCPCloudSQLInstance`, `GCPProject` | yes |
| `gcp_cloudsql_automated_backups_disabled` | `gcp_cloudsql_backups_disabled` | STABLE | `GCPCloudSQLInstance`, `GCPProject` | yes |
| `gcp_cloudsql_mysql_local_infile_not_off` | `gcp_cloudsql_mysql_local_infile` | STABLE | `GCPCloudSQLInstance`, `GCPProject` | yes |
| `gcp_cloudsql_mysql_skip_show_database_not_on` | `gcp_cloudsql_mysql_skip_show_database` | STABLE | `GCPCloudSQLInstance`, `GCPProject` | yes |
| `gcp_cloudsql_postgres_log_connections_not_on` | `gcp_cloudsql_postgres_log_connections` | STABLE | `GCPCloudSQLInstance`, `GCPProject` | yes |
| `gcp_cloudsql_postgres_log_disconnections_not_on` | `gcp_cloudsql_postgres_log_disconnections` | STABLE | `GCPCloudSQLInstance`, `GCPProject` | yes |
| `gcp_cloudsql_postgres_log_error_verbosity_too_permissive` | `gcp_cloudsql_postgres_log_error_verbosity` | STABLE | `GCPCloudSQLInstance`, `GCPProject` | yes |
| `gcp_cloudsql_postgres_log_min_duration_statement_not_disabled` | `gcp_cloudsql_postgres_log_min_duration_statement` | STABLE | `GCPCloudSQLInstance`, `GCPProject` | yes |
| `gcp_cloudsql_postgres_log_min_error_statement_below_error` | `gcp_cloudsql_postgres_log_min_error_statement` | STABLE | `GCPCloudSQLInstance`, `GCPProject` | yes |
| `gcp_cloudsql_postgres_log_min_messages_below_warning` | `gcp_cloudsql_postgres_log_min_messages` | STABLE | `GCPCloudSQLInstance`, `GCPProject` | yes |
| `gcp_cloudsql_postgres_pgaudit_not_enabled` | `gcp_cloudsql_postgres_enable_pgaudit` | STABLE | `GCPCloudSQLInstance`, `GCPProject` | yes |
| `gcp_cloudsql_public_ips` | `gcp_cloudsql_public_ip` | STABLE | `GCPCloudSQLInstance`, `GCPProject` | yes |
| `gcp_cloudsql_sqlserver_contained_database_authentication_enabled` | `gcp_cloudsql_sqlserver_contained_auth` | STABLE | `GCPCloudSQLInstance`, `GCPProject` | yes |
| `gcp_cloudsql_sqlserver_cross_db_ownership_chaining_enabled` | `gcp_cloudsql_sqlserver_cross_db_ownership` | STABLE | `GCPCloudSQLInstance`, `GCPProject` | yes |
| `gcp_cloudsql_sqlserver_external_scripts_enabled` | `gcp_cloudsql_sqlserver_external_scripts` | STABLE | `GCPCloudSQLInstance`, `GCPProject` | yes |
| `gcp_cloudsql_sqlserver_remote_access_not_off` | `gcp_cloudsql_sqlserver_remote_access` | STABLE | `GCPCloudSQLInstance`, `GCPProject` | yes |
| `gcp_cloudsql_sqlserver_trace_flag_3625_not_on` | `gcp_cloudsql_sqlserver_trace_3625` | STABLE | `GCPCloudSQLInstance`, `GCPProject` | yes |
| `gcp_cloudsql_sqlserver_user_connections_limiting` | `gcp_cloudsql_sqlserver_user_connections` | STABLE | `GCPCloudSQLInstance`, `GCPProject` | yes |
| `gcp_cloudsql_sqlserver_user_options_configured` | `gcp_cloudsql_sqlserver_user_options` | STABLE | `GCPCloudSQLInstance`, `GCPProject` | yes |
| `gcp_cloudsql_ssl_not_enforced` | `gcp_cloudsql_ssl_not_enforced` | STABLE | `GCPCloudSQLInstance`, `GCPProject` | yes |
| `gcp_compute_instance_public_ips` | `gcp_instance_public_ip` | STABLE | `GCPInstance`, `GCPNetworkInterface`, `GCPNicAccessConfig`, `GCPProject` | yes |
| `gcp_default_network_exists` | `gcp_default_network_exists` | STABLE | `GCPProject`, `GCPVpc` | yes |
| `gcp_default_service_account_full_cloud_api_scope` | `gcp_instance_default_service_account_full_api` | STABLE | `GCPInstance`, `GCPProject` | yes |
| `gcp_instances_not_blocking_project_wide_ssh_keys` | `gcp_instance_project_wide_ssh_keys` | STABLE | `GCPInstance`, `GCPProject` | yes |
| `gcp_instances_using_default_service_account` | `gcp_instance_default_service_account` | STABLE | `GCPInstance`, `GCPProject` | yes |
| `gcp_instances_with_ip_forwarding` | `gcp_instance_ip_forwarding` | STABLE | `GCPInstance`, `GCPProject` | yes |
| `gcp_instances_with_serial_port_access` | `gcp_instance_serial_port_enabled` | STABLE | `GCPInstance`, `GCPProject` | yes |
| `gcp_instances_without_confidential_computing_enabled` | `gcp_instance_confidential_compute_disabled` | STABLE | `GCPInstance`, `GCPProject` | yes |
| `gcp_instances_without_shielded_vm_enabled` | `gcp_instance_shielded_vm_disabled` | STABLE | `GCPInstance`, `GCPProject` | yes |
| `gcp_kms_keys_without_rotation_policy` | `gcp_kms_key_without_rotation_policy` | STABLE | `GCPCryptoKey`, `GCPProject` | no: missing `GCPCryptoKey` |
| `gcp_projects_without_effective_os_login` | `gcp_project_oslogin_disabled` | STABLE | `GCPInstance`, `GCPProject` | yes |
| `gcp_subnets_without_compliant_vpc_flow_logs` | `gcp_subnet_flow_logs_disabled` | STABLE | `GCPProject`, `GCPSubnet` | yes |
| `gcp_unrestricted_rdp_access` | `gcp_unrestricted_rdp` | STABLE | `GCPFirewall`, `GCPInstance`, `GCPIpRange`, `GCPIpRule`, `GCPNetworkInterface`, `GCPProject`, `GCPSubnet`, `GCPVpc` | yes |
| `gcp_unrestricted_ssh_access` | `gcp_unrestricted_ssh` | STABLE | `GCPFirewall`, `GCPInstance`, `GCPIpRange`, `GCPIpRule`, `GCPNetworkInterface`, `GCPProject`, `GCPSubnet`, `GCPVpc` | yes |
| `identity_administration_privileges` | `gcp_account_manipulation_permissions` | EXPERIMENTAL | `GCPPolicyBinding`, `GCPPrincipal`, `GCPRole` | yes |
| `kubernetes_control_plane_exposed` | `gcp_gke_control_plane_exposed` | EXPERIMENTAL | `GKECluster` | no: missing `GKECluster` |
| `object_storage_public` | `gcp_bucket_public` | EXPERIMENTAL | `GCPBucket`, `GCPPolicyBinding` | yes |
| `policy_administration_privileges` | `gcp_policy_manipulation_capabilities` | EXPERIMENTAL | `GCPPolicyBinding`, `GCPPrincipal`, `GCPRole` | yes |
| `serverless_workload_exposed` | `gcp_cloud_run_public_ingress` | EXPERIMENTAL | `GCPCloudRunService`, `GCPPolicyBinding` | no: missing `GCPCloudRunService` |
| `serverless_workload_exposed` | `gcp_cloud_function_http_trigger` | EXPERIMENTAL | `GCPCloudFunction`, `GCPPolicyBinding` | no: missing `GCPCloudFunction` |

<!-- GENERATED:END -->
