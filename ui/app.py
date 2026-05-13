"""
KubeWhisperer — Streamlit UI

Run:
    streamlit run ui/app.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(dotenv_path=ROOT / ".env", override=False)

import streamlit as st  # noqa: E402
from langgraph.types import Command  # noqa: E402

import config as cfg  # noqa: E402

# ── Page config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="KubeWhisperer RCA",
    page_icon="🔍",
    layout="wide",
)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_contexts() -> list[str]:
    try:
        r = subprocess.run(
            ["kubectl", "config", "get-contexts", "-o", "name"],
            capture_output=True, text=True, timeout=5,
        )
        return r.stdout.strip().splitlines() or ["default"]
    except Exception:
        return ["default"]


def _current_context() -> str:
    try:
        r = subprocess.run(
            ["kubectl", "config", "current-context"],
            capture_output=True, text=True, timeout=5,
        )
        return r.stdout.strip()
    except Exception:
        return ""


@st.cache_resource
def _build_workflow():
    from workflow.graph import build_graph
    return build_graph()


def _badge(d: dict) -> str:
    if not d:
        return "—"
    if d.get("skipped"):
        return "⏭ skipped"
    if d.get("fallback") or d.get("error"):
        return "⚠ fallback"
    return "✓ ok"


def _conf_icon(conf: str) -> str:
    c = (conf or "").upper()
    if c.startswith("HIGH"):
        return "🟢"
    if c.startswith("MEDIUM"):
        return "🟡"
    return "🔴"


def _kubeconfig() -> str:
    return cfg.KUBECONFIG or os.path.expanduser("~/.kube/config")


# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("KubeWhisperer")
    st.caption("Kubernetes Root Cause Analysis")
    st.divider()

    contexts    = _get_contexts()
    current_ctx = _current_context()
    default_idx = contexts.index(current_ctx) if current_ctx in contexts else 0

    kube_context = st.selectbox("Kube context", contexts, index=default_idx)
    namespace    = st.text_input("Namespace", value="kubewhisperer-demo")

    st.divider()
    st.caption("**Optional collectors**")

    col_a, col_b = st.columns(2)
    use_metrics  = col_a.toggle("Metrics server", value=True)
    use_gitops   = col_b.toggle("GitOps drift",   value=bool(cfg.GITOPS_REPO_URL))
    col_c, col_d = st.columns(2)
    use_prom     = col_c.toggle("Prometheus",      value=False)
    use_otel     = col_d.toggle("OTel / Loki",     value=False)

    gitops_url = ""
    if use_gitops:
        gitops_url = st.text_input(
            "GitOps repo URL",
            value=cfg.GITOPS_REPO_URL or "file:///tmp/kw-demo-gitops",
        )

    st.divider()
    model = st.text_input("Ollama model", value=cfg.OLLAMA_MODEL)
    query = st.text_area(
        "Query",
        value=(
            "Multiple services are failing. "
            "Identify every root cause and provide precise remediation commands."
        ),
        height=90,
    )

    col_run, col_reset = st.columns(2)
    run_btn   = col_run.button("▶ Run",   type="primary", use_container_width=True)
    reset_btn = col_reset.button("↺ Reset", use_container_width=True)

    st.divider()
    st.caption(f"Model : {model} @ {cfg.OLLAMA_URL}")
    st.caption(f"KubeCFG : {_kubeconfig()}")

# ── Session state init ────────────────────────────────────────────────────────

if "status" not in st.session_state or reset_btn:
    st.session_state.update(
        status="idle",
        interrupt_payload={},
        ingestion_stats={},
        wf_config={},
        thread_id="",
        final_state={},
        elapsed=0.0,
        run_log=[],
        analysis_context={},   # kube_context, namespace, entities, kube_version
        drift_items=[],        # DriftItem list from GitOps collection
        kb_entities=[],        # entity rows for Knowledge Base tab
        kb_anchors=[],         # anchor records for Knowledge Base tab
        kb_anchor_fixes=[],    # helm fix suggestions from manifest anchors
    )

# ── Run workflow ──────────────────────────────────────────────────────────────

if run_btn and st.session_state.status not in ("running",):
    st.session_state.status = "running"
    st.session_state.interrupt_payload = {}
    st.session_state.run_log = []
    stats: dict = {}

    thread_id = f"ui-{int(time.time())}"
    st.session_state.thread_id = thread_id

    kc = _kubeconfig()

    # ── K8s + Helm ────────────────────────────────────────────────────────────
    with st.spinner("Collecting K8s + Helm state…"):
        try:
            from ingestion.k8s_collector import K8sCollector
            from ingestion.helm_collector import HelmCollector
            from ontology.entities import ResourceKind as _RK

            collector = K8sCollector(kubeconfig=kc, context=kube_context)
            graph = collector.collect(
                namespaces=[namespace] if namespace else None
            )
            helm = HelmCollector(kubeconfig=kc, kube_context=kube_context)
            helm.collect(graph, namespaces=[namespace] if namespace else None)
            helm_releases = sum(1 for _ in graph.entities(_RK.HELM_RELEASE))

            kv = str(collector.kube_version) if collector.kube_version else "?"
            stats["ingest"] = {
                "entities":     graph.node_count,
                "edges":        graph.edge_count,
                "helm_releases": helm_releases,
                "kube_version": kv,
                "fallback":     False,
            }
            st.session_state.analysis_context = {
                "kube_context": kube_context,
                "namespace":    namespace or "all",
                "kubeconfig":   kc,
                "entities":     graph.node_count,
                "kube_version": kv,
            }
            # Snapshot entities for the KB tab (lightweight dicts only)
            st.session_state.kb_entities = [
                {
                    "kind":        getattr(e.kind, "value", str(e.kind)),
                    "namespace":   e.namespace or "",
                    "name":        e.name,
                    "annotations": len(e.annotations),
                    "uid":         e.uid,
                }
                for e in graph.entities()
            ]
        except Exception as exc:
            st.session_state.status = "error"
            st.session_state.run_log.append(f"Collection failed: {exc}")
            st.session_state.ingestion_stats = stats
            st.rerun()

    # ── Metrics server ────────────────────────────────────────────────────────
    if use_metrics:
        with st.spinner("Metrics server…"):
            try:
                from ingestion.metrics_server_collector import MetricsServerCollector
                count = MetricsServerCollector(
                    kubeconfig=kc, context=kube_context
                ).collect(graph)
                stats["metrics"] = {"pods_annotated": count, "fallback": False}
            except Exception as exc:
                stats["metrics"] = {"fallback": True, "error": str(exc)}
    else:
        stats["metrics"] = {"skipped": True}

    # ── Prometheus ────────────────────────────────────────────────────────────
    if use_prom:
        with st.spinner("Prometheus alerts…"):
            try:
                from ingestion.prometheus_collector import PrometheusCollector
                count = PrometheusCollector(
                    url=cfg.PROMETHEUS_URL,
                    token=cfg.PROMETHEUS_TOKEN,
                    timeout=cfg.PROMETHEUS_TIMEOUT,
                ).collect(graph)
                stats["prometheus"] = {"alerts": count, "fallback": False}
            except Exception as exc:
                stats["prometheus"] = {"fallback": True, "error": str(exc)}
    else:
        stats["prometheus"] = {"skipped": True}

    # ── OTel / Loki ───────────────────────────────────────────────────────────
    if use_otel:
        stats["otel"] = {}
        with st.spinner("OTel traces + Loki logs…"):
            try:
                from ingestion.otel_backend import build_backend
                from ingestion.otel_collector import OtelCollector
                backend = build_backend(
                    cfg.OTEL_BACKEND_TYPE, cfg.OTEL_BACKEND_URL,
                    cfg.OTEL_TOKEN, cfg.OTEL_TIMEOUT,
                )
                c = OtelCollector(backend, lookback_hours=cfg.OTEL_LOOKBACK_HOURS).collect(graph)
                stats["otel"]["traces"] = c
            except Exception as exc:
                stats["otel"]["traces_fallback"] = str(exc)
            try:
                from ingestion.loki_source import LokiSource
                c = LokiSource(
                    url=cfg.LOKI_URL, token=cfg.LOKI_TOKEN,
                    timeout=cfg.LOKI_TIMEOUT,
                    lookback_hours=cfg.LOKI_LOOKBACK_HOURS,
                    max_logs_per_pod=cfg.LOKI_MAX_LOGS_PER_POD,
                ).collect(graph)
                stats["otel"]["logs"] = c
            except Exception as exc:
                stats["otel"]["logs_fallback"] = str(exc)
    else:
        stats["otel"] = {"skipped": True}

    # ── GitOps drift ──────────────────────────────────────────────────────────
    provider = None
    if use_gitops and gitops_url:
        with st.spinner("GitOps drift detection…"):
            try:
                from ingestion.git_provider import GithubProvider, LocalGitProvider
                from ingestion.gitops_collector import GitopsCollector

                if gitops_url.startswith(("https://github.com", "git@github.com")):
                    repo = gitops_url.removeprefix("https://github.com/").removesuffix(".git")
                    provider = GithubProvider(repo=repo, ref=cfg.GITOPS_BRANCH, token=cfg.GITHUB_TOKEN)
                else:
                    provider = LocalGitProvider(repo_url=gitops_url, branch=cfg.GITOPS_BRANCH)

                drifts = GitopsCollector(provider, charts_path=cfg.GITOPS_CHARTS_PATH).collect(graph)
                critical = sum(1 for d in drifts if d.severity == "critical")
                stats["gitops"] = {"drifts": len(drifts), "critical": critical, "fallback": False}
                st.session_state.drift_items = drifts
            except Exception as exc:
                stats["gitops"] = {"fallback": True, "error": str(exc)}
    else:
        stats["gitops"] = {"skipped": True}

    # ── Anchors ───────────────────────────────────────────────────────────────
    with st.spinner("Collecting anchors…"):
        try:
            from ingestion.anchor_engine import AnchorEngine
            records = AnchorEngine().collect(
                graph, provider=provider, charts_path=cfg.GITOPS_CHARTS_PATH
            )
            with_manifest = sum(1 for r in records if r.source == "manifest")
            stats["anchor"] = {
                "total": len(records), "manifest": with_manifest,
                "schema": len(records) - with_manifest, "fallback": False,
            }
            # Snapshot for KB tab
            st.session_state.kb_anchors = [
                {
                    "entity":    getattr(r, "entity_kind", "") + "/" + getattr(r, "entity_name", ""),
                    "field":     getattr(r, "field_path", getattr(r, "key", str(r))),
                    "value":     str(getattr(r, "declared_value", getattr(r, "value", ""))),
                    "source":    getattr(r, "source", ""),
                }
                for r in records
            ]
            # Compute helm fix suggestions for manifest anchors on unhealthy entities
            try:
                from rca.context_builder import anchor_fix_hints
                from dedup.bfs import find_unhealthy
                st.session_state.kb_anchor_fixes = anchor_fix_hints(graph, find_unhealthy(graph))
            except Exception:
                st.session_state.kb_anchor_fixes = []
        except Exception as exc:
            stats["anchor"] = {"fallback": True, "error": str(exc)}

    # ── Index + build store ───────────────────────────────────────────────────
    with st.spinner("Indexing entities + enterprise docs…"):
        from vectorstore.embedder import Embedder
        from vectorstore.store import FAISSStore
        from knowledge import DocStore, DocIndexer, ExampleStore, ExampleIndexer
        store = FAISSStore(embedder=Embedder())
        store.index_graph(graph)
        doc_store      = DocStore()
        doc_chunks     = DocIndexer(store).index_all(doc_store)
        example_store  = ExampleStore()
        example_count  = ExampleIndexer(store).index_all(example_store)
        stats["index"] = {
            "vectors":       store.size,
            "doc_chunks":    doc_chunks,
            "examples":      example_count,
            "fallback":      False,
        }

    # ── Signal analysis ───────────────────────────────────────────────────────
    with st.spinner("PatchTST signal analysis…"):
        try:
            from signals.analyzer import SignalAnalyzer
            prom_src = None
            if use_prom:
                from signals.prometheus_source import PrometheusMetricSource
                prom_src = PrometheusMetricSource(url=cfg.PROMETHEUS_URL)
            results = SignalAnalyzer(prometheus_source=prom_src).analyze(graph)
            anomalous = [r for r in results if r.is_anomalous]
            stats["signals"] = {
                "total": len(results), "anomalous": len(anomalous),
                "mode": "real" if prom_src else "synthetic", "fallback": False,
            }
        except Exception as exc:
            stats["signals"] = {"fallback": True, "error": str(exc)}

    st.session_state.ingestion_stats = stats

    # ── Stream LangGraph workflow (nodes skip pre-built graph/store) ──────────
    wf_config = {
        "configurable": {
            "thread_id": thread_id,
            "graph":    graph,
            "store":    store,
            **({"provider": provider} if provider else {}),
        }
    }
    st.session_state.wf_config = wf_config

    initial_state = {
        "query":            query,
        "retry_count":      0,
        "human_decision":   "",
        "error":            "",
        "ingestion_stats":  stats,
    }

    compiled = _build_workflow()
    t0 = time.perf_counter()

    try:
        with st.spinner("LLM root-cause analysis…"):
            for event in compiled.stream(initial_state, config=wf_config):
                if "__interrupt__" in event:
                    payload = event["__interrupt__"][0].value
                    st.session_state.interrupt_payload = payload
                    st.session_state.status = "interrupted"
                    break
                # Merge stats from nodes — never overwrite a step the UI already
                # successfully collected (workflow nodes skip pre-built graph/store).
                for node_output in event.values():
                    if isinstance(node_output, dict) and "ingestion_stats" in node_output:
                        for k, v in node_output["ingestion_stats"].items():
                            existing = st.session_state.ingestion_stats.get(k, {})
                            # Only accept node output if UI had no data or it was fallback
                            if not existing or existing.get("skipped") or existing.get("fallback"):
                                st.session_state.ingestion_stats[k] = v
            else:
                if st.session_state.status == "running":
                    st.session_state.status = "error"
                    st.session_state.run_log.append("Workflow ended without interrupt — check Ollama logs")
    except Exception as exc:
        st.session_state.status = "error"
        st.session_state.run_log.append(f"Workflow error: {exc}")

    st.session_state.elapsed = time.perf_counter() - t0
    st.rerun()

# ─────────────────────────────────────────────────────────────────────────────
# Render functions
# ─────────────────────────────────────────────────────────────────────────────

_STEPS = [
    ("ingest",     "K8s + Helm"),
    ("metrics",    "Metrics server"),
    ("prometheus", "Prometheus"),
    ("otel",       "OTel / Loki"),
    ("gitops",     "GitOps drift"),
    ("anchor",     "Anchors"),
    ("index",      "FAISS index"),
    ("signals",    "PatchTST"),
]


def _render_rca():
    st.title("Root Cause Analysis")

    # Ingestion pipeline bar
    if st.session_state.status != "idle":
        st.subheader("Ingestion pipeline")
        cols  = st.columns(len(_STEPS))
        stats = st.session_state.ingestion_stats
        for col, (key, label) in zip(cols, _STEPS):
            d     = stats.get(key, {})
            lines = []
            col.metric(label, _badge(d))
            if key == "ingest" and not d.get("skipped"):
                if "entities" in d:
                    lines.append(f"{d['entities']} entities")
                if "helm_releases" in d:
                    lines.append(f"{d['helm_releases']} releases")
                if d.get("kube_version"):
                    lines.append(d["kube_version"])
            elif key == "metrics" and not d.get("skipped"):
                if "pods_annotated" in d:
                    lines.append(f"{d['pods_annotated']} pods")
            elif key == "prometheus" and not d.get("skipped"):
                if "alerts" in d:
                    lines.append(f"{d['alerts']} alerts")
            elif key == "otel" and not d.get("skipped"):
                if "traces" in d:
                    lines.append(f"{d['traces']} traces")
                if "logs" in d:
                    lines.append(f"{d['logs']} logs")
            elif key == "gitops" and not d.get("skipped"):
                if "drifts" in d:
                    lines.append(f"{d['drifts']} drifts ({d.get('critical',0)} crit)")
            elif key == "anchor" and not d.get("skipped"):
                if "total" in d:
                    lines.append(f"{d['total']} records")
                if "manifest" in d:
                    lines.append(f"manifest={d['manifest']} schema={d.get('schema',0)}")
            elif key == "index" and not d.get("skipped"):
                if "vectors" in d:
                    lines.append(f"{d['vectors']} vectors")
                if "doc_chunks" in d and d["doc_chunks"]:
                    lines.append(f"{d['doc_chunks']} doc chunks")
            elif key == "signals" and not d.get("skipped"):
                if "total" in d:
                    lines.append(f"{d.get('anomalous',0)}/{d['total']} anomalous")
                if "mode" in d:
                    lines.append(f"mode={d['mode']}")
            if d.get("error"):
                col.caption(f"⚠ {d['error'][:55]}")
            elif lines:
                col.caption(" · ".join(lines))
        st.divider()

    # Status-based content
    status = st.session_state.status

    if status == "idle":
        st.info("Configure the run in the sidebar and click **▶ Run**.")
        return

    if status == "error":
        st.error("Pipeline error")
        for msg in st.session_state.run_log:
            st.code(msg)
        return

    if status not in ("interrupted", "done"):
        return

    payload = st.session_state.interrupt_payload
    final   = st.session_state.final_state
    report  = payload if status == "interrupted" else final.get("report_dict", payload)

    # Context banner
    ctx = st.session_state.analysis_context
    if ctx:
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Context",   ctx.get("kube_context", "?"))
        c2.metric("Namespace", ctx.get("namespace", "?"))
        c3.metric("Entities",  ctx.get("entities", "?"))
        c4.metric("K8s",       ctx.get("kube_version", "?"))
        c5.metric("Elapsed",   f"{st.session_state.elapsed:.1f}s")
        st.divider()

    # Example match banner
    if payload.get("example_match"):
        ex_id = payload.get("matched_example_id", "")
        st.success(
            f"**Known incident matched** — similar past resolution found "
            f"(id: `{ex_id}`). Analysis loop skipped, fix loaded directly.",
            icon="⚡",
        )

    # Helm drift table
    drift_items = st.session_state.drift_items
    if drift_items:
        import pandas as pd
        crit  = sum(1 for d in drift_items if d.severity == "critical")
        label = f"Helm drift — {len(drift_items)} item(s)  ({crit} critical)"
        with st.expander(label, expanded=bool(crit)):
            rows = [
                {"severity": d.severity, "field": d.field_path,
                 "declared": str(d.declared), "observed": str(d.observed)}
                for d in sorted(drift_items, key=lambda x: 0 if x.severity == "critical" else 1)
            ]
            df = pd.DataFrame(rows)
            st.dataframe(
                df.style.apply(
                    lambda col: [
                        "background-color:#ffcccc" if v == "critical" else
                        "background-color:#fff3cd" for v in col
                    ] if col.name == "severity" else [""] * len(col), axis=0,
                ),
                use_container_width=True, hide_index=True,
            )
        st.divider()

    # Reasoning journey (always shown)
    history        = report.get("reasoning_history") or []
    paths_explored = report.get("paths_explored", 1)
    confidence     = report.get("confidence", "")

    st.subheader(f"Reasoning journey — {paths_explored} path(s) explored")
    for entry in history:
        conf  = (entry.get("confidence") or "").upper()
        hyp   = entry.get("hypothesis") or "generic query"
        with st.expander(
            f"Path {entry['step']}  ·  {hyp[:90]}  —  {_conf_icon(conf)} {conf}  ✗ exhausted",
            expanded=False,
        ):
            st.write(entry.get("summary") or "_No summary_")
            st.caption(f"retries on this path: {entry.get('retry_count', 0)}")

    cur_hyp  = report.get("current_hypothesis") or "generic query (no hypothesis extracted)"
    cur_conf = confidence
    cur_icon = _conf_icon(cur_conf)
    with st.expander(
        f"Path {paths_explored}  ·  {cur_hyp[:90]}  —  {cur_icon} {cur_conf}  ✓ selected",
        expanded=True,
    ):
        st.write(report.get("summary") or "_No summary_")
    st.divider()

    # Root cause + remediation
    h_col, c_col = st.columns([3, 1])
    h_col.subheader("Root cause")
    if confidence:
        c_col.metric("Confidence", f"{cur_icon} {cur_conf}")
    with st.expander("Causal chain", expanded=True):
        st.write(report.get("root_cause") or "_Not identified_")
    remediation = report.get("remediation") or []
    with st.expander(f"Remediation — {len(remediation)} command(s)", expanded=bool(remediation)):
        for cmd in remediation:
            st.code(cmd, language="bash")

    # ── Dry-run output ────────────────────────────────────────────────────────
    # Always read from interrupt_payload so results are available in both
    # "interrupted" and "done" states.
    dry_runs = st.session_state.interrupt_payload.get("dry_run_results") or []
    if dry_runs:
        st.divider()
        ok  = sum(1 for d in dry_runs if d["exit_code"] == 0)
        err = len(dry_runs) - ok
        st.subheader(
            f"Dry run — {len(dry_runs)} command(s) "
            + (f"✅ {ok} ok" if ok else "")
            + (f"  ⚠ {err} error(s)" if err else "")
        )
        st.caption("Each command was executed in dry-run mode against the cluster. Review before approving.")
        for item in dry_runs:
            icon = "✅" if item["exit_code"] == 0 else "⚠️"
            orig = item["original_cmd"]
            dry  = item["dry_cmd"]
            out  = item["output"] or "_no output_"
            lang = "diff" if ("diff upgrade" in dry or out.startswith("helm values diff")) else "yaml"
            changed = orig != dry
            with st.expander(
                f"{icon}  `{orig[:90]}`",
                expanded=(item["exit_code"] != 0 or lang == "diff"),
            ):
                if changed:
                    st.caption(f"Executed as: `{dry}`")
                st.code(out, language=lang)

    # Human review gate
    no_solution = payload.get("no_solution", False)

    if status == "interrupted":
        st.divider()
        if no_solution:
            # ── No actionable solution ────────────────────────────────────────
            st.subheader("No solution found")
            paths = payload.get("paths_explored", 1)
            conf  = confidence or "LOW"
            st.warning(
                f"All {paths} path(s) explored — confidence **{conf}** "
                f"with no actionable remediation commands. "
                f"Manual investigation required.",
                icon="⚠️",
            )
            st.markdown(
                "**Suggested next steps**\n"
                "- Review the reasoning journey above for partial findings\n"
                "- Check cluster events: `kubectl get events -n <ns> --sort-by='.lastTimestamp'`\n"
                "- Consult runbooks in the Enterprise Docs tab\n"
                "- Escalate to on-call if SLA is at risk"
            )
            if st.button("Acknowledge & close", use_container_width=True):
                with st.spinner("Closing…"):
                    _build_workflow().invoke(
                        Command(resume="reject"), config=st.session_state.wf_config,
                    )
                st.session_state.interrupt_payload = {**payload, "human_decision": "no_solution"}
                st.session_state.status = "done"
                st.rerun()
        else:
            # ── Normal approve / reject gate ──────────────────────────────────
            st.subheader("Human review")
            if (confidence or "").upper() == "LOW":
                st.warning(
                    f"Confidence **{confidence}** — solution found but certainty is low. "
                    f"Review carefully before approving.",
                    icon="⚠️",
                )
            else:
                st.info(f"Confidence **{confidence}** — review dry-run output above, then approve or reject.")
            b1, b2, _ = st.columns([1, 1, 4])
            approve = b1.button("✅ Approve", type="primary", use_container_width=True)
            reject  = b2.button("❌ Reject",              use_container_width=True)
            if approve or reject:
                decision = "approve" if approve else "reject"
                with st.spinner("Resuming workflow…"):
                    final_state = _build_workflow().invoke(
                        Command(resume=decision), config=st.session_state.wf_config,
                    )
                st.session_state.final_state = final_state
                st.session_state.interrupt_payload = {**payload, "human_decision": decision}
                st.session_state.status = "done"
                st.rerun()
    else:
        decision = (
            final.get("human_decision")
            or st.session_state.interrupt_payload.get("human_decision", "")
        )
        if decision == "approve":
            st.success("✅ Approved — remediation applied.")
        elif decision == "no_solution":
            st.warning("⚠️ No solution found — acknowledged. Manual action required.")
        else:
            st.warning("❌ Rejected — no changes applied.")
        if st.sidebar.button("▶ Run again", use_container_width=True):
            st.session_state.status = "idle"
            st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
# Knowledge Base tab
# ─────────────────────────────────────────────────────────────────────────────

def _k8s_versioned_base(kube_version: str) -> str:
    """Return the versioned docs base URL, e.g. https://v1-31.docs.kubernetes.io"""
    import re as _re
    m = _re.match(r"v?(\d+)\.(\d+)", kube_version or "")
    if m:
        return f"https://v{m.group(1)}-{m.group(2)}.docs.kubernetes.io"
    return "https://kubernetes.io"


def _fetch_k8s_page(url: str) -> str:
    """Fetch a K8s docs page and return clean plain text (stdlib only)."""
    import urllib.request
    from html.parser import HTMLParser

    class _Extractor(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self._buf: list[str] = []
            self._skip = 0

        def handle_starttag(self, tag: str, attrs) -> None:
            if tag in ("script", "style", "nav", "footer", "header", "aside"):
                self._skip += 1

        def handle_endtag(self, tag: str) -> None:
            if tag in ("script", "style", "nav", "footer", "header", "aside"):
                self._skip = max(0, self._skip - 1)

        def handle_data(self, data: str) -> None:
            if not self._skip:
                t = data.strip()
                if t:
                    self._buf.append(t)

        @property
        def text(self) -> str:
            return "\n".join(self._buf)

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "KubeWhisperer/1.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        p = _Extractor()
        p.feed(html)
        return p.text
    except Exception as exc:
        return f"[fetch error: {exc}]"


def _fetch_enterprise_url(url: str, token: str | None = None) -> str:
    """
    Fetch an enterprise doc page (wiki, Confluence, GitHub raw, etc.).
    Supports Bearer token auth. Falls back to HTML text extraction.
    """
    import urllib.request
    from html.parser import HTMLParser

    class _Extractor(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self._buf: list[str] = []
            self._skip = 0

        def handle_starttag(self, tag: str, attrs) -> None:
            if tag in ("script", "style", "nav", "footer", "header", "aside"):
                self._skip += 1

        def handle_endtag(self, tag: str) -> None:
            if tag in ("script", "style", "nav", "footer", "header", "aside"):
                self._skip = max(0, self._skip - 1)

        def handle_data(self, data: str) -> None:
            if not self._skip:
                t = data.strip()
                if t:
                    self._buf.append(t)

        @property
        def text(self) -> str:
            return "\n".join(self._buf)

    try:
        headers: dict[str, str] = {"User-Agent": "KubeWhisperer/1.0"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        # Confluence REST API detection: translate page URL → REST API JSON
        import re as _re
        confluence_match = _re.search(r"/wiki/spaces/([^/]+)/pages/(\d+)", url)
        if confluence_match:
            page_id = confluence_match.group(2)
            base = url.split("/wiki/")[0]
            api_url = f"{base}/wiki/rest/api/content/{page_id}?expand=body.storage,title"
            req = urllib.request.Request(api_url, headers={**headers, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                import json
                data  = json.loads(resp.read())
                title = data.get("title", "")
                body  = data.get("body", {}).get("storage", {}).get("value", "")
                # Strip HTML from Confluence storage format
                p = _Extractor()
                p.feed(body)
                return f"# {title}\n\n{p.text}"

        # Generic HTML fetch
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as resp:
            ct = resp.headers.get("Content-Type", "")
            raw = resp.read()

        if "json" in ct:
            import json
            return json.dumps(json.loads(raw), indent=2, ensure_ascii=False)

        if "text/plain" in ct or url.endswith((".md", ".txt", ".rst")):
            return raw.decode("utf-8", errors="replace")

        p = _Extractor()
        p.feed(raw.decode("utf-8", errors="replace"))
        return p.text or "[no text content extracted]"

    except Exception as exc:
        return f"[fetch error: {exc}]"


# Pages to crawl — (title, path) — path appended to versioned base URL
_K8S_CRAWL_PAGES = [
    ("Troubleshooting pods",               "/docs/tasks/debug/debug-application/debug-pods/"),
    ("Troubleshooting services",           "/docs/tasks/debug/debug-application/debug-service/"),
    ("Pod lifecycle",                      "/docs/concepts/workloads/pods/pod-lifecycle/"),
    ("Container resource management",      "/docs/concepts/configuration/manage-resources-containers/"),
    ("PersistentVolumes",                  "/docs/concepts/storage/persistent-volumes/"),
    ("Container images & ImagePullBackOff","/docs/concepts/containers/images/"),
    ("Init containers",                    "/docs/concepts/workloads/pods/init-containers/"),
    ("ConfigMaps",                         "/docs/concepts/configuration/configmap/"),
    ("Secrets",                            "/docs/concepts/configuration/secret/"),
    ("Services",                           "/docs/concepts/services-networking/service/"),
    ("Deployments",                        "/docs/concepts/workloads/controllers/deployment/"),
    ("StatefulSets",                       "/docs/concepts/workloads/controllers/statefulset/"),
    ("HPA",                                "/docs/tasks/run-application/horizontal-pod-autoscale/"),
    ("Node conditions & taints",           "/docs/concepts/scheduling-eviction/taint-and-toleration/"),
    ("Resource quotas",                    "/docs/concepts/policy/resource-quotas/"),
    ("Events & kubectl describe",          "/docs/reference/kubectl/generated/kubectl_describe/"),
]


_K8S_DOCS = [
    ("Workloads",  [
        ("Pods",            "/docs/concepts/workloads/pods/"),
        ("Deployments",     "/docs/concepts/workloads/controllers/deployment/"),
        ("StatefulSets",    "/docs/concepts/workloads/controllers/statefulset/"),
        ("DaemonSets",      "/docs/concepts/workloads/controllers/daemonset/"),
        ("Jobs / CronJobs", "/docs/concepts/workloads/controllers/job/"),
    ]),
    ("Storage",  [
        ("Volumes",                 "/docs/concepts/storage/volumes/"),
        ("PersistentVolumes",       "/docs/concepts/storage/persistent-volumes/"),
        ("StorageClasses",          "/docs/concepts/storage/storage-classes/"),
        ("ConfigMaps",              "/docs/concepts/configuration/configmap/"),
        ("Secrets",                 "/docs/concepts/configuration/secret/"),
    ]),
    ("Networking",  [
        ("Services",        "/docs/concepts/services-networking/service/"),
        ("Ingress",         "/docs/concepts/services-networking/ingress/"),
        ("Network Policies","/docs/concepts/services-networking/network-policies/"),
        ("DNS",             "/docs/concepts/services-networking/dns-pod-service/"),
    ]),
    ("Operations",  [
        ("Resource limits",            "/docs/concepts/configuration/manage-resources-containers/"),
        ("Liveness / readiness probes","/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/"),
        ("Horizontal Pod Autoscaler",  "/docs/tasks/run-application/horizontal-pod-autoscale/"),
        ("kubectl cheatsheet",         "/docs/reference/kubectl/cheatsheet/"),
        ("Troubleshooting",            "/docs/tasks/debug/"),
    ]),
    # Helm links are external — keep absolute
    ("Helm",  [
        ("Helm docs",        "https://helm.sh/docs/"),
        ("Chart template",   "https://helm.sh/docs/chart_template_guide/"),
        ("Values files",     "https://helm.sh/docs/chart_template_guide/values_files/"),
        ("helm diff plugin", "https://github.com/databus23/helm-diff"),
    ]),
]


def _render_kb():
    st.title("Knowledge Base")
    onto_tab, anchor_tab, k8s_tab, ent_tab = st.tabs([
        "🗂 Ontology", "⚓ Anchors", "📖 Kubernetes Docs", "🏢 Enterprise Docs",
    ])

    # ── Ontology ──────────────────────────────────────────────────────────────
    with onto_tab:
        entities = st.session_state.kb_entities
        if not entities:
            st.info("Run an analysis first to populate the ontology view.")
        else:
            import pandas as pd
            df = pd.DataFrame(entities)
            kinds = ["(all)"] + sorted(df["kind"].unique().tolist())
            nss   = ["(all)"] + sorted(df["namespace"].unique().tolist())
            f1, f2, f3 = st.columns([2, 2, 3])
            sel_kind = f1.selectbox("Kind", kinds)
            sel_ns   = f2.selectbox("Namespace", nss)
            search   = f3.text_input("Search name")
            mask = pd.Series([True] * len(df))
            if sel_kind != "(all)":
                mask &= df["kind"] == sel_kind
            if sel_ns != "(all)":
                mask &= df["namespace"] == sel_ns
            if search:
                mask &= df["name"].str.contains(search, case=False, na=False)
            filtered = df[mask].drop(columns=["uid"])
            st.caption(f"{mask.sum()} / {len(df)} entities")
            st.dataframe(filtered, use_container_width=True, hide_index=True)

    # ── Anchors ───────────────────────────────────────────────────────────────
    with anchor_tab:
        fixes   = st.session_state.kb_anchor_fixes
        anchors = st.session_state.kb_anchors

        if not anchors:
            st.info("Run an analysis first to populate anchors.")
        else:
            # Fix suggestions panel (only when manifest anchors exist on unhealthy pods)
            if fixes:
                st.subheader(f"Fix suggestions — {len(fixes)} helm command(s)")
                st.caption(
                    "Derived from manifest anchors on unhealthy entities. "
                    "Each line restores the chart-declared value."
                )
                for hint in fixes:
                    # Split at → for nicer display
                    parts = hint.split("  →  ", 1)
                    if len(parts) == 2:
                        st.markdown(f"**{parts[0].strip()}**")
                        st.code(parts[1].strip(), language="bash")
                    else:
                        st.code(hint, language="bash")
                st.divider()

            st.subheader("All anchor records")
            import pandas as pd
            df = pd.DataFrame(anchors)
            sources = ["(all)"] + sorted(df["source"].unique().tolist())
            f1, f2 = st.columns([2, 3])
            sel_src = f1.selectbox("Source", sources, key="anc_src")
            search  = f2.text_input("Search field / entity", key="anc_search")
            mask = pd.Series([True] * len(df))
            if sel_src != "(all)":
                mask &= df["source"] == sel_src
            if search:
                mask &= (
                df["field"].str.contains(search, case=False, na=False) |
                df["entity"].str.contains(search, case=False, na=False)
            )
            st.caption(f"{mask.sum()} / {len(df)} anchor records")
            st.dataframe(df[mask], use_container_width=True, hide_index=True)

    # ── Kubernetes Docs ───────────────────────────────────────────────────────
    with k8s_tab:
        import re as _re

        kube_ver     = (st.session_state.analysis_context or {}).get("kube_version", "")
        ver_base     = _k8s_versioned_base(kube_ver)
        ver_match    = _re.match(r"v?(\d+)\.(\d+)", kube_ver or "")
        ver_label    = f"v{ver_match.group(1)}.{ver_match.group(2)}" if ver_match else "latest"
        is_versioned = ver_match is not None

        st.subheader("Official Kubernetes documentation")
        if is_versioned:
            st.info(
                f"Links point to **{ver_label}** docs (detected from cluster). "
                f"[Open versioned docs site]({ver_base}/)"
            )
        else:
            st.caption("Run an analysis to get version-specific links.")

        for category, links in _K8S_DOCS:
            with st.expander(category, expanded=False):
                for title, path in links:
                    if path.startswith("http"):
                        full = path  # already absolute (e.g. helm.sh)
                    elif is_versioned:
                        full = ver_base + path
                    else:
                        full = "https://kubernetes.io" + path
                    st.markdown(f"- [{title}]({full})")

        st.divider()
        st.subheader("Fetch & index K8s documentation")
        st.caption(
            "Fetches the actual content of selected pages into the knowledge base "
            "so the LLM can retrieve it during analysis."
        )

        page_titles  = [t for t, _ in _K8S_CRAWL_PAGES]
        selected     = st.multiselect(
            "Pages to fetch", page_titles, default=page_titles[:6],
            key="k8s_crawl_sel",
        )
        crawl_ver_only = st.checkbox(
            f"Use {ver_label} versioned URLs", value=is_versioned, key="k8s_crawl_ver",
            disabled=not is_versioned,
        )

        if st.button("⬇ Fetch & Index selected pages", type="primary", key="k8s_crawl_btn"):
            from knowledge import DocStore, EnterpriseDoc, DocIndexer
            ds   = DocStore()
            base = ver_base if crawl_ver_only else "https://kubernetes.io"
            tags = ["k8s-docs", f"k8s-{ver_label}"] if is_versioned else ["k8s-docs"]

            # Remove previously fetched docs for same version to avoid duplicates
            existing = [d for d in ds.list() if "k8s-docs" in d.tags and f"k8s-{ver_label}" in d.tags]
            for d in existing:
                ds.delete(d.id)

            total_chunks = 0
            progress = st.progress(0, text="Starting…")
            page_map = {t: p for t, p in _K8S_CRAWL_PAGES}

            for i, title in enumerate(selected):
                path    = page_map[title]
                url     = base + path
                progress.progress((i) / len(selected), text=f"Fetching {title}…")
                content = _fetch_k8s_page(url)
                if content.startswith("[fetch error"):
                    st.warning(f"{title}: {content}")
                    continue
                doc = ds.save(EnterpriseDoc(
                    title=f"K8s {ver_label} — {title}",
                    content=content,
                    tags=tags,
                    source="url",
                    url=url,
                ))
                wf_cfg = st.session_state.wf_config
                store  = (wf_cfg.get("configurable") or {}).get("store")
                if store:
                    total_chunks += DocIndexer(store).index_doc(doc)

            progress.progress(1.0, text="Done.")
            msg = f"Indexed {len(selected)} pages"
            if total_chunks:
                msg += f" → {total_chunks} chunks added to current FAISS store"
            else:
                msg += " (will be indexed on next run)"
            st.success(msg)
            st.rerun()

        # Show already-indexed K8s doc pages
        from knowledge import DocStore as _DS
        k8s_docs_stored = [d for d in _DS().list() if "k8s-docs" in d.tags]
        if k8s_docs_stored:
            st.divider()
            st.caption(f"{len(k8s_docs_stored)} K8s doc pages in knowledge base")
            for d in k8s_docs_stored:
                tag_str = "  ".join(f"`{t}`" for t in d.tags)
                with st.expander(f"{d.title}  ·  {tag_str}", expanded=False):
                    st.caption(f"URL: {d.url}  ·  {len(d.content):,} chars  ·  {d.created_at[:10]}")
                    st.text(d.content[:600] + ("…" if len(d.content) > 600 else ""))

        st.divider()
        st.subheader("Add a custom reference")
        with st.form("add_k8s_ref", clear_on_submit=True):
            ref_title = st.text_input("Title")
            ref_url   = st.text_input("URL")
            ref_note  = st.text_area("Notes (optional)", height=80)
            if st.form_submit_button("Save reference"):
                if ref_title and ref_url:
                    from knowledge import DocStore, EnterpriseDoc
                    DocStore().save(EnterpriseDoc(
                        title=ref_title,
                        content=f"URL: {ref_url}\n\n{ref_note}",
                        tags=["k8s-ref"],
                        source="url",
                        url=ref_url,
                    ))
                    st.success(f"Saved '{ref_title}' — will be indexed on next run.")
                else:
                    st.warning("Title and URL are required.")

    # ── Enterprise Docs ───────────────────────────────────────────────────────
    with ent_tab:
        from knowledge import DocStore, EnterpriseDoc, DocIndexer

        doc_store = DocStore()

        st.subheader("Add enterprise document")

        input_mode = st.radio(
            "Input mode",
            ["✏️ Manual text", "📂 Upload file", "🔗 Fetch from URL"],
            horizontal=True,
            label_visibility="collapsed",
            key="ent_input_mode",
        )

        _PRESET_TAGS = ["runbook", "sop", "architecture", "post-mortem",
                        "api-doc", "oncall", "security", "infra"]

        with st.form("add_doc", clear_on_submit=True):
            title   = st.text_input("Title *")
            preset  = st.multiselect("Category tags", _PRESET_TAGS, key="ent_preset_tags")
            extra   = st.text_input("Additional tags (comma-separated)", placeholder="payment-service, prod")

            content  = ""
            uploaded = None
            fetch_url = ""

            if input_mode == "✏️ Manual text":
                content = st.text_area(
                    "Content *", height=200,
                    placeholder="Paste runbook, SOP, architecture notes, post-mortem…"
                )

            elif input_mode == "📂 Upload file":
                uploaded = st.file_uploader("File (.txt, .md, .pdf)", type=["txt", "md", "pdf"])

            else:  # Fetch from URL
                fetch_url = st.text_input(
                    "URL *",
                    placeholder="https://wiki.internal/runbooks/payment-service  or  Confluence page URL"
                )
                st.text_input(
                    "Auth token (optional)",
                    type="password",
                    placeholder="Bearer token / API key — leave empty for public URLs",
                    key="ent_fetch_token",
                )
                st.caption(
                    "Supports any HTTP/HTTPS page (internal wikis, Confluence, Notion, GitHub raw). "
                    "For Confluence: paste the page URL and provide a Personal Access Token."
                )

            submitted = st.form_submit_button("💾 Save & index", type="primary")
            if submitted:
                tags = preset + [t.strip() for t in extra.split(",") if t.strip()]

                # ── Resolve content ──────────────────────────────────────────
                if input_mode == "📂 Upload file":
                    if uploaded is None:
                        st.error("No file selected.")
                        submitted = False
                    else:
                        raw = uploaded.read()
                        if uploaded.name.endswith(".pdf"):
                            try:
                                import io
                                import pypdf
                                reader  = pypdf.PdfReader(io.BytesIO(raw))
                                content = "\n\n".join(p.extract_text() or "" for p in reader.pages)
                            except ImportError:
                                try:
                                    import io
                                    import PyPDF2
                                    reader  = PyPDF2.PdfReader(io.BytesIO(raw))
                                    content = "\n\n".join(p.extract_text() or "" for p in reader.pages)
                                except ImportError:
                                    st.error("PDF support requires `pypdf`. Run: pip install pypdf")
                                    content = ""
                        else:
                            content = raw.decode("utf-8", errors="replace")
                        if not title:
                            title = uploaded.name

                elif input_mode == "🔗 Fetch from URL":
                    if not fetch_url:
                        st.error("URL is required.")
                        submitted = False
                    else:
                        token = st.session_state.get("ent_fetch_token", "")
                        with st.spinner(f"Fetching {fetch_url}…"):
                            content = _fetch_enterprise_url(fetch_url, token=token or None)
                        if content.startswith("[fetch error"):
                            st.error(content)
                            submitted = False
                        else:
                            if not title:
                                # Auto-title from URL path
                                from urllib.parse import urlparse
                                parts = [p for p in urlparse(fetch_url).path.split("/") if p]
                                title = parts[-1].replace("-", " ").replace("_", " ").title() if parts else fetch_url

                if submitted and content.strip() and title:
                    doc = doc_store.save(EnterpriseDoc(
                        title=title,
                        content=content,
                        tags=tags,
                        source="url" if input_mode == "🔗 Fetch from URL" else (
                            "upload" if input_mode == "📂 Upload file" else "manual"
                        ),
                        url=fetch_url if input_mode == "🔗 Fetch from URL" else "",
                    ))
                    wf_cfg = st.session_state.wf_config
                    store  = (wf_cfg.get("configurable") or {}).get("store")
                    if store:
                        n = DocIndexer(store).index_doc(doc)
                        st.success(f"Saved and indexed '{title}' ({n} chunks) into current analysis.")
                    else:
                        st.success(f"Saved '{title}' ({len(content):,} chars). Will be indexed on next run.")
                elif submitted:
                    if not title:
                        st.error("Title is required.")
                    elif not content.strip():
                        st.error("Content is empty.")

        # ── Document list ──────────────────────────────────────────────────────
        ent_docs = [d for d in doc_store.list() if "k8s-docs" not in d.tags and "k8s-ref" not in d.tags]
        if ent_docs:
            st.divider()
            # Tag filter
            all_tags = sorted({t for d in ent_docs for t in d.tags})
            sel_tags = st.multiselect("Filter by tag", all_tags, key="ent_tag_filter")
            shown = [d for d in ent_docs if not sel_tags or any(t in sel_tags for t in d.tags)]

            st.subheader(f"Enterprise documents — {len(shown)} / {len(ent_docs)}")
            for doc in shown:
                tag_badges = "  ".join(f"`{t}`" for t in doc.tags) if doc.tags else "_no tags_"
                src_icon   = {"manual": "✏️", "upload": "📂", "url": "🔗"}.get(doc.source, "📄")
                with st.expander(
                    f"{src_icon} **{doc.title}**  ·  {tag_badges}", expanded=False
                ):
                    st.text(doc.content[:800] + ("…" if len(doc.content) > 800 else ""))
                    col_info, col_re, col_del = st.columns([3, 1, 1])
                    col_info.caption(
                        f"id={doc.id}  ·  {doc.created_at[:10]}  ·  {len(doc.content):,} chars"
                        + (f"  ·  {doc.url}" if doc.url else "")
                    )
                    # Re-index button (useful after a new run loads a fresh store)
                    if col_re.button("⟳ Re-index", key=f"reindex_{doc.id}"):
                        wf_cfg = st.session_state.wf_config
                        store  = (wf_cfg.get("configurable") or {}).get("store")
                        if store:
                            n = DocIndexer(store).index_doc(doc)
                            st.success(f"{n} chunks indexed.")
                        else:
                            st.warning("No active analysis — run RCA first.")
                    if col_del.button("🗑 Delete", key=f"del_{doc.id}"):
                        doc_store.delete(doc.id)
                        st.rerun()
        else:
            st.info("No enterprise documents yet. Add runbooks, SOPs, or architecture notes above.")


# ─────────────────────────────────────────────────────────────────────────────
# Tab layout — entry point
# ─────────────────────────────────────────────────────────────────────────────

tab_rca, tab_kb = st.tabs(["🔍 Root Cause Analysis", "📚 Knowledge Base"])

with tab_rca:
    _render_rca()

with tab_kb:
    _render_kb()
