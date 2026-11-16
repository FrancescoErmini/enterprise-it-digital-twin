"""Use Cases: the registry of analytical workflows and each one's investigation workflow."""

import streamlit as st

from ui.api import ApiError, post
from ui.components import page_setup, render_agent_response, render_panel, safe_get

page_setup("Use Cases")
st.title("Use Cases")

usecases = safe_get("/api/v1/usecases") or []
if not usecases:
    st.stop()

titles = {uc["title"]: uc for uc in usecases}
uc = titles[st.selectbox("Registered use case", list(titles))]
st.markdown(f"**{uc['title']}** - domain `{uc['domain']}`, provider `{uc['provider']}`")
st.write(uc["description"])
if uc["missing_node_labels"]:
    st.warning(f"Graph is missing labels this use case needs: {', '.join(uc['missing_node_labels'])}")

panels = {p["id"]: p for p in uc["ui_panels"]}
step_overview, step_results, step_plan, step_ask, step_rules = st.tabs(
    ["1. Overview", "2. Investigation results", "3. Remediation plan", "4. Investigate with the agent",
     "Rules engine"])


def show(panel_id: str, container) -> None:
    with container:
        panel = panels.get(panel_id)
        if not panel:
            return
        st.caption(panel["description"])
        with st.spinner("Running live graph queries and rules..."):
            payload = safe_get(f"/api/v1/usecases/{uc['id']}/panels/{panel_id}")
        if payload:
            render_panel(panel["kind"], payload["data"], key=panel_id)


show("overview", step_overview)
show("findings", step_results)
show("remediation", step_plan)
show("rules", step_rules)

with step_ask:
    st.write("Questions run live against the graph and the rules engine through the use case's tools.")
    for i, q in enumerate(uc["example_questions"]):
        if st.button(q, key=f"uc-ex-{i}"):
            st.session_state["uc_question"] = q
    question = st.text_area("Question", key="uc_question", height=90)
    if st.button("Investigate", type="primary", disabled=not question.strip()):
        with st.spinner("The agent is investigating..."):
            try:
                st.session_state["uc_response"] = post(f"/api/v1/usecases/{uc['id']}/investigate",
                                                       {"question": question})
            except ApiError as exc:
                st.session_state["uc_response"] = None
                st.error(f"{exc.status}: {exc.detail}")
    if st.session_state.get("uc_response"):
        render_agent_response(st.session_state["uc_response"], key="uc")

st.divider()
st.caption("Roadmap use cases (Cloud FinOps, Identity Intelligence, IT Modernization, Impact Analysis, Resilience) "
           "are described in docs/roadmap.md and are not available here.")
