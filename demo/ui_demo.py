"""
KubeWhisperer — Demo UI

Standalone Streamlit app for the VHS demo.
No real Kubernetes cluster required — uses the pre-built demo scenario.

Run:
    streamlit run demo/ui_demo.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(dotenv_path=ROOT / ".env", override=False)

import streamlit as st

st.set_page_config(
    page_title="KubeWhisperer — Demo",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Custom CSS ────────────────────────────────────────────────────────────────

st.markdown("""
<style>
.incident-critical { color: #ff4b4b; font-weight: bold; }
.incident-high     { color: #ffa500; font-weight: bold; }
.incident-medium   { color: #f0c040; font-weight: bold; }
.incident-ok       { color: #21c354; }
.reasoning-box {
    background: #0e1117;
    border: 1px solid #2d3748;
    border-radius: 8px;
    padding: 1rem;
    font-family: monospace;
    font-size: 0.85rem;
    height: 420px;
    overflow-y: auto;
    white-space: pre-wrap;
    color: #e2e8f0;
}
.status-badge {
    display: inline-block;
    padding: 2px 10px;
    border-radius: 4px;
    font-size: 0.8rem;
    font-weight: bold;
}
</style>
""", unsafe_allow_html=True)

# ── Load scenario & build infra once ─────────────────────────────────────────

@st.cache_resource(show_spinner="Building demo scenario…")
def _build_infra():
    from demo.scenario_builder import build_graph
    from vectorstore.embedder import Embedder
    from vectorstore.store import FAISSStore
    from workflow.graph import build_graph as build_wf

    graph = build_graph()
    embedder = Embedder()
    store = FAISSStore(embedder=embedder)
    store.index_graph(graph)
    store.index_anchor_violations(graph)
    wf = build_wf()
    return graph, store, wf


# ── Session state ─────────────────────────────────────────────────────────────

def _init_state():
    defaults = dict(
        status="idle",           # idle | running | awaiting_review | done | error
        tokens="",               # accumulated reasoning tokens
        hypothesis="",           # current hypothesis at interrupt
        review_payload=None,
        thread_id="",
        final_report=None,
        error=None,
        elapsed=0.0,
    )
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init_state()

# ── Header ────────────────────────────────────────────────────────────────────

col_title, col_mode = st.columns([3, 1])
with col_title:
    st.title("🔍 KubeWhisperer")
    st.caption("Automated Root Cause Analysis — `kubewhisperer-demo` namespace")
with col_mode:
    auto_mode = st.toggle("Auto mode", value=False, help="Skip human validation step")

st.divider()

# ── Cluster state ─────────────────────────────────────────────────────────────

from demo.scenario_builder import INCIDENTS

st.subheader("Cluster state")

cols = st.columns(5)
severity_color = {"critical": "🔴", "high": "🟠", "medium": "🟡", "ok": "🟢"}

for col, inc in zip(cols, INCIDENTS):
    with col:
        icon = severity_color[inc["severity"]]
        st.metric(
            label=f"{icon} {inc['service']}",
            value=inc["status"],
            delta=f"↺ {inc['restarts']}" if inc["restarts"] else None,
            delta_color="inverse" if inc["restarts"] else "off",
        )

with st.expander("Incident details", expanded=False):
    import pandas as pd
    df = pd.DataFrame(INCIDENTS)[["service", "status", "restarts", "cause"]]
    st.dataframe(df, use_container_width=True, hide_index=True)

st.divider()

# ── Controls + reasoning side-by-side ────────────────────────────────────────

left, right = st.columns([1, 2])

with left:
    st.subheader("Analysis")

    query = st.text_area(
        "Query",
        value=(
            "Multiple services are down in the kubewhisperer-demo namespace. "
            "Identify all root causes and provide precise remediation commands."
        ),
        height=100,
    )

    col_run, col_reset = st.columns(2)
    run_btn   = col_run.button(
        "▶ Run analysis",
        type="primary",
        use_container_width=True,
        disabled=st.session_state.status == "running",
    )
    reset_btn = col_reset.button("↺ Reset", use_container_width=True)

    if reset_btn:
        for k in ["status", "tokens", "hypothesis", "review_payload",
                  "thread_id", "final_report", "error", "elapsed"]:
            st.session_state[k] = {"status": "idle", "tokens": "", "hypothesis": "",
                                   "review_payload": None, "thread_id": "",
                                   "final_report": None, "error": None, "elapsed": 0.0}[k]
        st.rerun()

    # Status badge
    status_icons = {
        "idle":            ("⬜", "Waiting"),
        "running":         ("🔵", "Analysing…"),
        "awaiting_review": ("🟡", "Awaiting review"),
        "done":            ("🟢", "Complete"),
        "error":           ("🔴", "Error"),
    }
    icon, label = status_icons.get(st.session_state.status, ("⬜", "—"))
    st.markdown(f"**Status:** {icon} {label}")

    if st.session_state.elapsed:
        st.caption(f"Elapsed: {st.session_state.elapsed:.1f}s")

    # Human review panel
    if st.session_state.status == "awaiting_review" and not auto_mode:
        st.divider()
        st.subheader("Human review")
        payload = st.session_state.review_payload or {}
        hypo = payload.get("current_hypothesis") or payload.get("hypothesis", "")
        if hypo:
            st.info(f"**Proposed hypothesis:**\n\n{hypo}")
        fixes = payload.get("anchor_fixes") or []
        if fixes:
            st.markdown("**Proposed fixes:**")
            for f in fixes[:3]:
                st.code(f, language="bash")

        c1, c2 = st.columns(2)
        if c1.button("✅ Approve", type="primary", use_container_width=True):
            st.session_state["_review_decision"] = "approve"
            st.rerun()
        if c2.button("❌ Reject", use_container_width=True):
            st.session_state["_review_decision"] = "reject"
            st.rerun()

with right:
    st.subheader("Reasoning stream")
    reasoning_placeholder = st.empty()
    reasoning_placeholder.markdown(
        f'<div class="reasoning-box">{st.session_state.tokens or "Waiting for analysis…"}</div>',
        unsafe_allow_html=True,
    )

st.divider()

# ── Final report ──────────────────────────────────────────────────────────────

if st.session_state.final_report:
    r = st.session_state.final_report
    st.subheader("Root cause analysis report")

    meta_cols = st.columns(3)
    meta_cols[0].metric("Confidence", r.get("confidence", "?"))
    meta_cols[1].metric("K8s version", r.get("kube_version", "?"))
    meta_cols[2].metric("Hypotheses explored", len(r.get("reasoning_history", [])))

    if r.get("causal_chain"):
        st.markdown("**Causal chain:**")
        for step in r["causal_chain"]:
            st.markdown(f"- {step}")

    if r.get("remediation"):
        st.markdown("**Remediation commands:**")
        for cmd in r["remediation"]:
            st.code(cmd, language="bash")

    if r.get("anchor_fixes"):
        with st.expander("Anchor drift fixes"):
            for fix in r["anchor_fixes"]:
                st.code(fix, language="bash")

# ── Run workflow ──────────────────────────────────────────────────────────────

if run_btn:
    st.session_state.status  = "running"
    st.session_state.tokens  = ""
    st.session_state.elapsed = 0.0
    st.session_state.final_report = None
    st.session_state.thread_id = f"demo-{int(time.time())}"

    graph, store, wf = _build_infra()

    cfg_run = {
        "configurable": {
            "thread_id": st.session_state.thread_id,
            "graph":     graph,
            "store":     store,
        }
    }
    initial_state = {
        "query":      query,
        "namespaces": ["kubewhisperer-demo"],
        "edge_log":   [],
    }
    if auto_mode:
        cfg_run["configurable"]["auto_approve"] = True

    t0 = time.time()

    try:
        import asyncio

        async def _stream():
            async for state in wf.astream(initial_state, cfg_run, stream_mode="values"):
                # Accumulate reasoning tokens
                history = state.get("reasoning_history") or []
                if history:
                    last = history[-1]
                    analysis = last.get("raw_analysis", "") if isinstance(last, dict) else ""
                    if analysis and analysis not in st.session_state.tokens:
                        st.session_state.tokens = analysis
                        reasoning_placeholder.markdown(
                            f'<div class="reasoning-box">{analysis}</div>',
                            unsafe_allow_html=True,
                        )

                # Check for interrupt
                hypo = state.get("current_hypothesis", "")
                if hypo:
                    st.session_state.hypothesis = hypo

                st.session_state.elapsed = round(time.time() - t0, 1)

            # Check for human review interrupt
            snapshot = wf.get_state(cfg_run)
            if snapshot.next:
                st.session_state.status = "awaiting_review"
                for task in (snapshot.tasks or []):
                    if getattr(task, "interrupts", None):
                        st.session_state.review_payload = task.interrupts[0].value
                        break
            else:
                final = snapshot.values
                st.session_state.final_report = final.get("report_dict") or {}
                st.session_state.status = "done"

        asyncio.run(_stream())

    except Exception as exc:
        st.session_state.status = "error"
        st.session_state.error  = str(exc)

    st.rerun()

# ── Handle review decision ────────────────────────────────────────────────────

if "_review_decision" in st.session_state and st.session_state.status == "awaiting_review":
    decision = st.session_state.pop("_review_decision")
    graph, store, wf = _build_infra()
    from langgraph.types import Command

    cfg_run = {
        "configurable": {
            "thread_id": st.session_state.thread_id,
            "graph":     graph,
            "store":     store,
        }
    }
    try:
        import asyncio

        async def _resume():
            async for state in wf.astream(Command(resume=decision), cfg_run, stream_mode="values"):
                pass
            snapshot = wf.get_state(cfg_run)
            if not snapshot.next:
                st.session_state.final_report = snapshot.values.get("report_dict") or {}
                st.session_state.status = "done"

        asyncio.run(_resume())
    except Exception as exc:
        st.session_state.status = "error"
        st.session_state.error  = str(exc)

    st.rerun()
