"""
KubeWhisperer — Streamlit UI

Run:
    streamlit run ui/app.py
"""
from __future__ import annotations

import os
import shlex
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

    # Retrieval pipeline details
    ret = (report.get("context_stats") or {}).get("retrieval") or {}
    if ret:
        with st.expander("Retrieval pipeline — BM25 + FAISS → RRF", expanded=False):
            r1, r2, r3, r4 = st.columns(4)
            r1.metric("Dense (FAISS)",  ret.get("dense",  "—"))
            r2.metric("Sparse (BM25)",  ret.get("sparse", "—"))
            r3.metric("Fused (RRF)",    ret.get("fused",  "—"))
            r4.metric("Top RRF score",  ret.get("top_rrf_score", "—"))
            st.caption(
                "Dense = FAISS cosine hits · Sparse = BM25 keyword hits · "
                "Fused = after Reciprocal Rank Fusion + source weights"
            )

    if ctx:
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
    onto_tab, anchor_tab, k8s_tab, ent_tab, helm_tab = st.tabs([
        "🗂 Ontology", "⚓ Anchors", "📖 Kubernetes Docs", "🏢 Enterprise Docs", "⎈ Helm / Helmfile",
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

    # ── Helm / Helmfile ────────────────────────────────────────────────────────
    with helm_tab:
        from knowledge import DocStore, EnterpriseDoc, DocIndexer

        st.subheader("Index Helm chart or Helmfile as enterprise knowledge")
        st.caption(
            "Upload `values.yaml`, `Chart.yaml`, `helmfile.yaml` or any Helm/Helmfile "
            "config. The content is indexed in FAISS so the RCA pipeline retrieves "
            "declared values alongside live cluster state."
        )

        _HELM_PRESETS = ["helm", "helmfile", "values", "chart", "enterprise", "infra"]

        with st.form("add_helm_doc", clear_on_submit=True):
            h_title   = st.text_input("Title *", placeholder="payment-service values.yaml — prod")
            h_release = st.text_input("Release / chart name", placeholder="payment-service")
            h_ns      = st.text_input("Namespace", placeholder="production")
            h_env     = st.text_input("Helmfile environment (optional)", placeholder="production")
            h_preset  = st.multiselect("Tags", _HELM_PRESETS, default=["helm", "enterprise"])
            h_extra   = st.text_input("Additional tags", placeholder="payment, critical")

            h_input = st.radio(
                "Input",
                ["📂 Upload file", "✏️ Paste YAML"],
                horizontal=True,
                label_visibility="collapsed",
                key="helm_input_mode",
            )

            h_content  = ""
            h_uploaded = None

            if h_input == "✏️ Paste YAML":
                h_content = st.text_area(
                    "YAML content *", height=240,
                    placeholder="# Paste values.yaml, Chart.yaml, or helmfile.yaml here",
                )
            else:
                h_uploaded = st.file_uploader(
                    "File (.yaml, .yml, .tgz)",
                    type=["yaml", "yml", "tgz"],
                    key="helm_upload",
                )
                st.caption("`.tgz` archives are read and their YAML files are concatenated.")

            h_submit = st.form_submit_button("💾 Index chart / helmfile", type="primary")

            if h_submit:
                if h_input == "📂 Upload file":
                    if h_uploaded is None:
                        st.error("No file selected.")
                        h_submit = False
                    else:
                        raw = h_uploaded.read()
                        fname = h_uploaded.name
                        if fname.endswith(".tgz"):
                            import tarfile
                            import io
                            try:
                                with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tf:
                                    parts = []
                                    for member in tf.getmembers():
                                        if member.name.endswith((".yaml", ".yml")):
                                            f = tf.extractfile(member)
                                            if f:
                                                parts.append(f"# --- {member.name} ---\n" + f.read().decode("utf-8", errors="replace"))
                                    h_content = "\n\n".join(parts)
                            except Exception as exc:
                                st.error(f"Could not read .tgz: {exc}")
                                h_submit = False
                        else:
                            h_content = raw.decode("utf-8", errors="replace")

                if h_submit:
                    if not h_title.strip():
                        st.error("Title is required.")
                    elif not h_content.strip():
                        st.error("No content to index.")
                    else:
                        tags = h_preset + [t.strip() for t in h_extra.split(",") if t.strip()]
                        meta_header = ""
                        if h_release:
                            meta_header += f"release: {h_release}\n"
                        if h_ns:
                            meta_header += f"namespace: {h_ns}\n"
                        if h_env:
                            meta_header += f"helmfile_environment: {h_env}\n"
                        full_content = (
                            f"# {h_title}\n{meta_header}\n{h_content}" if meta_header
                            else f"# {h_title}\n\n{h_content}"
                        )
                        ds   = DocStore()
                        doc  = ds.save(EnterpriseDoc(
                            title=h_title.strip(),
                            content=full_content,
                            tags=tags,
                            source="helm",
                        ))
                        try:
                            store = st.session_state.get("faiss_store")
                            if store:
                                DocIndexer(store).index_doc(doc)
                                st.success(f"Indexed `{h_title}` (id={doc.id}) — active in current session.", icon="✅")
                            else:
                                st.success(f"Saved `{h_title}` (id={doc.id}). Will be indexed on next analysis run.", icon="💾")
                        except Exception as exc:
                            st.warning(f"Saved but indexing failed: {exc}", icon="⚠️")

        # ── Saved Helm docs list ───────────────────────────────────────────────
        st.divider()
        st.subheader("Indexed Helm / Helmfile documents")
        helm_docs = [d for d in DocStore().list() if "helm" in d.tags or d.source == "helm"]
        if helm_docs:
            for doc in helm_docs:
                tag_str = "  ".join(f"`{t}`" for t in doc.tags)
                with st.expander(f"**{doc.title}** — {tag_str}", expanded=False):
                    st.caption(f"id={doc.id}  ·  created {doc.created_at[:10]}")
                    st.code(doc.content[:1200] + ("…" if len(doc.content) > 1200 else ""), language="yaml")
                    if st.button("🗑 Delete", key=f"del_helm_{doc.id}"):
                        DocStore().delete(doc.id)
                        st.rerun()
        else:
            st.info("No Helm / Helmfile documents yet. Upload a `values.yaml` or `helmfile.yaml` above.")


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard tab
# ─────────────────────────────────────────────────────────────────────────────

def _render_dashboard():
    import json
    import pandas as pd

    st.title("Pipeline Dashboard")

    CASES_ROOT = ROOT / "cases"

    # ── Section 1: Ingestion pipeline ─────────────────────────────────────────
    st.subheader("Ingestion pipeline — last run")
    stats = st.session_state.ingestion_stats

    if not stats:
        st.info("No analysis run yet. Click **▶ Run** in the sidebar to start.")
    else:
        rows = []
        for key, label in _STEPS:
            d     = stats.get(key, {})
            if d.get("skipped"):
                status = "⏭ skipped"
                detail = ""
            elif d.get("fallback") or d.get("error"):
                status = "⚠ fallback"
                detail = d.get("error", "")[:80]
            else:
                status = "✅ ok"
                detail_parts = []
                if key == "ingest":
                    if "entities" in d:
                        detail_parts.append(f"{d['entities']} entities")
                    if "helm_releases" in d:
                        detail_parts.append(f"{d['helm_releases']} releases")
                    if d.get("kube_version"):
                        detail_parts.append(d["kube_version"])
                elif key == "metrics":
                    if "pods_annotated" in d:
                        detail_parts.append(f"{d['pods_annotated']} pods")
                elif key == "prometheus":
                    if "alerts" in d:
                        detail_parts.append(f"{d['alerts']} alerts")
                elif key == "otel":
                    if "traces" in d:
                        detail_parts.append(f"{d['traces']} traces")
                    if "logs" in d:
                        detail_parts.append(f"{d['logs']} logs")
                elif key == "gitops":
                    if "drifts" in d:
                        detail_parts.append(f"{d['drifts']} drifts ({d.get('critical',0)} critical)")
                elif key == "anchor":
                    if "total" in d:
                        detail_parts.append(f"{d['total']} records (manifest={d.get('manifest',0)} schema={d.get('schema',0)})")
                elif key == "index":
                    if "vectors" in d:
                        detail_parts.append(f"{d['vectors']} vectors")
                    if "doc_chunks" in d and d["doc_chunks"]:
                        detail_parts.append(f"{d['doc_chunks']} doc chunks")
                    if "examples" in d and d["examples"]:
                        detail_parts.append(f"{d['examples']} examples")
                elif key == "signals":
                    if "total" in d:
                        detail_parts.append(f"{d.get('anomalous',0)}/{d['total']} anomalous  mode={d.get('mode','?')}")
                detail = "  ·  ".join(detail_parts)
            rows.append({"Step": label, "Status": status, "Detail": detail})

        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # ── Section 2: Knowledge base stats ───────────────────────────────────────
    st.divider()
    st.subheader("Knowledge base")

    from knowledge import DocStore as _DS
    all_docs = _DS().list()

    ent_docs = [d for d in all_docs if "k8s-docs" not in d.tags and "k8s-ref" not in d.tags]
    k8s_docs = [d for d in all_docs if "k8s-docs" in d.tags]
    ref_docs = [d for d in all_docs if "k8s-ref" in d.tags]

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Enterprise docs", len(ent_docs))
    m2.metric("K8s docs",        len(k8s_docs))
    m3.metric("References",      len(ref_docs))
    total_chars = sum(len(d.content) for d in all_docs)
    m4.metric("Total content", f"{total_chars:,} chars")

    if not all_docs:
        st.info(
            "Knowledge base is empty. Add documents in **📚 Knowledge Base → Enterprise Docs** "
            "or fetch K8s documentation in **📚 Knowledge Base → Kubernetes Docs**.",
            icon="📄",
        )

    if ent_docs:
        # Tag breakdown
        tag_counts: dict[str, int] = {}
        for d in ent_docs:
            for t in d.tags:
                tag_counts[t] = tag_counts.get(t, 0) + 1
        if tag_counts:
            df_tags = pd.DataFrame(
                [{"tag": t, "docs": c} for t, c in sorted(tag_counts.items(), key=lambda x: -x[1])]
            )
            st.caption("Enterprise docs by tag")
            st.bar_chart(df_tags.set_index("tag")["docs"])

    # Source weights config
    with st.expander("Source weights (config)", expanded=False):
        import config as _cfg
        rows_w = [
            {"doc_source": k, "weight": v, "env_var": f"SOURCE_WEIGHT_{k.upper()}"}
            for k, v in _cfg.SOURCE_WEIGHTS.items()
        ]
        st.dataframe(pd.DataFrame(rows_w), use_container_width=True, hide_index=True)
        st.caption(
            "Override any weight in `.env` or as environment variable. "
            "Higher weight → documents from this source rank higher in TF-IDF results."
        )

    # ── Section 3: Case bank ───────────────────────────────────────────────────
    st.divider()
    st.subheader("Case bank")

    case_dirs = sorted(CASES_ROOT.glob("0*/"))
    case_rows = []
    for cd in case_dirs:
        inp_path = cd / "input.json"
        exp_path = cd / "expect.json"
        if not inp_path.exists() or not exp_path.exists():
            continue
        try:
            inp = json.loads(inp_path.read_text())
            exp = json.loads(exp_path.read_text())
        except Exception:
            continue

        bd   = exp.get("_debug_score_breakdown", {})
        conf = exp.get("confidence", "")
        conf_icon = {"HIGH": "🟢", "MEDIUM": "🟡", "LOW": "🔴"}.get(conf, "⚪")
        case_rows.append({
            "case":              cd.name,
            "scenario":          inp.get("scenario", ""),
            "confidence":        f"{conf_icon} {conf}",
            "score_min":         exp.get("confidence_score_min", ""),
            "score_expected":    bd.get("total", ""),
            "anchors":           len(inp.get("anchors", [])),
            "events":            len(inp.get("events", [])),
            "has_drift":         bool((inp.get("helm_drift") or {}).get("diffs")),
            "has_policy":        bool(inp.get("policy_report")),
            "fallback_expected": exp.get("fallback_expected", False),
        })

    if case_rows:
        df_cases = pd.DataFrame(case_rows)
        st.caption(f"{len(df_cases)} cases in `cases/`")

        # Filter by confidence
        conf_filter = st.multiselect(
            "Filter by confidence",
            ["🟢 HIGH", "🟡 MEDIUM", "🔴 LOW"],
            default=[],
            key="dash_conf_filter",
        )
        if conf_filter:
            mask = df_cases["confidence"].apply(lambda c: any(f in c for f in conf_filter))
            df_cases = df_cases[mask]

        st.dataframe(df_cases, use_container_width=True, hide_index=True)

        # Score distribution chart
        scores = [r["score_expected"] for r in case_rows if isinstance(r["score_expected"], (int, float))]
        if scores:
            score_df = pd.DataFrame({"case": [r["case"] for r in case_rows if isinstance(r["score_expected"], (int, float))], "expected_score": scores})
            st.caption("Expected score distribution (from `_debug_score_breakdown`)")
            st.bar_chart(score_df.set_index("case")["expected_score"])

        # Run offline validation
        st.divider()
        with st.expander("▶ Run offline pipeline validation (slow — ~10 min)", expanded=False):
            st.caption(
                "Builds a synthetic OntologyGraph for each case, runs the full pipeline "
                "(BM25 + FAISS → RRF + BFS + Jaccard + TF-IDF), and compares the actual "
                "confidence score against `confidence_score_min` in each `expect.json`. No LLM required."
            )
            if st.button("Run case bank now", key="dash_run_cases"):
                from tests.cases.graph_factory import load_case, build_graph
                from vectorstore.embedder import Embedder
                from vectorstore.store import FAISSStore
                from rca.context_builder import ContextBuilder

                results = []
                prog = st.progress(0, text="Starting…")
                for i, cd in enumerate(case_dirs):
                    if not (cd / "input.json").exists():
                        continue
                    prog.progress(i / len(case_dirs), text=f"{cd.name}…")
                    try:
                        data  = load_case(cd)
                        graph = build_graph(data["input"])
                        store = FAISSStore(embedder=Embedder())
                        store.index_graph(graph)
                        ctx   = ContextBuilder(graph, store).build(data["input"]["query"])
                        exp   = data["expect"]
                        actual  = ctx.pre_llm_confidence.score
                        minimum = exp.get("confidence_score_min", 0)
                        passed  = actual >= minimum
                        rs = ctx.retrieval_stats
                        results.append({
                            "case":    cd.name,
                            "actual":  round(actual, 3),
                            "minimum": minimum,
                            "label":   ctx.pre_llm_confidence.label,
                            "expect":  exp.get("confidence", ""),
                            "dense":   rs.get("dense",  "—"),
                            "sparse":  rs.get("sparse", "—"),
                            "fused":   rs.get("fused",  "—"),
                            "pass":    "✅" if passed else "❌",
                        })
                    except Exception as exc:
                        results.append({
                            "case": cd.name, "actual": "error",
                            "minimum": "", "label": "", "expect": "", "pass": f"⚠ {exc}",
                        })

                prog.progress(1.0, text="Done.")
                if results:
                    df_res = pd.DataFrame(results)
                    ok  = sum(1 for r in results if r["pass"] == "✅")
                    err = len(results) - ok
                    st.metric("Passed", f"{ok} / {len(results)}", delta=f"-{err} failed" if err else None)
                    st.dataframe(df_res, use_container_width=True, hide_index=True)
    else:
        st.info(f"No cases found in `{CASES_ROOT}`.")


# ─────────────────────────────────────────────────────────────────────────────
# Tab 4 — Integration Tests (interactive dialogue)
# ─────────────────────────────────────────────────────────────────────────────

def _render_integration_tests():  # noqa: C901
    import json as _json

    from tests.cases.graph_factory import build_graph, load_case
    from tests.helm_cases.helm_case_factory import build_helm_graph, load_helm_case
    from tests.integration.cases.case_loader import (
        load_case as load_native_case,
        build_graph as build_native_graph,
        list_cases as list_native_cases,
    )
    from tests.integration.use_cases.dialogue_simulator import (
        DialogueSimulator, render_tree, write_json,
    )
    from tests.integration.use_cases.proposal_engine import generate_proposals
    from tools.case_contract import update_expect_from_sim, update_input_from_sim, recalibrate_all
    from rca.analyzer import RCAAnalyzer
    from llm.ollama_client import OllamaClient
    from vectorstore.embedder import Embedder
    from vectorstore.store import FAISSStore

    CASES_ROOT        = ROOT / "cases"
    HELM_CASES_ROOT   = ROOT / "cases" / "helm_cases"
    NATIVE_CASES_ROOT = ROOT / "tests" / "integration" / "cases"
    SIM_RESULTS       = ROOT / "tests" / "integration" / "use_cases" / "sim_results"

    # Discover cases — only h-series: synthetic cases that have a matching
    # tests/unit/test_hybrid_pipeline_NNN.py, shown as h001_*, h002_*, etc.
    _unit_dir = ROOT / "tests" / "unit"
    _h_nums   = {
        p.stem.replace("test_hybrid_pipeline_", "")
        for p in _unit_dir.glob("test_hybrid_pipeline_*.py")
    }  # e.g. {"001", "002"}
    synthetic_dirs = [
        d for d in sorted(CASES_ROOT.glob("0*/"))
        if d.name[:3] in _h_nums
    ]
    helm_dirs   = sorted(HELM_CASES_ROOT.glob("h*/")) if HELM_CASES_ROOT.is_dir() else []
    native_dirs = list_native_cases(NATIVE_CASES_ROOT) if NATIVE_CASES_ROOT.is_dir() else []
    all_cases_meta = (
        [("synthetic", d) for d in synthetic_dirs]
        + [("helm", d) for d in helm_dirs]
        + [("native", d) for d in native_dirs]
    )

    if not all_cases_meta:
        st.warning("No h-series cases found. Add tests/unit/test_hybrid_pipeline_NNN.py to register a case.")
        return

    st.title("Integration Tests — Dialogue Simulation")

    col_left, col_right = st.columns([1, 2], gap="large")

    # ── Left panel: selector + config ─────────────────────────────────────────
    with col_left:
        case_labels = [
            ("h" if ct == "synthetic" else "") + d.name
            for ct, d in all_cases_meta
        ]
        sel_idx   = st.selectbox(
            "Case",
            range(len(case_labels)),
            format_func=lambda i: case_labels[i],
            key="it_case_select",
        )
        case_type, case_dir = all_cases_meta[sel_idx]
        case_name = case_dir.name

        # Load case metadata
        try:
            if case_type == "synthetic":
                data   = load_case(case_dir)
                inp    = data["input"]
                expect = data["expect"]
                root_query = inp.get("query", "")
                scenario   = inp.get("scenario", case_name)
            elif case_type == "native":
                data   = load_native_case(case_dir)
                inp    = None
                expect = data["expect"]
                _ns    = expect.get("namespace", "default")
                _rel   = expect.get("release", case_name)
                root_query = (
                    f"Multiple failures detected in the '{_rel}' release "
                    f"(namespace {_ns}). Identify every root cause and provide "
                    f"remediation commands."
                )
                scenario = expect.get("scenario", case_name)
            else:
                data   = load_helm_case(case_dir)
                inp    = None
                expect = data["expect"]
                _ns    = expect.get("namespace", "default")
                _rel   = expect.get("release", case_name)
                root_query = (
                    f"Multiple failures detected in the '{_rel}' release "
                    f"(namespace {_ns}). Identify every root cause and provide "
                    f"remediation commands."
                )
                scenario = expect.get("scenario", case_name)
        except Exception as exc:
            st.error(f"Cannot load case: {exc}")
            return

        st.caption(f"**Scenario:** {scenario}")
        st.caption(f"**Query:** {root_query[:110]}{'…' if len(root_query) > 110 else ''}")
        st.caption(
            f"**Expected:** {_conf_icon(expect.get('confidence',''))} "
            f"{expect.get('confidence','?')}  "
            f"(min score {expect.get('confidence_score_min','?')})"
        )
        if expect.get("notes"):
            st.info(expect["notes"], icon="📌")

        st.divider()

        mode = st.radio(
            "Simulation mode",
            ["🔬 Pipeline trace", "Auto (full BFS)", "Manual (step-by-step)"],
            horizontal=True,
            key="it_mode",
        )
        is_auto = mode.startswith("Auto")

        is_pipeline = mode.startswith("🔬")
        if not is_pipeline:
            max_turns    = st.slider("Max turns",    1, 4, 2, key="it_turns")
            max_branches = st.slider("Max branches", 1, 4, 3, key="it_branches")
        else:
            max_turns, max_branches = 2, 3

        st.divider()

        client    = OllamaClient()
        ollama_ok = client.is_available() and client.model_is_pulled()
        if is_pipeline:
            st.success("Pipeline trace — no Ollama required", icon="🔬")
        elif not client.is_available():
            st.error("Ollama not reachable — run `ollama serve`", icon="🔴")
        elif not client.model_is_pulled():
            st.warning(f"Model `{client.model}` not pulled", icon="⚠️")
        else:
            st.success(f"Ollama: `{client.model}`", icon="🟢")

        run_label = "▶ Run trace" if is_pipeline else "▶ Run simulation"
        run_btn = st.button(
            run_label, type="primary",
            use_container_width=True, disabled=(not ollama_ok and not is_pipeline),
        )

        sim_path  = SIM_RESULTS / f"{case_name}.json"
        has_cache = sim_path.exists()
        load_cached = False
        if is_auto and has_cache:
            st.success("Cached result available", icon="✅")
            load_cached = st.button("📂 Load cached", use_container_width=True)

    # ── Session-state key scoped to the selected case ─────────────────────────
    state_key = f"it_dlg_{case_name}"

    def _reset_state():
        st.session_state[state_key] = {
            "mode":       "auto" if is_auto else ("pipeline" if is_pipeline else "manual"),
            "case_type":  case_type,
            "root_query": root_query,
            "status":     "idle",
            "payload":    None,   # auto mode: full sim JSON
            "turns":      [],     # manual mode: list of turn dicts
        }

    if state_key not in st.session_state:
        _reset_state()

    dlg = st.session_state[state_key]

    # Reset when case or mode changes
    cur_mode = "auto" if is_auto else ("pipeline" if is_pipeline else "manual")
    if dlg.get("root_query") != root_query or dlg.get("mode") != cur_mode:
        _reset_state()
        dlg = st.session_state[state_key]

    # ── Graph / analyzer builder (called from both modes) ─────────────────────
    def _build_analyzer() -> RCAAnalyzer:
        if case_type == "synthetic":
            graph = build_graph(inp)
        elif case_type == "native":
            graph = build_native_graph(data)
        else:
            graph = build_helm_graph(data)
        store = FAISSStore(embedder=Embedder())
        store.index_graph(graph)
        return RCAAnalyzer(graph=graph, store=store, llm=client)

    # ── Right panel ────────────────────────────────────────────────────────────
    with col_right:

        # ══════════════════════════════════════════════════════════════════════
        # AUTO MODE
        # ══════════════════════════════════════════════════════════════════════
        if is_auto:
            if load_cached and has_cache:
                dlg["payload"] = _json.loads(sim_path.read_text())
                dlg["status"]  = "done"

            if run_btn:
                _reset_state()
                dlg = st.session_state[state_key]
                tree_ph = st.empty()
                with st.status("Running BFS dialogue simulation…", expanded=True) as sw:
                    st.write(f"Building graph for `{case_name}`…")
                    analyzer = _build_analyzer()

                    def _on_node(root):
                        tree_ph.code(render_tree(root), language=None)

                    sim = DialogueSimulator(
                        analyzer=analyzer,
                        max_turns=max_turns,
                        max_branches=max_branches,
                        on_node=_on_node,
                    )
                    st.write("Expanding proposal tree…")
                    root = sim.run(root_query)
                    jpath = write_json(
                        root, case_name, root_query,
                        out_dir=SIM_RESULTS,
                        max_turns=max_turns,
                        max_branches=max_branches,
                    )
                    dlg["payload"] = _json.loads(jpath.read_text())
                    dlg["status"]  = "done"
                    sw.update(label="Simulation complete", state="complete")

            payload = dlg.get("payload")
            if payload:
                # ── Summary metrics ────────────────────────────────────────
                summary = payload.get("summary", {})
                c1, c2, c3, c4, c5 = st.columns(5)
                c1.metric("Nodes",      summary.get("total_nodes", "—"))
                c2.metric("Resolved",   summary.get("resolved",    "—"))
                c3.metric("Dead ends",  summary.get("dead_ends",   "—"))
                c4.metric("Root score", f"{summary.get('root_score', 0):.2f}")
                c5.metric("Best score", f"{summary.get('best_score', 0):.2f}")

                # ── ASCII tree ─────────────────────────────────────────────
                st.divider()

                def _ascii_lines(node: dict, prefix: str = "", is_last: bool = True) -> list[str]:
                    status = node.get("status", "pending")
                    icon   = {"resolved": "✓", "dead_end": "✗", "pending": "…"}.get(status, "?")
                    suffix = (
                        " resolved" if status == "resolved" else
                        f" dead_end ({node.get('dead_end_reason','')})" if status == "dead_end" else ""
                    )
                    prop = node.get("proposal")
                    if prop:
                        connector = "└── " if is_last else "├── "
                        head = f"[{prop['label']}] {prop['description']}"
                    else:
                        connector = prefix = ""
                        q    = node.get("query", "")
                        head = q[:70] + ("…" if len(q) > 70 else "")
                    score = node.get("score", 0)
                    label = node.get("label", "?")
                    lines = [f"{prefix}{connector}{head} → score={score:.2f}, {label} {icon}{suffix}"]
                    cpfx  = prefix + ("    " if is_last else "│   ")
                    for i, ch in enumerate(node.get("children", [])):
                        lines += _ascii_lines(ch, cpfx, i == len(node["children"]) - 1)
                    return lines

                st.code("\n".join(_ascii_lines(payload["tree"])), language=None)

                # ── Turn-by-turn breakdown ─────────────────────────────────
                st.divider()
                st.subheader("Turn breakdown")

                def _walk_nodes(node: dict, depth: int = 0) -> list[dict]:
                    flat = [{"depth": depth, **node}]
                    for ch in node.get("children", []):
                        flat += _walk_nodes(ch, depth + 1)
                    return flat

                all_nodes = _walk_nodes(payload["tree"])
                for ni, node in enumerate(all_nodes):
                    turn = node.get("turn", 0)
                    prop = node.get("proposal")
                    status = node.get("status", "pending")
                    icon   = {"resolved": "✓", "dead_end": "✗", "pending": "…"}.get(status, "?")
                    label_str = (
                        f"[Turn {turn}] {prop['label'] if prop else 'root'} — "
                        f"score={node.get('score', 0):.2f} {node.get('label','?')} {icon}"
                    )
                    if prop:
                        label_str += f"  ·  {prop['description'][:60]}"
                    with st.expander(label_str, expanded=(turn == 0)):
                        ret_n = node.get("retrieval") or {}
                        if ret_n:
                            ac1, ac2, ac3 = st.columns(3)
                            ac1.metric("FAISS dense", ret_n.get("dense",  "—"))
                            ac2.metric("BM25 sparse", ret_n.get("sparse", "—"))
                            ac3.metric("RRF fused",   ret_n.get("fused",  "—"))
                        if node.get("root_cause"):
                            st.markdown(f"**Root cause:** {node['root_cause']}")
                        if node.get("raw_analysis"):
                            st.text_area(
                                "LLM response (truncated)",
                                value=node["raw_analysis"][:1200],
                                height=150,
                                disabled=True,
                                key=f"auto_raw_{case_name}_{ni}",
                            )
                        cmds = node.get("remediation", [])
                        if cmds:
                            st.markdown("**Remediation commands:**")
                            for cmd in cmds:
                                st.code(cmd, language="bash")
                        if status == "dead_end":
                            st.warning(f"Dead end: {node.get('dead_end_reason', '')}", icon="✗")
                        elif status == "resolved":
                            st.success("Resolved", icon="✓")

                # ── Remediation panel (auto mode) ──────────────────────────
                all_cmds = list(dict.fromkeys(
                    cmd
                    for node in all_nodes
                    if node.get("status") != "dead_end"
                    for cmd in node.get("remediation", [])
                ))
                if all_cmds:
                    st.divider()
                    _render_remediation_panel(all_cmds, case_name)

                # ── Actions ────────────────────────────────────────────────
                st.divider()
                ac1, ac2, ac3 = st.columns(3)
                with ac1:
                    st.download_button(
                        "📥 Download JSON",
                        data=_json.dumps(payload, indent=2, ensure_ascii=False),
                        file_name=f"{case_name}_sim.json",
                        mime="application/json",
                        use_container_width=True,
                    )
                with ac2:
                    if st.button("📋 Update expect.json", use_container_width=True, key="upd_expect"):
                        _, changes = update_expect_from_sim(case_dir, payload, dry_run=True)
                        if changes:
                            with st.expander("Changes preview", expanded=True):
                                for c in changes:
                                    st.code(c, language=None)
                            if st.button("✅ Apply to expect.json", key="apply_expect"):
                                update_expect_from_sim(case_dir, payload, dry_run=False)
                                st.success("expect.json updated.")
                        else:
                            st.info("expect.json already up to date.")
                with ac3:
                    if case_type == "synthetic":
                        if st.button("📋 Update input.json", use_container_width=True, key="upd_input"):
                            _, changes = update_input_from_sim(case_dir, payload, dry_run=True)
                            if changes:
                                with st.expander("Changes preview", expanded=True):
                                    for c in changes:
                                        st.code(c, language=None)
                                if st.button("✅ Apply to input.json", key="apply_input"):
                                    update_input_from_sim(case_dir, payload, dry_run=False)
                                    st.success("input.json updated.")
                            else:
                                st.info("No anchors/symptom to add.")

                st.divider()
                with st.expander("🔁 Recalibrate all cases from sim results"):
                    st.caption(
                        "Loops over every JSON in `sim_results/` and updates "
                        "`confidence_score_min` + `confidence` in the matching `expect.json`."
                    )
                    if st.button("Preview changes (dry run)", key="recal_dry"):
                        all_changes = recalibrate_all(SIM_RESULTS, CASES_ROOT, dry_run=True)
                        for cnm, ch_list in all_changes.items():
                            if ch_list:
                                st.write(f"**{cnm}**")
                                for c in ch_list:
                                    st.code(c, language=None)
                        if not any(all_changes.values()):
                            st.info("All cases already calibrated.")
                    if st.button("Apply to all cases", type="primary", key="recal_apply"):
                        all_changes = recalibrate_all(SIM_RESULTS, CASES_ROOT, dry_run=False)
                        updated = sum(1 for v in all_changes.values() if v)
                        st.success(f"{updated} case(s) updated.")

            elif not run_btn:
                st.info(
                    "Select a case and click **▶ Run simulation** to expand the full BFS "
                    "dialogue tree. The ASCII tree builds live as each node completes.",
                    icon="🧪",
                )

        # ══════════════════════════════════════════════════════════════════════
        # PIPELINE TRACE MODE  (no LLM)
        # ══════════════════════════════════════════════════════════════════════
        elif is_pipeline:
            _render_pipeline_trace(run_btn, case_name, root_query, inp, case_type, data)

        # ══════════════════════════════════════════════════════════════════════
        # MANUAL MODE
        # ══════════════════════════════════════════════════════════════════════
        else:
            turns = dlg.setdefault("turns", [])

            # ── Start: run initial LLM call ────────────────────────────────
            if run_btn:
                _reset_state()
                dlg   = st.session_state[state_key]
                turns = dlg["turns"]
                with st.spinner("Running initial analysis…"):
                    analyzer = _build_analyzer()
                    report   = analyzer.analyze(root_query)
                    props    = generate_proposals(report, max_n=max_branches)
                    turns.append({
                        "turn":         0,
                        "query":        root_query,
                        "score":        report.context.pre_llm_confidence.score if report.context and report.context.pre_llm_confidence else 0.0,
                        "label":        report.context.pre_llm_confidence.label if report.context and report.context.pre_llm_confidence else "LOW",
                        "retrieval":    report.context.retrieval_stats if report.context else {},
                        "raw_analysis": (report.raw_analysis or "")[:1500],
                        "root_cause":   report.root_cause or "",
                        "remediation":  list(report.remediation or []),
                        "proposals":    [{"label": p.label, "category": p.category, "description": p.description, "query": p.follow_up_query} for p in props],
                        "status":       "pending",
                        "dead_end_reason": "",
                    })
                    dlg["status"] = "ready"

            if not turns:
                st.info(
                    "Click **▶ Run simulation** to begin step-by-step dialogue.\n\n"
                    "At each turn you choose which proposal to follow, see the full LLM "
                    "response, and decide how to proceed.",
                    icon="🧪",
                )
            else:
                # ── Render completed turns ─────────────────────────────────
                for t in turns:
                    turn_n = t["turn"]
                    icon   = {"resolved": "✓", "dead_end": "✗", "pending": "…"}.get(t["status"], "?")
                    conf_i = _conf_icon(t.get("label", ""))
                    header = (
                        f"Turn {turn_n} — score={t['score']:.2f} {conf_i} {t.get('label','?')} {icon}"
                        + (f"  ·  dead end ({t['dead_end_reason']})" if t["status"] == "dead_end" else "")
                        + (" ✓ resolved" if t["status"] == "resolved" else "")
                    )
                    with st.expander(header, expanded=(turn_n == len(turns) - 1)):
                        st.markdown(f"**Query:** _{t['query'][:200]}_")
                        ret_t = t.get("retrieval") or {}
                        if ret_t:
                            rc1, rc2, rc3 = st.columns(3)
                            rc1.metric("FAISS dense", ret_t.get("dense",  "—"))
                            rc2.metric("BM25 sparse", ret_t.get("sparse", "—"))
                            rc3.metric("RRF fused",   ret_t.get("fused",  "—"))
                        if t.get("root_cause"):
                            st.markdown(f"**Root cause:** {t['root_cause']}")
                        if t.get("raw_analysis"):
                            st.text_area(
                                "LLM response (truncated)",
                                value=t["raw_analysis"],
                                height=160,
                                disabled=True,
                                key=f"man_raw_{case_name}_{turn_n}",
                            )
                        cmds = t.get("remediation", [])
                        if cmds:
                            st.markdown("**Remediation commands:**")
                            for cmd in cmds:
                                st.code(cmd, language="bash")

                # ── Proposal chooser for last pending turn ─────────────────
                last = turns[-1]
                max_turns_reached = last["turn"] >= max_turns

                if last["status"] == "pending" and not max_turns_reached and last.get("proposals"):
                    st.divider()
                    st.subheader(f"Turn {last['turn'] + 1} — Choose a proposal")
                    proposals = last["proposals"]
                    prop_labels = [f"[{p['label']}] {p['description']}" for p in proposals]
                    chosen_idx = st.radio(
                        "Follow-up proposals",
                        range(len(prop_labels)),
                        format_func=lambda i: prop_labels[i],
                        key=f"man_prop_{case_name}_{last['turn']}",
                    )
                    chosen = proposals[chosen_idx]
                    st.caption(f"Query: _{chosen['query']}_")

                    if st.button(
                        f"▶ Continue with [{chosen['label']}]",
                        type="primary",
                        use_container_width=True,
                        key=f"man_continue_{case_name}_{last['turn']}",
                    ):
                        with st.spinner(f"Running turn {last['turn'] + 1}…"):
                            analyzer = _build_analyzer()
                            report   = analyzer.analyze(chosen["query"])
                            score    = report.context.pre_llm_confidence.score if report.context and report.context.pre_llm_confidence else 0.0
                            parent_score = last["score"]

                            # Determine status
                            if score >= 0.70 or (score - parent_score >= 0.10 and score >= 0.55):
                                new_status = "resolved"
                                ded_reason = ""
                            elif score < parent_score - 0.05 or abs(score - parent_score) < 0.03:
                                new_status = "dead_end"
                                ded_reason = (
                                    "confidence_regressed" if score < parent_score - 0.05
                                    else "confidence_stagnant"
                                )
                            else:
                                new_status = "pending"
                                ded_reason = ""

                            next_props = []
                            if new_status == "pending":
                                next_props = [
                                    {"label": p.label, "category": p.category,
                                     "description": p.description, "query": p.follow_up_query}
                                    for p in generate_proposals(report, max_n=max_branches)
                                ]

                            turns.append({
                                "turn":         last["turn"] + 1,
                                "query":        chosen["query"],
                                "score":        score,
                                "label":        report.context.pre_llm_confidence.label if report.context and report.context.pre_llm_confidence else "LOW",
                                "retrieval":    report.context.retrieval_stats if report.context else {},
                                "raw_analysis": (report.raw_analysis or "")[:1500],
                                "root_cause":   report.root_cause or "",
                                "remediation":  list(report.remediation or []),
                                "proposals":    next_props,
                                "status":       new_status,
                                "dead_end_reason": ded_reason,
                            })
                        st.rerun()

                elif max_turns_reached and last["status"] == "pending":
                    st.info(f"Max turns ({max_turns}) reached — simulation complete.", icon="🏁")

                # ── Remediation panel (manual mode) ───────────────────────
                all_cmds = list(dict.fromkeys(
                    cmd
                    for t in turns
                    if t.get("status") != "dead_end"
                    for cmd in t.get("remediation", [])
                ))
                if all_cmds:
                    st.divider()
                    _render_remediation_panel(all_cmds, case_name)


def _render_remediation_panel(cmds: list[str], case_name: str) -> None:
    """Remediation panel: checkboxes, kube context display, apply with confirmation."""
    st.subheader("Remediation panel")
    st.caption(
        "Review commands collected across all resolved turns. "
        "Check the ones you want to apply, confirm the kube context, then execute."
    )

    # Kube context info
    ctx = _current_context()
    if ctx:
        st.info(f"Current kube context: `{ctx}`", icon="⎈")
    else:
        st.warning("No active kube context detected.", icon="⚠️")

    # Checkboxes
    selected_cmds = []
    for i, cmd in enumerate(cmds):
        if st.checkbox(cmd, key=f"rem_cmd_{case_name}_{i}"):
            selected_cmds.append(cmd)

    if not selected_cmds:
        st.caption("Select at least one command above to enable execution.")
        return

    st.divider()
    confirmed = st.checkbox(
        f"I confirm the kube context `{ctx or 'unknown'}` is the correct target cluster.",
        key=f"rem_confirm_{case_name}",
    )

    if st.button(
        f"Apply {len(selected_cmds)} selected command(s)",
        type="primary",
        disabled=not confirmed,
        use_container_width=True,
        key=f"rem_apply_{case_name}",
    ):
        for cmd in selected_cmds:
            st.markdown(f"**Running:** `{cmd}`")
            try:
                args = shlex.split(cmd)
                result = subprocess.run(args, capture_output=True, text=True, timeout=120)
                if result.returncode == 0:
                    st.success("Exit 0")
                    if result.stdout:
                        st.code(result.stdout[:2000], language="yaml")
                else:
                    st.error(f"Exit {result.returncode}")
                    if result.stderr:
                        st.code(result.stderr[:1000], language=None)
            except Exception as exc:
                st.error(f"Failed: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# Step 10 helper — Proposed Remediation
# ─────────────────────────────────────────────────────────────────────────────

def _render_proposed_changes(ctx, case_name: str, case_type: str) -> None:
    """
    Step 10 — full deployment readiness remediation.

    Priority order (what blocks deployment first):
      1. Missing dependencies  (secrets, configmaps, RBAC, PVC, imagePullSecret)
      2. NetworkPolicy blockers
      3. OPA / Kyverno violations
      4. Helm drift (declared values ≠ deployed)
      5. Declared values.yaml (reference)
    """
    import re as _re
    import pandas as pd

    CASES_ROOT      = ROOT / "cases"
    HELM_CASES_ROOT = ROOT / "cases" / "helm_cases"
    NATIVE_ROOT     = ROOT / "tests" / "integration" / "cases"

    if case_type == "synthetic":
        case_dir = CASES_ROOT / case_name
    elif case_type == "native":
        case_dir = NATIVE_ROOT / case_name
    else:
        case_dir = HELM_CASES_ROOT / case_name
    values_file = case_dir / "helm" / "values.yaml"

    # Partition anchor_fixes by type
    missing_fixes   = [f for f in ctx.anchor_fixes if "missing" in f.split("→")[0].lower()]
    netpol_fixes    = [f for f in ctx.anchor_fixes if "networkpolicy" in f.lower() or "netpol" in f.lower()]
    helm_fixes      = [f for f in ctx.anchor_fixes
                       if f not in missing_fixes and f not in netpol_fixes]

    has_missing    = bool(missing_fixes)
    has_netpol     = bool(netpol_fixes)
    has_drift      = bool(ctx.drift)
    has_helm_fixes = bool(helm_fixes)
    has_violations = bool(ctx.policy_violations)
    has_values     = values_file.exists()

    nothing = not any([has_missing, has_netpol, has_drift, has_helm_fixes,
                       has_violations, has_values])

    # Label summarises the types of issues found
    issue_tags = []
    if has_missing:
        issue_tags.append("missing deps")
    if has_netpol:
        issue_tags.append("netpol")
    if has_violations:
        issue_tags.append("OPA/Kyverno")
    if has_drift:
        issue_tags.append("helm drift")
    label = f"Step 10 — Proposed Remediation ({' · '.join(issue_tags) if issue_tags else '—'})"

    with st.expander(label, expanded=True):
        if nothing:
            st.success("No missing dependencies, drift, or policy violations detected.", icon="✅")
            return

        # ── 1. MISSING DEPENDENCIES (highest priority) ────────────────────────
        if has_missing:
            st.markdown("#### 🔴 Missing deployment dependencies")
            st.caption(
                "These Kubernetes objects are referenced in the pod spec but do not exist. "
                "The application **cannot start** until they are created."
            )
            for fix in missing_fixes:
                parts = fix.split("  →  ", 1)
                label_part = parts[0].strip()
                cmd_part   = parts[1].strip() if len(parts) == 2 else fix
                # Categorise icon
                if "secret" in label_part.lower() and "docker" in cmd_part.lower():
                    icon = "🔑"
                elif "secret" in label_part.lower():
                    icon = "🔐"
                elif "configmap" in label_part.lower():
                    icon = "⚙️"
                elif "serviceaccount" in label_part.lower():
                    icon = "👤"
                elif "rbac" in label_part.lower() or "rolebinding" in cmd_part.lower():
                    icon = "🔒"
                elif "pvc" in label_part.lower():
                    icon = "💾"
                else:
                    icon = "❌"
                st.caption(f"{icon} {label_part}")
                st.code(cmd_part, language="bash")
            st.divider()

        # ── 2. NetworkPolicy blockers ─────────────────────────────────────────
        if has_netpol:
            st.markdown("#### 🌐 NetworkPolicy — traffic blocked")
            st.caption(
                "A NetworkPolicy is blocking required traffic. "
                "Edit it to add egress/ingress rules for the ports your application needs."
            )
            for fix in netpol_fixes:
                parts = fix.split("  →  ", 1)
                st.caption(parts[0].strip())
                cmd = parts[1].strip() if len(parts) == 2 else fix
                st.code(cmd, language="bash")
                # Show a template for the egress rule
                st.code(
                    "# Example: add egress rules for PostgreSQL + Redis + DNS\n"
                    "egress:\n"
                    "- ports:\n"
                    "  - port: 5432    # PostgreSQL\n"
                    "  - port: 6379    # Redis\n"
                    "  - port: 53      # DNS (UDP)\n"
                    "    protocol: UDP\n"
                    "  - port: 53      # DNS (TCP)\n"
                    "    protocol: TCP",
                    language="yaml",
                )
            st.divider()

        # ── 3. OPA / Kyverno violations ───────────────────────────────────────
        if has_violations:
            st.markdown("#### 🔒 OPA / Kyverno policy fixes")
            for v in ctx.policy_violations:
                m_pol  = _re.search(r"policy=(\S+)", v)
                m_rule = _re.search(r"rule=(\S+)", v)
                m_res  = _re.search(r"resource=(\S+)", v)
                m_sev  = _re.search(r"severity=(\S+)", v)
                m_src  = _re.search(r"source=(\S+)", v)
                m_msg  = _re.search(r"message='(.+)'$", v)
                policy = m_pol.group(1) if m_pol else "?"
                rule   = m_rule.group(1) if m_rule else "?"
                res    = m_res.group(1) if m_res else "?"
                sev    = m_sev.group(1) if m_sev else "low"
                src    = m_src.group(1) if m_src else "unknown"
                msg    = m_msg.group(1) if m_msg else ""
                icon   = {"critical":"🔴","high":"🟠","medium":"🟡","low":"🟢"}.get(sev,"⚪")
                st.markdown(f"{icon} **{policy}** / `{rule}` → `{res}`")
                if msg:
                    st.caption(msg[:300])
                if src == "kyverno":
                    p = policy.split("=")[-1] if "=" in policy else policy
                    st.code(f"kubectl describe clusterpolicy {p}\nkyverno test .", language="bash")
                else:
                    st.code(f"kubectl get constraint {policy} -o jsonpath='{{.status.violations}}'", language="bash")
                st.markdown("---")
            st.divider()

        # ── 4. Helm drift ─────────────────────────────────────────────────────
        if has_drift or has_helm_fixes:
            st.markdown("#### 🔀 Helm values drift — declared vs deployed")
            if has_drift:
                rows = []
                for d in ctx.drift:
                    m_decl  = _re.search(r"declared='?([^'\s|]+)'?", d)
                    m_obs   = _re.search(r"observed='?([^'\s|]+)'?", d)
                    m_field = _re.search(r"drift\.([\w.]+)", d)
                    rows.append({
                        "field":    m_field.group(1) if m_field else d.split(":")[0],
                        "declared": m_decl.group(1) if m_decl else "?",
                        "observed": m_obs.group(1)  if m_obs  else "?",
                        "action":   "🔄 restore",
                    })
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            if has_helm_fixes:
                for fix in helm_fixes:
                    parts = fix.split("  →  ", 1)
                    if len(parts) == 2:
                        st.caption(parts[0].strip())
                        st.code(parts[1].strip(), language="bash")
                    else:
                        st.code(fix, language="bash")
            st.divider()

        # ── 5. Declared values.yaml (reference) ───────────────────────────────
        if has_values:
            with st.expander("📄 Declared `values.yaml` (reference)", expanded=False):
                st.code(values_file.read_text(), language="yaml")

        # ── 4. OPA / Kyverno policy fixes ─────────────────────────────────────
        if has_violations:
            st.markdown("#### 🔒 OPA / Kyverno policy fixes")
            for v in ctx.policy_violations:
                # Parse key fields from to_text() output
                m_src    = _re.search(r"source=(\S+)",   v)
                m_pol    = _re.search(r"policy=(\S+)",   v)
                m_rule   = _re.search(r"rule=(\S+)",     v)
                m_res    = _re.search(r"resource=(\S+)", v)
                m_sev    = _re.search(r"severity=(\S+)", v)
                m_msg    = _re.search(r"message='(.+)'$", v)
                source   = m_src.group(1)  if m_src  else "unknown"
                policy   = m_pol.group(1)  if m_pol  else "?"
                rule     = m_rule.group(1) if m_rule else "?"
                resource = m_res.group(1)  if m_res  else "?"
                severity = m_sev.group(1)  if m_sev  else "low"
                message  = m_msg.group(1)  if m_msg  else ""

                sev_icon = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}.get(severity, "⚪")
                st.markdown(f"{sev_icon} **{policy}** / `{rule}` → `{resource}`")
                if message:
                    st.caption(message[:300])

                if source == "kyverno":
                    policy_name = policy.split("=")[-1] if "=" in policy else policy
                    st.code(
                        f"# Inspect the policy\n"
                        f"kubectl describe clusterpolicy {policy_name}\n\n"
                        f"# Test locally\n"
                        f"kyverno test .\n\n"
                        f"# Patch the violating resource\n"
                        f"kubectl annotate {resource.replace('/', ' -n ')} "
                        f"policies.kyverno.io/last-applied-patches-",
                        language="bash",
                    )
                elif source == "gatekeeper":
                    constraint = policy.split("=")[-1] if "=" in policy else policy
                    st.code(
                        f"# Inspect the constraint\n"
                        f"kubectl describe constraint {constraint}\n\n"
                        f"# List violations\n"
                        f"kubectl get constraint {constraint} -o jsonpath='{{.status.violations}}'",
                        language="bash",
                    )
                else:
                    ns = resource.split("/")[1] if resource.count("/") >= 1 else "default"
                    st.code(
                        f"kubectl get policyreport -n {ns} -o yaml\n"
                        f"kubectl describe policyreport -n {ns}",
                        language="bash",
                    )
                st.markdown("---")


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline Trace renderer  (no LLM — pre-LLM pipeline only)
# ─────────────────────────────────────────────────────────────────────────────

def _render_pipeline_trace(run_btn, case_name, root_query, inp, case_type, data):
    """Step-by-step trace of the hybrid retrieval + context-building pipeline."""
    from tests.cases.graph_factory import build_graph
    from tests.helm_cases.helm_case_factory import build_helm_graph
    from tests.integration.cases.case_loader import build_graph as build_native_graph
    from vectorstore.bm25_retriever import _tokenize
    from vectorstore.embedder import Embedder
    from vectorstore.store import FAISSStore
    from rca.context_builder import ContextBuilder
    from rca.remediation_engine import RemediationEngine
    from rca.analyzer import _build_prompt

    cache_key = f"trace_{case_name}"

    # Auto-run on first selection; explicit button always forces a rerun
    if cache_key not in st.session_state or run_btn:
        with st.spinner("Building graph & running pipeline…"):
            if case_type == "synthetic":
                graph = build_graph(inp)
            elif case_type == "native":
                graph = build_native_graph(data)
            else:
                graph = build_helm_graph(data)
            store = FAISSStore(embedder=Embedder())
            store.index_graph(graph)
            ctx        = ContextBuilder(graph=graph, store=store).build(root_query)
            tokens     = _tokenize(root_query)
            dense_hits = store.search(root_query, top_k=5)
            bm25_hits  = store._bm25.search(root_query, top_k=5)
            fused_hits = store.hybrid_search(root_query, top_k=5)
            hyps       = RemediationEngine().score(graph)
            prompt     = _build_prompt(root_query, ctx, kube_version="trace/n-a")

        st.session_state[cache_key] = {
            "ctx":        ctx,
            "tokens":     tokens,
            "dense_hits": dense_hits,
            "bm25_hits":  bm25_hits,
            "fused_hits": fused_hits,
            "hyps":       hyps,
            "prompt":     prompt,
        }

    c = st.session_state[cache_key]
    ctx        = c["ctx"]
    tokens     = c["tokens"]
    dense_hits = c["dense_hits"]
    bm25_hits  = c["bm25_hits"]
    fused_hits = c["fused_hits"]
    hyps       = c["hyps"]
    prompt     = c["prompt"]

    col_hdr, col_rerun = st.columns([5, 1])
    col_hdr.success(
        f"Pipeline — {ctx.total_chunks} chunks  ·  "
        f"confidence {ctx.pre_llm_confidence.score:.2f} {ctx.pre_llm_confidence.label}",
        icon="✅",
    )
    if col_rerun.button("🔄 Rerun", use_container_width=True, key=f"rerun_{cache_key}"):
        del st.session_state[cache_key]
        st.rerun()

    # ── Step 1: BM25 tokenizer ─────────────────────────────────────────────────
    with st.expander("Step 1 — BM25 tokenizer", expanded=True):
        st.caption(f"Query: _{root_query}_")
        st.write(f"**{len(tokens)} tokens:** `{' · '.join(tokens)}`")

    # ── Step 2: FAISS dense hits ───────────────────────────────────────────────
    with st.expander("Step 2 — FAISS dense hits (cosine similarity)", expanded=True):
        if dense_hits:
            import pandas as pd
            st.dataframe(
                pd.DataFrame([{
                    "score": round(h.get("score", 0), 4),
                    "kind":  h.get("kind", "?"),
                    "uid":   h["uid"][:80],
                    "text":  h.get("text", "")[:120],
                } for h in dense_hits]),
                use_container_width=True, hide_index=True,
            )
        else:
            st.warning("No dense hits.")

    # ── Step 3: BM25 sparse hits ───────────────────────────────────────────────
    with st.expander("Step 3 — BM25 sparse hits (keyword)", expanded=True):
        if bm25_hits:
            import pandas as pd
            st.dataframe(
                pd.DataFrame([{
                    "bm25":  round(h.get("bm25_score", 0), 4),
                    "kind":  h.get("kind", "?"),
                    "uid":   h["uid"][:80],
                    "text":  h.get("text", "")[:120],
                } for h in bm25_hits]),
                use_container_width=True, hide_index=True,
            )
        else:
            st.warning("No BM25 hits — no keyword overlap with corpus.")

    # ── Step 4: RRF fusion ─────────────────────────────────────────────────────
    with st.expander("Step 4 — RRF fusion (dense + sparse)", expanded=True):
        rs = ctx.retrieval_stats
        if rs:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Dense (FAISS)",  rs.get("dense",  "—"))
            c2.metric("Sparse (BM25)",  rs.get("sparse", "—"))
            c3.metric("Fused (RRF)",    rs.get("fused",  "—"))
            c4.metric("Top RRF score",  rs.get("top_rrf_score", "—"))
        if fused_hits:
            import pandas as pd
            st.dataframe(
                pd.DataFrame([{
                    "rrf_score": round(h.get("rrf_score", 0), 5),
                    "kind":      h.get("kind", "?"),
                    "uid":       h["uid"][:80],
                    "text":      h.get("text", "")[:120],
                } for h in fused_hits]),
                use_container_width=True, hide_index=True,
            )

    # ── Step 5: Seeds (unhealthy resources) ────────────────────────────────────
    with st.expander(f"Step 5 — Seeds — {len(ctx.seeds)} unhealthy resource(s)", expanded=True):
        if ctx.seeds:
            for s in ctx.seeds:
                st.markdown(f"- `{s[:200]}`")
        else:
            st.info("No unhealthy seeds found.")
        if ctx.events:
            st.markdown(f"**{len(ctx.events)} Warning event(s):**")
            for ev in ctx.events[:5]:
                st.markdown(f"  - {ev[:200]}")

    # ── Step 6: Anchors — pivot table declared → observed → fix ───────────────
    with st.expander(
        f"Step 6 — Anchors ({len(ctx.anchors)} declared values → drift → fix)",
        expanded=True,
    ):
        import re as _re
        import pandas as pd

        if not ctx.anchors:
            st.info("No anchors found — run HelmDriftDetector + AnchorEngine for this case.")
        else:
            # Build a lookup: drift_key → observed value
            # drift text: "kind/ns/name: drift.field.path: declared='X' observed='Y' ..."
            # or anchor_fixes text: "kind/ns/name  field='val' (declared)  →  helm upgrade ..."
            drift_observed: dict[str, str] = {}
            for d in ctx.drift:
                m_field = _re.search(r"drift\.([\w.]+)(?:\s*:|\s+declared)", d)
                m_obs   = _re.search(r"observed='?([^'\s|]+)'?", d)
                if m_field and m_obs:
                    drift_observed[m_field.group(1)] = m_obs.group(1)

            # Build lookup: field path → fix command
            fix_for_field: dict[str, str] = {}
            for fix in ctx.anchor_fixes:
                m_field = _re.search(r"(container\.\S+|spec\.\S+|\w+[\.\w]+)\s*=", fix)
                m_cmd   = fix.split("  →  ", 1)
                if m_field and len(m_cmd) == 2:
                    fix_for_field[m_field.group(1)] = m_cmd[1].strip()

            rows = []
            for ann in ctx.anchors:
                # Format: "kind/ns/name: field.path declared='X' [source] | observed='Y' [drift]"
                m_resource = _re.match(r"^([^:]+):\s*", ann)
                m_field    = _re.search(r":\s*([^\s]+)\s+declared=", ann)
                m_decl     = _re.search(r"declared='?([^'\s|]+)'?", ann)
                m_obs_ann  = _re.search(r"observed='?([^'\s|]+)'?", ann)
                m_src      = _re.search(r"\[(manifest|schema|values\.yaml|helm-deployed)\]", ann)

                resource  = m_resource.group(1).strip() if m_resource else "?"
                field     = m_field.group(1) if m_field else "?"
                declared  = m_decl.group(1) if m_decl else "?"
                # Observed: prefer drift lookup, then inline annotation
                observed  = drift_observed.get(field) or (m_obs_ann.group(1) if m_obs_ann else "—")
                source    = m_src.group(1) if m_src else "?"

                has_drift = observed not in ("—", declared)
                status    = "🔴 DRIFT" if has_drift else "✅ OK"
                fix_cmd   = fix_for_field.get(field, "—") if has_drift else "—"

                rows.append({
                    "resource": resource.split("/")[-1] if "/" in resource else resource,
                    "field":    field,
                    "declared": declared[:60],
                    "observed": observed[:60],
                    "source":   source,
                    "status":   status,
                    "fix":      fix_cmd[:80] if fix_cmd != "—" else "—",
                })

            df = pd.DataFrame(rows)
            st.dataframe(df, use_container_width=True, hide_index=True)

            drift_count = sum(1 for r in rows if r["status"] == "🔴 DRIFT")
            if drift_count:
                st.warning(f"{drift_count} anchor(s) have drift — see Step 10 for fix commands.", icon="🔴")
            else:
                st.success("All declared values match observed state.", icon="✅")

    # ── Step 7: Jaccard dedup ──────────────────────────────────────────────────
    with st.expander("Step 7 — Jaccard deduplication", expanded=True):
        js         = ctx.jaccard_stats
        candidates = js.get("candidates", "?")
        kept       = js.get("kept", "?")
        ratio      = kept / max(candidates, 1) if isinstance(candidates, int) and isinstance(kept, int) else 0
        c1, c2, c3 = st.columns(3)
        c1.metric("Candidates",       candidates)
        c2.metric("Kept after dedup", kept)
        c3.metric("Diversity ratio",  f"{ratio:.0%}")
        st.caption("Jaccard threshold=0.7  ·  lower = stricter dedup")

    # ── Step 8: TF-IDF ranked context ─────────────────────────────────────────
    with st.expander(f"Step 8 — TF-IDF ranked context — {len(ctx.related)} chunk(s)", expanded=False):
        if ctx.related:
            for i, chunk in enumerate(ctx.related, 1):
                st.markdown(f"**#{i}** `{chunk[:250]}`")
        else:
            st.info("No related context after dedup+ranking.")

    # ── Step 9: Confidence score breakdown ────────────────────────────────────
    with st.expander("Step 9 — Confidence score breakdown", expanded=True):
        conf = ctx.pre_llm_confidence
        st.metric("Score", f"{conf.score:.2f}", delta=conf.label)
        for reason in conf.reasons:
            st.markdown(f"  - {reason}")

    # ── Step 10: Proposed Remediation — values / helm / OPA ──────────────────
    _render_proposed_changes(ctx, case_name, case_type)

    # ── Bonus: RemediationEngine hypotheses ───────────────────────────────────
    with st.expander("Bonus — RemediationEngine (rule-based hypotheses)", expanded=False):
        if hyps:
            for h in hyps:
                with st.container():
                    b1, b2 = st.columns([1, 5])
                    b1.metric("Weight", f"{h.weight:.2f}")
                    b2.markdown(f"**[{h.rule_id}]** {h.symptom}  \n_{h.explanation}_")
                    for cmd in h.commands:
                        st.code(cmd, language="bash")
                    st.divider()
        else:
            st.info("No hypotheses fired.")

    # ── Bonus: LLM prompt dry-run ─────────────────────────────────────────────
    with st.expander("Bonus — LLM prompt dry-run (what would be sent to Ollama)", expanded=False):
        st.caption(f"{len(prompt)} characters total")
        st.text_area("Prompt preview", value=prompt[:3000], height=400, disabled=True)


# Tab layout — entry point
# ─────────────────────────────────────────────────────────────────────────────

tab_rca, tab_kb, tab_dash, tab_it = st.tabs([
    "🔍 Root Cause Analysis", "📚 Knowledge Base", "📊 Dashboard", "🧪 Integration Tests",
])

with tab_rca:
    _render_rca()

with tab_kb:
    _render_kb()

with tab_dash:
    _render_dashboard()

with tab_it:
    _render_integration_tests()
