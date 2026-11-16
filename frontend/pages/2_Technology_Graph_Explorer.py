"""Technology Graph Explorer: entities, relationships, evidence and focused subgraphs."""

import pandas as pd
import streamlit as st

from ui.api import ApiError, post
from ui.components import page_setup, safe_get

page_setup("Technology Graph Explorer")
st.title("Technology Graph Explorer")

tab_explore, tab_query, tab_schema = st.tabs(["Explore", "Read-only Cypher", "Schema"])


def _short(node_id: str) -> str:
    return node_id if len(node_id) <= 40 else "..." + node_id[-37:]


def _dot(graph: dict) -> str:
    lines = ["digraph G {", "rankdir=LR; node [shape=box, style=rounded, fontsize=10]; edge [fontsize=8];"]
    for n in graph["nodes"]:
        label = (n.get("_labels") or ["?"])[0]
        name = n.get("instancename") or n.get("email") or n.get("name") or n.get("displayname") or n.get("id")
        fill = "#ffd8a8" if n["id"] == graph["center"] else "#e7f5ff"
        lines.append(f'"{n["id"]}" [label="{label}\\n{_short(str(name))}", style="rounded,filled", fillcolor="{fill}"];')
    for e in graph["edges"]:
        lines.append(f'"{e["source"]}" -> "{e["target"]}" [label="{e["type"]}"];')
    lines.append("}")
    return "\n".join(lines)


with tab_explore:
    query = st.text_input("Search entities (id, name, email)", value=st.session_state.get("explorer_q", ""))
    selected = st.session_state.get("explorer_node")
    if query and len(query) >= 2:
        results = safe_get("/api/v1/graph/search", q=query) or []
        if results:
            options = {f"{r['name']}  [{', '.join(r['labels'][:2])}]": r["id"] for r in results}
            choice = st.selectbox("Matches", list(options))
            selected = options[choice]
        else:
            st.info("No matching entity.")
    depth = st.radio("Neighbourhood depth", [1, 2], horizontal=True)
    if selected:
        graph = safe_get("/api/v1/graph/neighborhood", node_id=selected, depth=depth)
        if graph:
            st.graphviz_chart(_dot(graph), width="stretch")
            center = next(n for n in graph["nodes"] if n["id"] == graph["center"])
            col1, col2 = st.columns(2)
            with col1:
                st.markdown(f"**Properties of** `{graph['center']}`")
                st.dataframe(pd.DataFrame([{"property": k, "value": str(v)} for k, v in sorted(center.items())]),
                             hide_index=True, width="stretch")
            with col2:
                st.markdown(f"**Relationships** ({len(graph['edges'])})")
                st.dataframe(pd.DataFrame(graph["edges"]), hide_index=True, width="stretch")
            with st.expander("Cypher used for this subgraph"):
                st.code(graph["cypher"], language="cypher")

with tab_query:
    st.write("Queries are validated (read-only, schema-checked, server-side EXPLAIN) and run in a READ transaction "
             "with timeout and row limits. Every attempt is audited.")
    cypher = st.text_area("Cypher", "MATCH (n)\nRETURN labels(n)[0] AS label, count(*) AS nodes\n"
                                    "ORDER BY nodes DESC LIMIT 25", height=120)
    if st.button("Run", type="primary"):
        try:
            result = post("/api/v1/graph/query", {"cypher": cypher, "question": "graph explorer ad hoc query"})
        except ApiError as exc:
            st.error(f"{exc.status}: {exc.detail}")
            result = None
        if result:
            verdict = result["validation"]["verdict"]
            (st.success if result["ok"] else st.error)(
                f"Validation: {verdict}" + ("" if result["ok"] else f" - {result['error']}"))
            if result["ok"]:
                st.caption(f"{result['row_count']} rows | {result['duration_ms']} ms | truncated: {result['truncated']}")
                st.dataframe(pd.DataFrame(result["rows"]), hide_index=True, width="stretch")
                with st.expander(f"Evidence ids ({len(result['evidence_ids'])})"):
                    st.write(result["evidence_ids"])
    audit = safe_get("/api/v1/audit/recent", limit=15) or []
    with st.expander("Recent query audit log"):
        st.dataframe(pd.DataFrame(audit), hide_index=True, width="stretch")

with tab_schema:
    schema = safe_get("/api/v1/graph/schema")
    if schema:
        st.markdown(f"{len(schema['labels'])} labels, {len(schema['relationship_types'])} relationship types "
                    "(introspected live)")
        st.dataframe(pd.DataFrame([{"label": k, "nodes": v["count"], "properties": ", ".join(v["properties"])}
                                   for k, v in schema["labels"].items()]), hide_index=True, width="stretch")
        st.dataframe(pd.DataFrame([{"type": k, "relationships": v["count"]}
                                   for k, v in schema["relationship_types"].items()]), hide_index=True,
                     width="stretch")
