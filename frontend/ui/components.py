"""Shared, domain-neutral UI components: banner, classification badges, agent responses, panels."""

from __future__ import annotations

import json
from typing import Any

import pandas as pd
import streamlit as st

from ui.api import ApiError, get

CLASSIFICATION_STYLE = {
    "verified_fact": ("Verified fact", "#1b7f3b", "#e6f4ea"),
    "potential_exposure": ("Potential exposure", "#a15c00", "#fff3e0"),
    "unverified_hypothesis": ("Unverified hypothesis", "#5b3fa0", "#efe9fb"),
}
SEVERITY_COLOR = {"critical": "#b3261e", "high": "#d9480f", "medium": "#a15c00", "low": "#4f5b62"}


def page_setup(title: str) -> None:
    st.set_page_config(page_title=f"{title} | Enterprise IT Digital Twin", layout="wide")
    st.markdown(
        "<div style='background:#fff4ce;border:1px solid #e0b100;border-radius:6px;padding:8px 14px;"
        "margin-bottom:12px;color:#5c4400;font-weight:600'>SYNTHETIC DEMO DATA &mdash; every resource shown is "
        "fictional and was generated for this proof of concept. It is not a real Cartography ingestion run and "
        "no cloud credentials are used.</div>", unsafe_allow_html=True)


def badge(classification: str) -> str:
    label, fg, bg = CLASSIFICATION_STYLE.get(classification, (classification, "#333", "#eee"))
    return (f"<span style='background:{bg};color:{fg};border:1px solid {fg};border-radius:10px;"
            f"padding:1px 8px;font-size:0.8em;font-weight:600'>{label}</span>")


def severity_badge(severity: str, score: float | None = None) -> str:
    color = SEVERITY_COLOR.get(severity, "#333")
    text = f"{severity.upper()} {score:.1f}" if score is not None else severity.upper()
    return (f"<span style='background:{color};color:white;border-radius:10px;padding:1px 8px;"
            f"font-size:0.8em;font-weight:600'>{text}</span>")


def safe_get(path: str, **params: Any) -> Any:
    try:
        return get(path, **params)
    except ApiError as exc:
        st.error(f"API error {exc.status}: {exc.detail}")
    except Exception as exc:  # noqa: BLE001 - network errors shown to the user
        st.error(f"Cannot reach digital-twin-api: {exc}")
    return None


def render_agent_response(resp: dict[str, Any], key: str) -> None:
    status = resp.get("status")
    if status == "insufficient_evidence":
        st.warning("Insufficient evidence: the graph does not contain what this question needs.")
    st.markdown(resp.get("answer", ""))
    counts = resp.get("classification_counts", {})
    st.markdown(" ".join(f"{badge(k)} &times; {v}" for k, v in counts.items()), unsafe_allow_html=True)
    usage = resp.get("usage", {})
    st.caption(f"Tool calls: {usage.get('tool_calls')} | iterations: {usage.get('iterations')} | "
               f"tokens: {usage.get('tokens')} | {usage.get('seconds')} s | stop: {resp.get('stop_reason')}")

    with st.expander(f"Generated Cypher ({len(resp.get('cypher', []))})"):
        if not resp.get("cypher"):
            st.write("No Cypher was executed.")
        for item in resp.get("cypher", []):
            st.caption(item["tool"])
            st.code(item["cypher"], language="cypher")
    with st.expander(f"Tool execution trace ({len(resp.get('trace', []))} steps)"):
        for step in resp.get("trace", []):
            icon = "OK" if step["ok"] else "FAILED"
            st.markdown(f"**{step['step']}. {step['tool']}** ({step['owner']}) - {icon} - {step['summary']} "
                        f"- {step['evidence_ids']} evidence ids - {step['duration_ms']} ms")
            st.json(step["arguments"], expanded=False)
            if step.get("error"):
                st.error(step["error"])
    with st.expander(f"Evidence ({len(resp.get('evidence', []))} statements, "
                     f"{len(resp.get('evidence_ids', []))} graph ids)"):
        for cls in CLASSIFICATION_STYLE:
            items = [e for e in resp.get("evidence", []) if e["classification"] == cls]
            if not items:
                continue
            st.markdown(badge(cls), unsafe_allow_html=True)
            for e in items:
                ids = ", ".join(f"`{i}`" for i in e["resource_ids"][:8])
                more = f" (+{len(e['resource_ids']) - 8} more)" if len(e["resource_ids"]) > 8 else ""
                st.markdown(f"- {e['statement']}  \n  <small>source: {e['source']} | ids: {ids}{more}</small>",
                            unsafe_allow_html=True)
    with st.expander(f"Unknowns and assumptions ({len(resp.get('unknowns', []))})"):
        for u in resp.get("unknowns", []):
            st.markdown(f"- {u}")


def render_metrics(data: dict[str, Any]) -> None:
    metrics = data.get("metrics", [])
    cols = st.columns(min(len(metrics), 6) or 1)
    for col, m in zip(cols, metrics):
        col.metric(m["label"], m["value"])
    with st.expander("Metric details"):
        st.json({m["label"]: m.get("detail") for m in metrics if m.get("detail") is not None}, expanded=False)


def render_findings(data: dict[str, Any], key: str) -> None:
    findings = data.get("findings", [])
    if not findings:
        st.info("No findings.")
        return
    table = pd.DataFrame([{"rank": i + 1, "risk": f["risk_score"], "severity": f["severity"],
                           "scenario": f["scenario"], "classification": f["classification"], "title": f["title"]}
                          for i, f in enumerate(findings)])
    st.dataframe(table, hide_index=True, width="stretch")
    for f in findings:
        with st.expander(f"{f['scenario']} | {f['risk_score']:.1f} | {f['title']}"):
            st.markdown(f"{severity_badge(f['severity'], f['risk_score'])} {badge(f['classification'])}",
                        unsafe_allow_html=True)
            st.markdown("**Facts from the graph**")
            for fact in f["facts"]:
                st.markdown(f"- {fact}")
            st.markdown("**Scoring factors** (deterministic)")
            st.dataframe(pd.DataFrame([f["factors"]]), hide_index=True, width="stretch")
            st.markdown("**Evidence chain (graph paths)**")
            for path in f["graph_paths"][:8]:
                st.code(" -> ".join(path), language=None)
            st.markdown(f"**Corroborating Cartography rules:** {', '.join(f['rule_refs']) or 'none'}")
            st.markdown("**Missing evidence (what would prove or disprove exploitability)**")
            for m in f["missing_evidence"]:
                st.markdown(f"- {m}")


def render_remediation_plan(data: dict[str, Any], key: str) -> None:
    phases = data.get("phases", {})
    cols = st.columns(3)
    for col, (name, ids) in zip(cols, phases.items()):
        col.markdown(f"**{name.capitalize()}** ({len(ids)})")
        for fid in ids:
            col.caption(fid)
    for item in data.get("items", []):
        rec = item.get("recommended_mitigation") or {}
        alt = item.get("alternative_mitigation") or {}
        with st.expander(f"Priority {item['priority_score']:.1f} | {item['title']}"):
            st.markdown(f"{severity_badge(item['severity'], item['risk_score'])} {badge(item['classification'])} "
                        f"confidence: **{item['confidence']}**", unsafe_allow_html=True)
            st.markdown(f"**Problem:** {item['problem']}")
            st.markdown(f"**Potential impact:** {item['potential_impact']}")
            st.markdown(f"**Recommended ({rec.get('id')}, {rec.get('disruption')} disruption):** {rec.get('title')}")
            for a in rec.get("actions", []):
                st.markdown(f"- {a}")
            st.markdown(f"_Operational consequence:_ {rec.get('operational_consequences')}")
            if alt:
                st.markdown(f"**Alternative ({alt.get('id')}, {alt.get('disruption')} disruption):** {alt.get('title')}")
            st.markdown("**Trade-offs**")
            for t in item["trade_offs"]:
                st.markdown(f"- {t}")
            st.markdown("**Validation steps**")
            for v in item["validation_steps"]:
                st.markdown(f"- {v}")
            st.markdown(f"**Rollback:** {item['rollback']}")
            st.markdown("**Missing information**")
            for m in item["missing_information"]:
                st.markdown(f"- {m}")
            st.caption(f"Affected resources: {', '.join(item['affected_resources'][:15])}")
    for note in data.get("notes", []):
        st.caption(note)


def render_rules(data: dict[str, Any], key: str) -> None:
    st.markdown(f"Engine: `{data.get('engine')}` | applicable: **{data['counts']['applicable']}** | "
                f"skipped: **{data['counts']['skipped']}**")
    st.markdown("**Applicable**")
    st.dataframe(pd.DataFrame([{"rule": r["rule_id"], "fact": r["fact_id"], "maturity": r["maturity"],
                                "asset": r["asset_label"]} for r in data["applicable"]]),
                 hide_index=True, width="stretch")
    st.markdown("**Skipped (cannot run on the available data)**")
    st.dataframe(pd.DataFrame([{"rule": r["rule_id"], "fact": r["fact_id"], "reason": r["reason"]}
                               for r in data["skipped"]]), hide_index=True, width="stretch")


PANEL_RENDERERS = {
    "metrics": lambda d, k: render_metrics(d),
    "findings": render_findings,
    "remediation_plan": render_remediation_plan,
    "rules": render_rules,
}


def render_panel(kind: str, data: Any, key: str) -> None:
    renderer = PANEL_RENDERERS.get(kind)
    if renderer is None:
        st.code(json.dumps(data, indent=2)[:5000], language="json")
    else:
        renderer(data, key)
