#!/usr/bin/env python3
"""
KubeVerdict demo — end-to-end RCA runner.

Usage
─────
    python demo/run_rca.py
    python demo/run_rca.py "Why is the payment-service down?"

Output
──────
    - Full report printed to stdout
    - Saved to demo/output/rca_<timestamp>.txt

Prerequisites
─────────────
    1. demo/setup.sh has been run
    2. Ollama is running with Mistral  →  ollama serve & ollama pull mistral
    3. demo/.env.demo exists
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv                             # noqa: E402

ENV_FILE = Path(__file__).parent / ".env.demo"
if not ENV_FILE.exists():
    ENV_FILE = ROOT / ".env"
load_dotenv(dotenv_path=ENV_FILE, override=True)

import config as cfg                                       # noqa: E402
from ingestion.k8s_collector import K8sCollector           # noqa: E402
from ingestion.helm_collector import HelmCollector         # noqa: E402
from langgraph.types import Command                        # noqa: E402
from ontology.entities import ResourceKind                 # noqa: E402
from vectorstore.embedder import Embedder                  # noqa: E402
from vectorstore.store import FAISSStore                   # noqa: E402
from workflow.graph import build_graph                     # noqa: E402

DEMO_NS = "kubeverdict-demo"
OUTPUT_DIR = Path(__file__).parent / "output"
W = 68

DEFAULT_QUERY = (
    "Multiple services are failing in the kubeverdict-demo namespace. "
    "Identify every root cause, explain the chain of events, "
    "and provide precise remediation commands."
)


# ─────────────────────────────────────────────────────────────────────────────
# Formatting helpers
# ─────────────────────────────────────────────────────────────────────────────

def _banner(title: str) -> str:
    return f"\n{'═' * W}\n  {title}\n{'═' * W}"

def _section(title: str) -> str:
    return f"\n{'─' * W}\n  {title}\n{'─' * W}"

def _bullet(items: list[str], indent: int = 4) -> str:
    pad = " " * indent
    return "\n".join(f"{pad}• {item}" for item in items) if items else f"{' ' * indent}(none)"


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("query", nargs="*", help="RCA query")
    parser.add_argument("--yes", action="store_true", help="Skip cluster confirmation prompt")
    parser.add_argument("--auto-approve", action="store_true", help="Auto-approve remediation")
    args = parser.parse_args()
    query = " ".join(args.query) if args.query else DEFAULT_QUERY
    auto_yes = args.yes
    auto_approve = args.auto_approve
    ts    = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    lines: list[str] = []   # accumulate for file output

    def emit(s: str = "") -> None:
        print(s)
        lines.append(s)

    # ── Context confirmation ──────────────────────────────────────────────────
    import subprocess
    _ctx = cfg.KUBE_CONTEXT or subprocess.run(
        ["kubectl", "config", "current-context"],
        capture_output=True, text=True,
    ).stdout.strip()
    print(f"\n  Target context : {_ctx}")
    print(f"  Namespace      : {DEMO_NS}")
    print(f"  Model          : {cfg.OLLAMA_MODEL} @ {cfg.OLLAMA_URL}")
    if auto_yes:
        print("\n  --yes: skipping confirmation.")
    else:
        _ok = input("\n  Run RCA on this cluster? [y/N] ").strip().lower()
        if _ok != "y":
            print("Aborted.")
            return

    emit(_banner("KubeVerdict — Root Cause Analysis Demo"))
    emit(f"  Timestamp : {datetime.now(timezone.utc).isoformat()}")
    emit(f"  Namespace : {DEMO_NS}")
    emit(f"  Model     : {cfg.OLLAMA_MODEL} @ {cfg.OLLAMA_URL}")
    emit(f"  Query     : {query}")

    t_start = time.perf_counter()

    # ── 1. Collect cluster state ──────────────────────────────────────────────
    emit(_section("1/5  Cluster state collection"))
    collector = K8sCollector(kubeconfig=cfg.KUBECONFIG, context=cfg.KUBE_CONTEXT)
    graph = collector.collect(namespaces=[DEMO_NS])
    emit(f"  {graph.summary()}")

    # Helm releases
    helm = HelmCollector(kubeconfig=cfg.KUBECONFIG, kube_context=cfg.KUBE_CONTEXT)
    helm.collect(graph, namespaces=[DEMO_NS])
    releases = list(graph.entities(ResourceKind.HELM_RELEASE))
    emit(f"  Helm releases : {len(releases)}")
    for r in releases:
        from ontology.entities import HelmRelease
        if isinstance(r, HelmRelease):
            emit(f"    • {r.name}  chart={r.chart}@{r.chart_version}  status={r.status}")

    # ── 2. GitOps drift ───────────────────────────────────────────────────────
    emit(_section("2/5  GitOps drift detection"))
    drift_items: list[str] = []
    if cfg.GITOPS_ENABLED and cfg.GITOPS_REPO_URL:
        try:
            from ingestion.git_provider import LocalGitProvider
            from ingestion.gitops_collector import GitopsCollector
            provider  = LocalGitProvider(repo_url=cfg.GITOPS_REPO_URL, branch=cfg.GITOPS_BRANCH)
            collector_go = GitopsCollector(provider, charts_path=cfg.GITOPS_CHARTS_PATH)
            drifts = collector_go.collect(graph)
            for d in drifts:
                msg = f"{d.field_path}: declared={d.declared!r} → observed={d.observed!r} [{d.severity}]"
                drift_items.append(msg)
                emit(f"  ⚡ {msg}")
            if not drifts:
                emit("  No drift detected.")
        except Exception as exc:
            emit(f"  GitOps unavailable: {exc}")
    else:
        emit("  GitOps disabled.")

    # ── 3. Unhealthy resources + events ───────────────────────────────────────
    emit(_section("3/5  Unhealthy resources & Kubernetes events"))

    from ontology.entities import DaemonSet, Deployment, Pod, StatefulSet
    unhealthy: list[str] = []
    unhealthy_entities: list[object] = []   # keep actual entity objects for evidence section
    for e in graph.entities():
        if isinstance(e, Pod) and e.is_unhealthy:
            unhealthy.append(
                f"Pod/{e.namespace}/{e.name}  phase={e.phase}"
                f"  restarts={e.restart_count}"
                + (f"  cpu={e.annotations.get('metrics.cpu_m','?')}m"
                   f"  mem={e.annotations.get('metrics.memory_mi','?')}Mi"
                   if e.annotations.get("metrics.cpu_m") else "")
            )
            unhealthy_entities.append(e)
        elif isinstance(e, Deployment) and e.is_degraded:
            unhealthy.append(
                f"Deployment/{e.namespace}/{e.name}"
                f"  ready={e.ready_replicas}/{e.replicas}"
            )
            unhealthy_entities.append(e)
        elif isinstance(e, StatefulSet) and e.ready_replicas < e.replicas:
            unhealthy.append(f"StatefulSet/{e.namespace}/{e.name}  ready={e.ready_replicas}/{e.replicas}")
            unhealthy_entities.append(e)
        elif isinstance(e, DaemonSet) and e.ready < e.desired:
            unhealthy.append(f"DaemonSet/{e.namespace}/{e.name}  ready={e.ready}/{e.desired}")
            unhealthy_entities.append(e)

    emit(f"  Unhealthy resources ({len(unhealthy)}):")
    for u in unhealthy:
        emit(f"    ✗ {u}")

    events = sorted(
        [e for e in graph.entities(ResourceKind.EVENT) if e.is_warning],
        key=lambda e: e.count,
        reverse=True,
    )[:15]
    emit(f"\n  Warning events ({len(events)}):")
    for ev in events:
        emit(f"    [{ev.count:>3}×] {ev.reason}  {ev.involved_kind}/{ev.involved_name}"
             f"  — {ev.message[:80]}")

    # ── 4. Signal analysis ────────────────────────────────────────────────────
    emit(_section("4/5  Signal analysis (PatchTST)"))
    anomalies: list[str] = []
    signal_by_uid: dict[str, list] = {}
    try:
        from signals.analyzer import SignalAnalyzer
        prom_source = None
        if cfg.PROMETHEUS_ENABLED:
            from signals.prometheus_source import PrometheusMetricSource
            prom_source = PrometheusMetricSource(url=cfg.PROMETHEUS_URL)

        # Enrich with metrics-server if available
        if cfg.METRICS_SERVER_ENABLED:
            try:
                from ingestion.metrics_server_collector import MetricsServerCollector
                ms    = MetricsServerCollector(kubeconfig=cfg.KUBECONFIG, context=cfg.KUBE_CONTEXT)
                count = ms.collect(graph)
                emit(f"  metrics-server: {count} pod(s) annotated")
            except Exception as exc:
                emit(f"  metrics-server: {exc}")

        results = SignalAnalyzer(prometheus_source=prom_source).analyze(graph)
        for r in results:
            signal_by_uid.setdefault(r.entity_uid, []).append(r)
            if r.is_anomalous:
                entity = graph.get(r.entity_uid)
                label = (
                    f"{entity.namespace}/{entity.name}" if entity else r.entity_uid
                )
                msg = f"{label}  {r.metric_name}  {r.severity}  score={r.score:.3f}"
                anomalies.append(msg)
                emit(f"  ⚠ {msg}")
        if not anomalies:
            emit("  No anomalies detected.")
    except Exception as exc:
        emit(f"  Signal analysis failed: {exc}")

    # ── 5. LangGraph RCA workflow ─────────────────────────────────────────────
    emit(_section("5/5  LLM root-cause analysis (LangGraph workflow)"))
    emit(f"  Sending context to {cfg.OLLAMA_MODEL}...")

    t_llm = time.perf_counter()
    store = FAISSStore(embedder=Embedder())
    store.index_graph(graph)

    rca_graph = build_graph()
    wf_config = {
        "configurable": {
            "thread_id": f"demo-{ts}",
            "graph": graph,
            "store": store,
        }
    }
    initial_state = {
        "query": query,
        "retry_count": 0,
        "human_decision": "",
        "error": "",
    }

    # Stream until human_review interrupt
    interrupt_payload = None
    for event in rca_graph.stream(initial_state, config=wf_config):
        if "__interrupt__" in event:
            interrupt_payload = event["__interrupt__"][0].value
            break

    llm_time = time.perf_counter() - t_llm

    if interrupt_payload is None:
        emit("  (workflow completed without interrupt — no human review needed)")
    else:
        confidence = (interrupt_payload.get("confidence", "") or "").strip().rstrip(":")
        conf_word  = (confidence.split()[0] if confidence else "?").upper()
        remediation_cmds: list[str] = interrupt_payload.get("remediation") or []

        # ── Executive Summary (read in 10 seconds) ────────────────────────────
        impacted = [u.split("/")[-1] for u in unhealthy if "Pod/" not in u] or \
                   [u.split("/")[-1] for u in unhealthy]
        emit(_banner("INCIDENT SUMMARY"))
        emit(f"  Severity    : {'HIGH' if conf_word in ('HIGH','MEDIUM') else 'LOW'}")
        emit(f"  Namespace   : {DEMO_NS}")
        emit(f"  Confidence  : {conf_word}")
        emit(f"  Impacted    : {', '.join(impacted[:5]) or 'see below'}")
        emit("")
        emit("  Root cause  :")
        root_cause = interrupt_payload.get("root_cause") or "(not identified)"
        for line in root_cause.splitlines()[:3]:
            emit(f"    {line.strip()}")
        emit("")
        emit("  Key evidence:")
        # Pull top 3 warning events as evidence
        for ev in events[:3]:
            emit(f"    • [{ev.count}×] {ev.reason} on {ev.involved_kind}/{ev.involved_name}")
            emit(f'      “{ev.message[:90]}”')
        if drift_items:
            for d in drift_items[:2]:
                emit(f"    • Helm drift: {d}")
        emit("")
        emit("  Proposed fix:")
        for cmd in remediation_cmds[:3]:
            emit(f"    $ {cmd}")

        # ── Full detail ───────────────────────────────────────────────────────
        emit(_banner("FULL ANALYSIS"))

        emit(_section("Root cause chain"))
        emit(root_cause)

        # ── Evidence by resource ──────────────────────────────────────────────
        # Build an index of warning events keyed by "Kind/name" for fast lookup.
        events_by_resource: dict[str, list] = {}
        for ev in graph.entities(ResourceKind.EVENT):
            if ev.is_warning:
                key = f"{ev.involved_kind}/{ev.involved_name}"
                events_by_resource.setdefault(key, []).append(ev)

        emit(_section("Evidence by resource"))
        for entity in unhealthy_entities:
            kind = type(entity).__name__
            label = f"{kind}/{entity.namespace}/{entity.name}"
            emit(f"\n  {label}")

            # Status line
            if isinstance(entity, Pod):
                status = f"phase={entity.phase}  restarts={entity.restart_count}"
                if entity.annotations.get("metrics.cpu_m"):
                    status += (
                        f"  cpu={entity.annotations['metrics.cpu_m']}m"
                        f"  mem={entity.annotations.get('metrics.memory_mi','?')}Mi"
                    )
                emit(f"    Status  : {status}")
                # Container-level termination reason from annotations
                reason = entity.annotations.get("last_termination_reason", "")
                if reason:
                    emit(f"    Reason  : {reason}")
            elif isinstance(entity, Deployment):
                emit(f"    Status  : ready={entity.ready_replicas}/{entity.replicas}")
            elif isinstance(entity, StatefulSet):
                emit(f"    Status  : ready={entity.ready_replicas}/{entity.replicas}")
            elif isinstance(entity, DaemonSet):
                emit(f"    Status  : ready={entity.ready}/{entity.desired}")

            # Matching K8s events
            ev_key = f"Pod/{entity.name}" if isinstance(entity, Pod) else f"{kind}/{entity.name}"
            matched_events = sorted(
                events_by_resource.get(ev_key, []),
                key=lambda e: e.count, reverse=True,
            )[:5]
            if matched_events:
                emit("    Events  :")
                for ev in matched_events:
                    emit(f"      [{ev.count:>3}×] {ev.reason:<18} {ev.message[:70]}")
            else:
                emit("    Events  : (none)")

            # Signal anomalies for this entity
            uid = getattr(entity, "uid", None)
            entity_signals = [r for r in signal_by_uid.get(uid or "", []) if r.is_anomalous]
            if entity_signals:
                emit("    Signals :")
                for r in entity_signals:
                    emit(f"      ⚠ {r.metric_name:<20} {r.severity:<8} score={r.score:.3f}")
            else:
                emit("    Signals : (none)")

        if not unhealthy_entities:
            emit("  (no unhealthy resources found)")

        if drift_items:
            emit(_section("GitOps drift detected"))
            for d in drift_items:
                emit(f"  ⚡ {d}")

        if anomalies:
            emit(_section("Signal anomalies (PatchTST)"))
            for a in anomalies:
                emit(f"  ⚠ {a}")

        emit(_section("All remediation commands"))
        if remediation_cmds:
            for i, cmd in enumerate(remediation_cmds, 1):
                emit(f"  {i:>2}. {cmd}")
        else:
            emit("  (no remediation commands generated)")

        if drift_items:
            emit("")
            emit("  Helm / GitOps fix — restore declared state:")
            for r in releases:
                from ontology.entities import HelmRelease
                if isinstance(r, HelmRelease):
                    emit(f"    helm upgrade {r.name} demo/charts/{r.name} -n {DEMO_NS}")

        emit(_section("Metadata"))
        emit(f"  Confidence  : {confidence or 'N/A'}")
        emit(f"  LLM time    : {llm_time:.1f}s")

        # ── Human approval gate ───────────────────────────────────────────────
        print(f"\n{'─' * W}")
        print(f"  Confidence: {confidence}")
        if auto_approve:
            decision = "approve"
            print("  --auto-approve: applying remediation.")
        else:
            print("  Approve and apply remediation? [approve/reject] (Enter = reject): ", end="")
            decision = input().strip().lower() or "reject"
            if decision not in ("approve", "reject"):
                decision = "reject"

        final = rca_graph.invoke(Command(resume=decision), config=wf_config)
        emit(f"\n  Human decision: {final.get('human_decision', decision)}")
        if decision == "approve":
            emit("  ✓ Remediation approved — commands above should be applied.")
        else:
            emit("  ✗ Rejected — no changes applied.")

    total_time = time.perf_counter() - t_start
    emit(_section("Timings"))
    emit(f"  LLM time    : {llm_time:.1f}s")
    emit(f"  Total time  : {total_time:.1f}s")
    emit("")

    # ── Save to file ──────────────────────────────────────────────────────────
    OUTPUT_DIR.mkdir(exist_ok=True)
    out_file = OUTPUT_DIR / f"rca_{ts}.txt"
    out_file.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n  Report saved → {out_file}")


if __name__ == "__main__":
    main()
