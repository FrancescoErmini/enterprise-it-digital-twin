from graph_ingestion.registry import DataSource, register
from providers.gcp.synthetic.loader import load_synthetic_gcp

register(DataSource(
    provider_id="gcp",
    display_name="Google Cloud",
    ingestion_mode="synthetic",
    description=("Deterministic synthetic Google Cloud organisation written through Cartography's own "
                 "GCP transform/load functions. Not a real Cartography ingestion run."),
    root_label="GCPOrganization",
    loader=load_synthetic_gcp,
    documented_modes=({"ingestion_mode": "cartography", "status": "documented, not implemented",
                       "doc": "docs/architecture.md#future-live-ingestion-data_sourcegcp"},),
))
