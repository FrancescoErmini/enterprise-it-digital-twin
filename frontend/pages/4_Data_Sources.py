"""Data Sources: providers, ingestion mode, status and graph freshness."""

from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from ui.components import page_setup, safe_get

page_setup("Data Sources")
st.title("Data Sources")

data = safe_get("/api/v1/datasources")
if data:
    g = data["graph"]
    c1, c2, c3 = st.columns(3)
    c1.metric("Nodes in graph", g["nodes"])
    c2.metric("Labels", g["labels"])
    c3.metric("Relationship types", g["relationship_types"])
    rows = []
    for s in data["sources"]:
        fresh = s.get("graph_lastupdated")
        rows.append({
            "provider": s["display_name"], "provider_id": s["provider_id"], "mode": s["ingestion_mode"],
            "status": s["status"], "completed_at": s["completed_at"],
            "graph freshness (lastupdated)": datetime.fromtimestamp(fresh, timezone.utc).isoformat() if fresh else None,
            "nodes at ingestion": s["counts"]["nodes"], "relationships at ingestion": s["counts"]["relationships"],
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    for s in data["sources"]:
        st.info(f"**{s['display_name']} / {s['ingestion_mode']}**: {s['description']}")
        with st.expander("Node counts by label at ingestion"):
            st.json(s["counts"]["nodes_by_label"], expanded=False)
    documented = [(s["provider_id"], m) for s in data["sources"] for m in s.get("documented_modes", [])]
    if documented:
        st.markdown("**Other ingestion modes (documented, not implemented)**")
        for provider, m in documented:
            st.markdown(f"- `{provider}` / `{m['ingestion_mode']}`: {m['status']} ({m['doc']})")
