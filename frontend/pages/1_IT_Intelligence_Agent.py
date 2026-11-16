"""IT Intelligence Agent: natural-language interaction with the technology graph."""

import streamlit as st

from ui.api import ApiError, post
from ui.components import page_setup, render_agent_response, safe_get

page_setup("IT Intelligence Agent")
st.title("IT Intelligence Agent")
st.write("The agent answers from the technology graph only. Every response shows the Cypher it ran, the tools it "
         "called, the evidence (by classification) and what remains unknown.")

usecases = safe_get("/api/v1/usecases") or []
scope_labels = {"Platform tools only": None} | {uc["title"]: uc["id"] for uc in usecases}
scope = st.selectbox("Scope", list(scope_labels), index=1 if usecases else 0,
                     help="A use-case scope adds that use case's tools and guidance to the platform tools.")
usecase_id = scope_labels[scope]
uc = next((u for u in usecases if u["id"] == usecase_id), None)

examples = (uc or {}).get("example_questions", [])
if examples:
    st.markdown("**Example prompts**")
    for i, q in enumerate(examples):
        if st.button(q, key=f"ex-{i}"):
            st.session_state["agent_question"] = q

question = st.text_area("Question", key="agent_question", height=90)
if st.button("Ask", type="primary", disabled=not question.strip()):
    with st.spinner("Investigating..."):
        try:
            st.session_state["agent_response"] = post("/api/v1/agent/ask",
                                                      {"question": question, "usecase_id": usecase_id})
        except ApiError as exc:
            st.session_state["agent_response"] = None
            st.error(f"{exc.status}: {exc.detail}")

resp = st.session_state.get("agent_response")
if resp:
    st.divider()
    st.markdown(f"**Q:** {resp['question']}")
    render_agent_response(resp, key="agent")
    followups = (uc or {}).get("follow_up_questions", [])
    if followups:
        st.markdown("**Suggested follow-up questions**")
        for i, q in enumerate(followups):
            if st.button(q, key=f"fu-{i}"):
                st.session_state["agent_question"] = q
                st.rerun()
