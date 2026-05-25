"""
KubeVerdict — Demo UI

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
    page_title="KubeVerdict — Demo",
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
        log_lines=[],            # per-node progress log
        hypothesis="",           # current hypothesis at interrupt
        review_payload=None,
        thread_id="",
        final_report=None,       # None = not yet run, {} = ran but no report
        cluster_fixed=False,     # True after remediation applied
        expected_patches=[],     # pre-computed from fresh graph before workflow
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
    st.title("🔍 KubeVerdict")
    st.caption("Automated Root Cause Analysis — `kubeverdict-demo` namespace")
with col_mode:
    auto_mode = st.toggle("Auto mode", value=False, help="Skip human validation step")

st.divider()

# ── Cluster state ─────────────────────────────────────────────────────────────

from demo.scenario_builder import INCIDENTS, HEALED_INCIDENTS, generate_patch_diffs

st.subheader("Cluster state")

_display_incidents = HEALED_INCIDENTS if st.session_state.cluster_fixed else INCIDENTS
severity_color = {"critical": "🔴", "high": "🟠", "medium": "🟡", "ok": "🟢"}

cols = st.columns(5)
for col, inc in zip(cols, _display_incidents):
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
    df = pd.DataFrame(_display_incidents)[["service", "status", "restarts", "cause"]]
    st.dataframe(df, use_container_width=True, hide_index=True)

st.divider()

# ── Controls + reasoning side-by-side ────────────────────────────────────────

left, right = st.columns([1, 2])

with left:
    st.subheader("Analysis")

    query = st.text_area(
        "Query",
        value=(
            "Multiple services are down in the kubeverdict-demo namespace. "
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
        for k in ["status", "tokens", "log_lines", "hypothesis", "review_payload",
                  "thread_id", "final_report", "cluster_fixed", "expected_patches",
                  "error", "elapsed"]:
            st.session_state[k] = {
                "status": "idle", "tokens": "", "log_lines": [], "hypothesis": "",
                "review_payload": None, "thread_id": "", "final_report": None,
                "cluster_fixed": False, "expected_patches": [], "error": None, "elapsed": 0.0,
            }[k]
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

    if st.session_state.status == "error" and st.session_state.error:
        st.error(st.session_state.error)

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
    log_placeholder      = st.empty()
    analysis_placeholder = st.empty()

    _log_text = "\n".join(st.session_state.log_lines) if st.session_state.log_lines else "Waiting for analysis…"
    log_placeholder.markdown(
        f'<div class="reasoning-box">{_log_text}</div>',
        unsafe_allow_html=True,
    )
    if st.session_state.tokens:
        analysis_placeholder.markdown(st.session_state.tokens)

st.divider()

# ── Final report ──────────────────────────────────────────────────────────────

if st.session_state.final_report is not None:
    r = st.session_state.final_report
    st.subheader("Root cause analysis report")

    _conf_raw  = (r.get("confidence") or "").strip()
    _conf_word = (_conf_raw.split()[0] if _conf_raw else "?").upper()
    _conf_badge = {"HIGH": "🟢 HIGH", "MEDIUM": "🟡 MEDIUM", "LOW": "🔴 LOW"}.get(_conf_word, f"⚪ {_conf_word}")
    _anchor_count = len(st.session_state.expected_patches)
    meta_cols = st.columns(3)
    meta_cols[0].metric("Confidence", _conf_badge)
    meta_cols[1].metric("K8s version", r.get("kube_version") or "demo")
    meta_cols[2].metric("Anchor violations", _anchor_count)

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

    _patches = st.session_state.expected_patches
    if _patches:
        _label  = "Applied patches (git diff)" if st.session_state.cluster_fixed else "Proposed patches (git diff)"
        _expand = st.session_state.cluster_fixed
        if st.session_state.cluster_fixed:
            st.success("All services restored to healthy state.")
        with st.expander(_label, expanded=_expand):
            for p in _patches:
                st.caption(f"`{p['entity']}`  —  field: `{p['field']}`")
                st.code(p["diff"], language="diff")

# ── Run workflow ──────────────────────────────────────────────────────────────

if run_btn:
    st.session_state.status    = "running"
    st.session_state.tokens    = ""
    st.session_state.log_lines = []
    st.session_state.elapsed   = 0.0
    st.session_state.final_report = None
    st.session_state.cluster_fixed = False
    st.session_state.thread_id = f"demo-{int(time.time())}"

    graph, store, wf = _build_infra()

    # Pre-compute patches from a fresh (unmodified) graph so anchor_node
    # mutations during the workflow don't affect the display.
    if not st.session_state.expected_patches:
        from demo.scenario_builder import build_graph as _build_demo_graph
        _fresh = _build_demo_graph()
        st.session_state.expected_patches = generate_patch_diffs(_fresh)

    cfg_run = {
        "configurable": {
            "thread_id": st.session_state.thread_id,
            "graph":     graph,
            "store":     store,
        }
    }
    initial_state = {
        "query":      query,
        "namespaces": ["kubeverdict-demo"],
        "edge_log":   [],
    }
    if auto_mode:
        cfg_run["configurable"]["auto_approve"] = True

    import config as cfg  # noqa: PLC0415

    _NODE_LABELS = {
        "ingest":          "Cluster state collected",
        "metrics":         "Metrics-server checked",
        "prometheus":      "Prometheus alerts correlated",
        "otel":            "OTel traces / Loki logs gathered",
        "gitops":          "GitOps drift checked",
        "anchor":          "Manifest anchors evaluated",
        "index":           "FAISS index ready",
        "signal_analysis": "Signal analysis (PatchTST) done",
        "hypothesize":     "Hypotheses generated",
        "example_lookup":  "Knowledge base searched",
        "analyze":         "LLM root-cause analysis done",
        "increment_retry": "Retry — widening BFS",
        "archive_path":    "Path archived → next hypothesis",
        "select_best":     "Best hypothesis selected",
        "dry_run":         "Dry-run validation done",
        "human_review":    "Human review gate",
        "remediation":     "Remediation applied",
        "save_example":    "Saved to knowledge base",
    }
    import os as _os
    _provider = _os.getenv("LLM_PROVIDER", "ollama").lower()
    _llm_label = {
        "ollama":    f"Ollama / {cfg.OLLAMA_MODEL}",
        "anthropic": f"Anthropic / {_os.getenv('ANTHROPIC_MODEL', 'claude-sonnet-4-6')}",
        "openai":    f"OpenAI / {_os.getenv('OPENAI_MODEL', 'gpt-4o-mini')}",
        "google":    f"Google / {_os.getenv('GOOGLE_MODEL', 'gemini-2.0-flash')}",
    }.get(_provider, _provider)
    _PENDING_AFTER = {
        "example_lookup": f"⏳  LLM root-cause analysis  ({_llm_label})…",
        "analyze":        "⏳  Selecting best hypothesis…",
    }

    t0 = time.time()

    try:
        import asyncio

        async def _stream():
            from langgraph.types import Command

            log_lines: list[str] = []

            def _update_log(node_name: str, node_output: dict) -> None:
                t_now = round(time.time() - t0, 1)
                label = _NODE_LABELS.get(node_name)
                if label:
                    log_lines.append(f"[{t_now:5.1f}s] ✅  {label}")
                for src in ("analyze", "archive_path"):
                    if node_name == src:
                        raw = (node_output or {}).get("raw_analysis", "")
                        if raw and raw != st.session_state.tokens:
                            st.session_state.tokens = raw
                            analysis_placeholder.markdown(raw)
                pending = _PENDING_AFTER.get(node_name)
                if pending:
                    log_lines.append(pending)
                st.session_state.log_lines = log_lines
                st.session_state.elapsed   = t_now
                log_placeholder.markdown(
                    f'<div class="reasoning-box">{"<br>".join(log_lines)}</div>',
                    unsafe_allow_html=True,
                )

            async for updates in wf.astream(initial_state, cfg_run, stream_mode="updates"):
                for node_name, node_output in updates.items():
                    _update_log(node_name, node_output or {})

            # Check for human review interrupt
            snapshot = wf.get_state(cfg_run)
            if snapshot.next:
                if auto_mode:
                    # Auto-approve: resume directly without surfacing to human
                    t_now = round(time.time() - t0, 1)
                    log_lines.append(f"[{t_now:5.1f}s] 🤖  Auto-approve — applying remediation…")
                    log_placeholder.markdown(
                        f'<div class="reasoning-box">{"<br>".join(log_lines)}</div>',
                        unsafe_allow_html=True,
                    )
                    async for updates in wf.astream(
                        Command(resume="approve"), cfg_run, stream_mode="updates"
                    ):
                        for node_name, node_output in updates.items():
                            _update_log(node_name, node_output or {})
                    snapshot = wf.get_state(cfg_run)

            if snapshot.next:
                # Still paused — surface human review panel
                st.session_state.status = "awaiting_review"
                for task in (snapshot.tasks or []):
                    if getattr(task, "interrupts", None):
                        st.session_state.review_payload = task.interrupts[0].value
                        break
            else:
                final = snapshot.values
                st.session_state.final_report  = final.get("report_dict")  # keep None or {}
                st.session_state.status        = "done"
                st.session_state.cluster_fixed = True

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
                st.session_state.final_report  = snapshot.values.get("report_dict")
                st.session_state.status         = "done"
                st.session_state.cluster_fixed  = True

        asyncio.run(_resume())
    except Exception as exc:
        st.session_state.status = "error"
        st.session_state.error  = str(exc)

    st.rerun()
