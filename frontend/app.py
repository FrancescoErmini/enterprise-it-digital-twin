"""digital-twin-ui: Enterprise IT Digital Twin home."""

import streamlit as st

from ui.components import page_setup, safe_get

page_setup("Home")
st.title("Enterprise IT Digital Twin")
st.write("A cross-domain IT intelligence platform built on a technology knowledge graph. "
         "Each analytical workflow is a registered use case on top of the same graph, agent and evidence model.")

health = safe_get("/health")
if health:
    st.caption(f"API: {health['status']} | graph: {health['graph']} | "
               f"LLM configured: {'yes' if health['llm_configured'] else 'no'}")

sections = [
    ("IT Intelligence Agent", "pages/1_IT_Intelligence_Agent.py",
     "Ask questions in natural language. The agent plans, calls graph tools, and answers with evidence, "
     "generated Cypher, a tool trace and explicit unknowns."),
    ("Technology Graph Explorer", "pages/2_Technology_Graph_Explorer.py",
     "Search entities, inspect relationships and properties, render focused subgraphs, run validated read-only Cypher."),
    ("Use Cases", "pages/3_Use_Cases.py",
     "The registry of analytical workflows and their investigation views."),
    ("Data Sources", "pages/4_Data_Sources.py",
     "Providers feeding the graph, ingestion mode and status, and graph freshness."),
]
cols = st.columns(2)
for i, (title, page, text) in enumerate(sections):
    with cols[i % 2].container(border=True):
        st.subheader(title)
        st.write(text)
        st.page_link(page, label=f"Open {title}")

usecases = safe_get("/api/v1/usecases") or []
st.subheader("Registered use cases")
for uc in usecases:
    st.markdown(f"- **{uc['title']}** ({uc['domain']}) - {'ready' if uc['ready'] else 'missing data'}")
