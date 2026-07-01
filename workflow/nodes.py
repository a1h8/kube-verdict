"""
LangGraph node functions for the KubeVerdict RCA workflow.

Heavy objects (OntologyGraph, FAISSStore) are NOT stored in state — they are
passed via config["configurable"] so the MemorySaver checkpointer never tries
to serialise them.  Nodes that need them call _get_infra(config).
"""
from __future__ import annotations
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

import config as cfg
from rca.analyzer import RCAAnalyzer, _generate_rollback
from rca.context_builder import ContextBuilder
from decision.decision_engine import DecisionEngine
from decision.models import IncidentReport
from reasoning.beam_search import MAX_SWITCHES, should_switch_path
from reasoning.monte_carlo import run_monte_carlo
from reasoning.template_catalog import TemplateCatalog
from remediation.blast_radius import compute_blast_radius
from vectorstore.embedder import Embedder
from vectorstore.store import FAISSStore
from workflow.state import RCAState

# LangGraph does not propagate config mutations between nodes.
# Heavy non-serializable objects are cached here keyed by thread_id.
_INFRA_CACHE: dict[str, tuple[Any, Any]] = {}

log = logging.getLogger(__name__)

MAX_RETRIES = 2
MAX_PATHS   = 3     # max distinct hypotheses to explore per run
EXAMPLE_MATCH_THRESHOLD = 0.65   # cosine similarity (IndexFlatIP, L2-normalised, all-MiniLM-L6-v2)


def _stats(state: RCAState, step: str, data: dict) -> dict:
    """Merge per-step telemetry into ingestion_stats without overwriting other steps."""
    current = dict(state.get("ingestion_stats") or {})
    current[step] = data
    return {"ingestion_stats": current}


# Parses the fixed DriftItem.to_text() format written by ManifestDiffer as
# `gitops.<field_path>` annotations. Greedy declared/observed terminated by the
# trailing ` severity=<sev>`, so values containing spaces survive intact.
_GITOPS_DRIFT_RE = re.compile(
    r"^drift field=(?P<fp>\S+) declared=(?P<dec>.*) observed=(?P<obs>.*) "
    r"severity=(?P<sev>\S+)$"
)


def _render_evidence_rows(graph: Any) -> list[dict]:
    """
    Extract structured render-vs-live drift from the graph for the UI.

    GitopsCollector renders the chart with `helm template` and writes each
    discrepancy as a ``gitops.<field_path>`` annotation (DriftItem.to_text()).
    We group those back per entity so the Decision Journey can show the
    expected-state-vs-live diff that grounds the verdict.
    """
    rows: list[dict] = []
    for entity in graph.entities():
        diffs: list[dict] = []
        for value in entity.annotations.values():
            m = _GITOPS_DRIFT_RE.match(str(value))
            if m:
                diffs.append({
                    "field_path": m.group("fp"),
                    "declared": m.group("dec"),
                    "observed": m.group("obs"),
                    "severity": m.group("sev"),
                })
        if diffs:
            rows.append({
                "kind": getattr(entity.kind, "value", str(entity.kind)),
                "name": entity.name,
                "namespace": entity.namespace or "",
                "diffs": sorted(diffs, key=lambda d: d["field_path"]),
            })
    return sorted(rows, key=lambda r: (r["kind"], r["name"]))


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_infra(config: RunnableConfig) -> tuple[Any, Any]:
    """Pull graph and store from config or the thread-local infra cache."""
    c = config.get("configurable", {})
    graph = c.get("graph")
    store = c.get("store")
    if graph is None:
        tid = c.get("thread_id", "")
        cached = _INFRA_CACHE.get(tid)
        if cached:
            graph, store_cached = cached
            if store is None:
                store = store_cached
    return graph, store


def _get_provider(config: RunnableConfig):
    """Return the GitProvider stored by gitops_node, or None."""
    return config.get("configurable", {}).get("provider")


def _get_llm(config: RunnableConfig):
    """Return injected LLM (tests) or the provider configured by LLM_PROVIDER."""
    injected = config.get("configurable", {}).get("llm")
    if injected is not None:
        return injected
    from llm import build_llm_client
    return build_llm_client()


# ─────────────────────────────────────────────────────────────────────────────
# Ingestion
# ─────────────────────────────────────────────────────────────────────────────

def ingest_node(state: RCAState, config: RunnableConfig) -> dict:
    """
    Collect cluster state from K8s API + Helm + Helmfile.
    Skipped when a pre-built graph is already in config["configurable"].
    """
    graph, _ = _get_infra(config)
    if graph is not None:
        log.info("ingest: pre-built graph provided — skipping collection")
        return _stats(state, "ingest", {"skipped": True, "reason": "pre-built"})

    try:
        from ingestion import K8sCollector, HelmCollector, HelmDriftDetector

        collector = K8sCollector(
            kubeconfig=state.get("kubeconfig") or cfg.KUBECONFIG,
            context=state.get("kube_context") or cfg.KUBE_CONTEXT,
        )
        built_graph = collector.collect(
            namespaces=state.get("namespaces") or cfg.KUBE_NAMESPACES or None
        )

        helm = HelmCollector(
            kubeconfig=state.get("kubeconfig") or cfg.KUBECONFIG,
            kube_context=state.get("kube_context") or cfg.KUBE_CONTEXT,
        )
        helm_releases = 0
        helm.collect(built_graph, namespaces=state.get("namespaces") or None)
        from ontology.entities import ResourceKind as _RK
        helm_releases = sum(1 for _ in built_graph.entities(_RK.HELM_RELEASE))

        if cfg.HELMFILE_PATH:
            from ingestion import HelmfileCollector
            hf = HelmfileCollector(
                helmfile_path=cfg.HELMFILE_PATH,
                environment=cfg.HELMFILE_ENVIRONMENT,
                use_cli=cfg.HELMFILE_USE_CLI,
            )
            hf.collect(built_graph)

        drift_count = HelmDriftDetector().detect_all(built_graph)
        kube_ver = getattr(collector, "kube_version", None)
        log.info("ingest: %s", built_graph.summary())

        tid = config.get("configurable", {}).get("thread_id", "")
        store_ref = config.get("configurable", {}).get("store")
        _INFRA_CACHE[tid] = (built_graph, store_ref)
        return _stats(state, "ingest", {
            "entities": built_graph.node_count,
            "edges": built_graph.edge_count,
            "helm_releases": helm_releases,
            "helm_drift": drift_count,
            "kube_version": str(kube_ver) if kube_ver else "",
            "fallback": False,
        })

    except Exception as exc:
        log.error("ingest failed: %s", exc)
        return {"error": str(exc), **_stats(state, "ingest", {"fallback": True, "error": str(exc)})}


# ─────────────────────────────────────────────────────────────────────────────
# Prometheus alert correlation
# ─────────────────────────────────────────────────────────────────────────────

def prometheus_node(state: RCAState, config: RunnableConfig) -> dict:
    """
    Fetch firing Prometheus alerts and correlate with OntologyGraph entities.
    Skipped when PROMETHEUS_ENABLED=false.
    Fails silently — alert correlation is enrichment, not a blocker.
    """
    if not cfg.PROMETHEUS_ENABLED:
        log.info("prometheus: disabled — skipping")
        return _stats(state, "prometheus", {"skipped": True})

    graph, _ = _get_infra(config)
    if graph is None:
        log.info("prometheus: no graph — skipping")
        return _stats(state, "prometheus", {"skipped": True, "reason": "no graph"})

    try:
        from ingestion.prometheus_collector import PrometheusCollector
        collector = PrometheusCollector(
            url=cfg.PROMETHEUS_URL,
            token=cfg.PROMETHEUS_TOKEN,
            timeout=cfg.PROMETHEUS_TIMEOUT,
        )
        count = collector.collect(graph)
        log.info("prometheus: %d alert(s) correlated", count)
        return _stats(state, "prometheus", {"alerts": count, "fallback": False})
    except Exception as exc:
        log.warning("prometheus node failed (%s) — continuing without alert data", exc)
        return _stats(state, "prometheus", {"fallback": True, "error": str(exc)})


# ─────────────────────────────────────────────────────────────────────────────
# Metrics-server (kubectl top)
# ─────────────────────────────────────────────────────────────────────────────

def metrics_node(state: RCAState, config: RunnableConfig) -> dict:
    """
    Fetch live CPU/memory from metrics-server and annotate pod entities.
    Skipped when METRICS_SERVER_ENABLED=false.
    Fails silently — enrichment only, not a blocker.
    """
    if not cfg.METRICS_SERVER_ENABLED:
        log.info("metrics-server: disabled — skipping")
        return _stats(state, "metrics", {"skipped": True})

    graph, _ = _get_infra(config)
    if graph is None:
        log.info("metrics-server: no graph — skipping")
        return _stats(state, "metrics", {"skipped": True, "reason": "no graph"})

    try:
        from ingestion.metrics_server_collector import MetricsServerCollector
        collector = MetricsServerCollector(
            kubeconfig=state.get("kubeconfig") or cfg.KUBECONFIG,
            context=state.get("kube_context") or cfg.KUBE_CONTEXT,
        )
        count = collector.collect(graph)
        log.info("metrics-server: %d pod(s) annotated", count)
        return _stats(state, "metrics", {"pods_annotated": count, "fallback": False})
    except Exception as exc:
        log.warning("metrics-server node failed (%s) — continuing without metrics data", exc)
        return _stats(state, "metrics", {"fallback": True, "error": str(exc)})


# ─────────────────────────────────────────────────────────────────────────────
# OpenTelemetry traces + Loki logs
# ─────────────────────────────────────────────────────────────────────────────

def otel_node(state: RCAState, config: RunnableConfig) -> dict:
    """
    Fetch OTel error traces (Tempo/Jaeger) and Loki logs for unhealthy entities.
    Skipped when both OTEL_ENABLED and LOKI_ENABLED are false.
    Fails silently — observability data is enrichment, not a blocker.
    """
    if not cfg.OTEL_ENABLED and not cfg.LOKI_ENABLED:
        log.info("otel: disabled — skipping")
        return _stats(state, "otel", {"skipped": True})

    graph, _ = _get_infra(config)
    if graph is None:
        log.info("otel: no graph — skipping")
        return {}

    otel_stat: dict = {}
    if cfg.OTEL_ENABLED:
        try:
            from ingestion.otel_backend import build_backend
            from ingestion.otel_collector import OtelCollector
            backend = build_backend(
                cfg.OTEL_BACKEND_TYPE, cfg.OTEL_BACKEND_URL,
                cfg.OTEL_TOKEN, cfg.OTEL_TIMEOUT,
                otlp_host=cfg.OTLP_HOST,
                otlp_port=cfg.OTLP_PORT,
                otlp_max_traces=cfg.OTLP_MAX_TRACES,
            )
            count = OtelCollector(backend, lookback_hours=cfg.OTEL_LOOKBACK_HOURS).collect(graph)
            log.info("otel: %d trace(s) ingested", count)
            otel_stat["traces"] = count
        except Exception as exc:
            log.warning("otel traces failed (%s) — continuing without trace data", exc)
            otel_stat["traces_fallback"] = str(exc)

    if cfg.LOKI_ENABLED:
        try:
            from ingestion.loki_source import LokiSource
            loki = LokiSource(
                url=cfg.LOKI_URL, token=cfg.LOKI_TOKEN, timeout=cfg.LOKI_TIMEOUT,
                lookback_hours=cfg.LOKI_LOOKBACK_HOURS,
                max_logs_per_pod=cfg.LOKI_MAX_LOGS_PER_POD,
            )
            count = loki.collect(graph)
            log.info("loki: %d log(s) ingested", count)
            otel_stat["logs"] = count
        except Exception as exc:
            log.warning("loki logs failed (%s) — continuing without log data", exc)
            otel_stat["logs_fallback"] = str(exc)

    return _stats(state, "otel", otel_stat or {"skipped": True})


# ─────────────────────────────────────────────────────────────────────────────
# GitOps drift detection
# ─────────────────────────────────────────────────────────────────────────────

def gitops_node(state: RCAState, config: RunnableConfig) -> dict:
    """
    Run GitOps drift detection: fetch chart from Git, helm template, diff vs cluster.
    Skipped when GITOPS_ENABLED=false or no GITOPS_REPO_URL is configured.
    Fails silently — gitops drift is enrichment, not a blocker.
    """
    if not cfg.GITOPS_ENABLED or not cfg.GITOPS_REPO_URL:
        log.info("gitops: disabled or no repo URL — skipping")
        return _stats(state, "gitops", {"skipped": True})

    graph, _ = _get_infra(config)
    if graph is None:
        log.info("gitops: no graph — skipping")
        return {}

    try:
        from ingestion.git_provider import GithubProvider, LocalGitProvider
        from ingestion.gitops_collector import GitopsCollector

        if cfg.GITOPS_REPO_URL.startswith(("https://github.com", "git@github.com")):
            repo = cfg.GITOPS_REPO_URL.removeprefix("https://github.com/").removesuffix(".git")
            provider = GithubProvider(repo=repo, ref=cfg.GITOPS_BRANCH, token=cfg.GITHUB_TOKEN)
        else:
            provider = LocalGitProvider(
                repo_url=cfg.GITOPS_REPO_URL, branch=cfg.GITOPS_BRANCH,
            )

        collector = GitopsCollector(provider, charts_path=cfg.GITOPS_CHARTS_PATH)
        drifts = collector.collect(graph)
        critical = sum(1 for d in drifts if d.severity == "critical")
        log.info("gitops: %d drift(s) found, %d critical", len(drifts), critical)

        config.setdefault("configurable", {})["provider"] = provider
        update = _stats(state, "gitops", {
            "drifts": len(drifts), "critical": critical, "fallback": False,
        })
        update["drift_evidence"] = _render_evidence_rows(graph)
        return update

    except Exception as exc:
        log.warning("gitops node failed (%s) — continuing without gitops data", exc)
        return _stats(state, "gitops", {"fallback": True, "error": str(exc)})


# ─────────────────────────────────────────────────────────────────────────────
# Anchor collection
# ─────────────────────────────────────────────────────────────────────────────

def anchor_node(state: RCAState, config: RunnableConfig) -> dict:
    """
    Collect declared-value anchors from two sources and write them as
    ``anchor.*`` annotations on OntologyGraph entities.

    Source 1 — K8s schema (always): valid values and defaults for every
               entity kind present in the graph.

    Source 2 — Rendered manifests (when gitops provider is available):
               ``helm template`` output gives the exact K8s field values
               that Helm would deploy, including the full value hierarchy
               (chart defaults < helmfile env value_files < release value_files
               < inline release values).  This is the generic ground truth —
               no heuristic value-key → field mapping needed.

    The anchors are consumed by ContextBuilder (``### ANCHORS`` section) so
    the LLM sees declared vs observed drift in structured form.

    Fails silently — anchor enrichment never blocks the analysis.
    """
    graph, _ = _get_infra(config)
    if graph is None:
        log.info("anchor: no graph — skipping")
        return {}

    provider = _get_provider(config)  # None when gitops is disabled

    try:
        from ingestion.anchor_engine import AnchorEngine
        engine = AnchorEngine()
        records = engine.collect(
            graph,
            provider=provider,
            charts_path=cfg.GITOPS_CHARTS_PATH,
        )
        with_manifest = sum(1 for r in records if r.source == "manifest")
        log.info(
            "anchor: %d anchor(s) (%d from manifest, %d from schema)",
            len(records), with_manifest, len(records) - with_manifest,
        )
        return _stats(state, "anchor", {
            "total": len(records), "manifest": with_manifest,
            "schema": len(records) - with_manifest, "fallback": False,
        })
    except Exception as exc:
        log.warning("anchor node failed (%s) — continuing without anchor data", exc)
        return _stats(state, "anchor", {"fallback": True, "error": str(exc)})


# ─────────────────────────────────────────────────────────────────────────────
# Indexing
# ─────────────────────────────────────────────────────────────────────────────

def index_node(state: RCAState, config: RunnableConfig) -> dict:
    """Embed entities and build FAISSStore. Skipped if already in config."""
    graph, store = _get_infra(config)
    if store is not None:
        log.info("index: pre-built store provided — skipping")
        return {}
    if graph is None:
        return {"error": "index_node: no graph available"}

    embedder = Embedder()
    built_store = FAISSStore(embedder=embedder)
    built_store.index_graph(graph)
    anchor_count = built_store.index_anchor_violations(graph)

    # Enterprise expected-state anchors — pushed Helm / Helmfile / Kustomize /
    # raw-manifest sources, rendered at their pinned version. Opt-in by presence:
    # no pushed charts → no-op. Best-effort: a render failure never breaks RCA.
    chart_anchor_count = 0
    try:
        from knowledge.chart_indexer import ChartIndexer
        from knowledge.chart_store import ChartStore
        chart_dir = (config.get("configurable", {}).get("chart_dir")
                     or os.environ.get("KUBEVERDICT_CHART_DIR"))
        chart_store = ChartStore(chart_dir) if chart_dir else ChartStore()
        if chart_store.list():
            ns = config.get("configurable", {}).get("namespace") or "default"
            chart_anchor_count = ChartIndexer(built_store).index_all(chart_store, namespace=ns)
    except Exception as exc:  # noqa: BLE001 — enterprise store must never break RCA
        log.warning("index: enterprise chart indexing skipped: %s", exc)

    built_store.save()

    from persistence.db import get_db
    conn = get_db()
    try:
        built_store.persist_texts(conn)
    finally:
        conn.close()

    log.info("index: %d vectors, %d anchor violation(s), %d chart anchor(s)",
             built_store.size, anchor_count, chart_anchor_count)

    config.setdefault("configurable", {})["store"] = built_store
    # Also refresh the thread-local infra cache: LangGraph does not reliably
    # share a mutated config["configurable"] across nodes, so downstream nodes
    # (analyze, example_lookup) read the store via _INFRA_CACHE. Without this,
    # a live run that did not pre-pass a store sees store=None in analyze_node
    # → silent error early-return → confidence=UNKNOWN → spurious NO_GO.
    # Only *update* the entry ingest_node established for this thread — never
    # create one — so isolated node tests don't leak a graph into the global
    # cache across the suite.
    tid = config.get("configurable", {}).get("thread_id", "")
    cached = _INFRA_CACHE.get(tid)
    if cached is not None:
        _INFRA_CACHE[tid] = (cached[0], built_store)
    return {}


# ─────────────────────────────────────────────────────────────────────────────
# Signal analysis (PatchTST)
# ─────────────────────────────────────────────────────────────────────────────

def signal_analysis_node(state: RCAState, config: RunnableConfig) -> dict:
    """
    Run PatchTST anomaly detection over entity metric signals.

    Annotates OntologyGraph entities with `signal.*` annotations so they
    surface in FAISS searches and appear in the LLM context window.
    Skipped gracefully if graph is not available.
    """
    graph, _ = _get_infra(config)
    if graph is None:
        log.info("signal_analysis: no graph — skipping")
        return {}

    try:
        from signals.analyzer import SignalAnalyzer
        prom_source = None
        if cfg.PROMETHEUS_ENABLED:
            from signals.prometheus_source import PrometheusMetricSource
            prom_source = PrometheusMetricSource(
                url=cfg.PROMETHEUS_URL,
                token=cfg.PROMETHEUS_TOKEN,
                timeout=cfg.PROMETHEUS_TIMEOUT,
            )
        results = SignalAnalyzer(prometheus_source=prom_source).analyze(graph)
        anomalous = [r for r in results if r.is_anomalous]
        mode = "real" if prom_source else "synthetic"
        log.info(
            "signal_analysis[%s]: %d metrics analysed, %d anomalous",
            mode, len(results), len(anomalous),
        )
        return _stats(state, "signals", {
            "total": len(results), "anomalous": len(anomalous),
            "mode": mode, "fallback": False,
        })
    except Exception as exc:
        log.warning("signal_analysis failed (%s) — continuing without signal data", exc)
        return _stats(state, "signals", {"fallback": True, "error": str(exc)})


# ─────────────────────────────────────────────────────────────────────────────
# Hypothesis generation (LLM-based — uses cluster evidence)
# ─────────────────────────────────────────────────────────────────────────────

def _parse_hypotheses(raw: str) -> list[str]:
    """
    Flexible parser for LLM hypothesis output.
    Tries in order:
      1. Explicit H1:/H2:/H3: format
      2. Numbered list  1. / 2. / 3.
      3. Bullet list    - / * / •
    Returns stripped, non-empty strings only.
    """
    patterns = [
        re.compile(r"^H\d+[:.]\s*(.+)", re.IGNORECASE),   # H1: ... or H1. ...
        re.compile(r"^\d+[.)]\s+(.+)"),                     # 1. ... or 1) ...
        re.compile(r"^[-*•]\s+(.+)"),                       # - ... or * ...
    ]
    for pat in patterns:
        results = []
        for line in raw.splitlines():
            m = pat.match(line.strip())
            if m:
                text = m.group(1).strip()
                if len(text) > 10:  # skip spuriously short matches
                    results.append(text)
        if results:
            return results
    return []


def _graph_snapshot(graph) -> str:
    """Compact cluster snapshot for the hypothesis prompt — entity states + drifts."""
    lines: list[str] = []
    for entity in graph.entities():
        kind = getattr(entity.kind, "value", entity.kind)
        ns   = entity.namespace or ""
        name = entity.name
        ann  = getattr(entity, "annotations", {}) or {}
        sigs: list[str] = []

        for cs in (getattr(entity, "container_statuses", None) or []):
            s = cs.get("state", "")
            if s in ("CrashLoopBackOff", "OOMKilled", "Error"):
                sigs.append(
                    f"container={cs.get('name','')} state={s} "
                    f"restarts={cs.get('restart_count','?')}"
                )

        phase = (
            ann.get("status.phase")
            or getattr(entity, "status_phase", None)
            or getattr(entity, "phase", None)
        )
        if phase in ("Pending", "Failed"):
            sigs.append(f"phase={phase}")

        if kind == "Deployment":
            r  = getattr(entity, "replicas", None)
            rr = getattr(entity, "ready_replicas", None)
            if r and rr is not None and int(rr) < int(r):
                sigs.append(f"degraded={rr}/{r} replicas ready")

        crits = [v for k, v in ann.items() if k.startswith("drift.") and "critical" in str(v)]
        if crits:
            sigs.append(f"critical_drifts={len(crits)}")

        anchor_keys = [
            k[len("anchor."):] for k, v in ann.items()
            if k.startswith("anchor.") and "[manifest]" in str(v)
        ]
        if anchor_keys:
            sigs.append(f"anchor_violations={','.join(anchor_keys[:3])}")

        if sigs:
            lines.append(f"  {kind} {ns}/{name}: {'; '.join(sigs)}")

    return "\n".join(lines) if lines else "  (no obvious unhealthy signals detected)"


def _build_hypothesis_context(graph, store, query: str) -> tuple[str, list[dict]]:
    """
    Build a pre-ranked evidence block to anchor hypothesis ordering.

    Uses deterministic rule-based scoring (RemediationEngine), active policy
    violations, manifest anchor drift, and FAISS example matches so the LLM
    ranks H1/H2/H3 based on ALL available signals — not just raw entity states.

    Returns:
        evidence_block  — formatted string injected into the hypothesize prompt
        rule_sources    — serialisable list of rule hits stored in state for UI
    """
    from ontology.entities import ResourceKind, PolicyViolation
    from rca.remediation_engine import RemediationEngine

    sections: list[str] = []
    rule_sources: list[dict] = []

    # ── 1. Rule-based diagnostics (deterministic, weight-ordered) ─────────────
    rule_hyps = RemediationEngine().score(graph)[:5]
    if rule_hyps:
        block = "DETERMINISTIC RULE EVIDENCE (ordered by diagnostic weight):\n"
        for h in rule_hyps:
            block += f"  [{h.weight:.2f}] {h.symptom}  →  {h.affected}\n"
            if h.evidence:
                block += f"         corroborated by: {', '.join(h.evidence[:2])}\n"
        sections.append(block)
        rule_sources = [
            {
                "rule_id":  h.rule_id,
                "symptom":  h.symptom,
                "affected": h.affected,
                "weight":   round(h.weight, 2),
                "evidence": h.evidence,
            }
            for h in rule_hyps
        ]

    # ── 2. Policy violations (OPA / Kyverno FAIL) ─────────────────────────────
    policy_fails = [
        e for e in graph.entities(ResourceKind.POLICY_VIOLATION)
        if isinstance(e, PolicyViolation) and e.is_fail
    ]
    if policy_fails:
        block = f"POLICY VIOLATIONS — {len(policy_fails)} FAIL rule(s):\n"
        for p in policy_fails[:3]:
            block += f"  - {p.to_text()[:120]}\n"
        sections.append(block)

    # ── 3. Anchor violations (manifest declared ≠ live observed) ──────────────
    anchor_lines: list[str] = []
    for entity in graph.entities():
        ann = getattr(entity, "annotations", {}) or {}
        for k, v in ann.items():
            if k.startswith("anchor.") and "[manifest]" in str(v):
                kind_str = getattr(entity.kind, "value", str(entity.kind))
                anchor_lines.append(
                    f"{kind_str}/{entity.namespace}/{entity.name}: "
                    f"{k[len('anchor.'):]}  (declared≠observed)"
                )
    if anchor_lines:
        block = f"ANCHOR VIOLATIONS — declared ≠ observed ({len(anchor_lines)}):\n"
        for a in anchor_lines[:5]:
            block += f"  - {a}\n"
        sections.append(block)

    # ── 4. Similar past incidents from knowledge base (FAISS examples) ─────────
    if store is not None:
        try:
            hits = store.hybrid_search(query, top_k=6)
            ex_hits = [h for h in hits if h["uid"].startswith("example:")][:2]
            if ex_hits:
                block = "SIMILAR RESOLVED INCIDENTS (knowledge base):\n"
                for h in ex_hits:
                    block += f"  (score={h['score']:.2f}) {h['text'][:200]}\n"
                sections.append(block)
        except Exception as exc:
            log.debug("_build_hypothesis_context: example search failed: %s", exc)

    # ── 5. Ontology causal chains (graph traversal — topology-aware) ───────────
    from dedup.bfs import find_unhealthy
    from ontology.relationships import RelationshipType

    _CAUSAL_RELS = {
        RelationshipType.USES_PVC:           "uses PVC",
        RelationshipType.BINDS_PV:           "binds PV",
        RelationshipType.MOUNTS_SECRET:      "mounts Secret",
        RelationshipType.MOUNTS_CONFIGMAP:   "mounts ConfigMap",
        RelationshipType.DRIFTS_FROM:        "drifts from HelmRelease",
        RelationshipType.MANAGED_BY_HELM:    "managed by HelmRelease",
        RelationshipType.HAS_ALERT:          "has firing alert",
        RelationshipType.HAS_TRACE:          "has error trace",
        RelationshipType.EXPOSES:            "exposed by Service",
        RelationshipType.USES_SERVICE_ACCOUNT: "uses ServiceAccount",
    }

    seeds = find_unhealthy(graph)
    chain_lines: list[str] = []
    for seed in seeds[:6]:
        kind_str = getattr(seed.kind, "value", str(seed.kind))
        state_str = (
            getattr(seed, "phase", "")
            or (seed.annotations or {}).get("status.phase", "unhealthy")
        )
        header = f"{kind_str}/{seed.namespace}/{seed.name} [{state_str}]"
        children: list[str] = []
        for rel_type, rel_label in _CAUSAL_RELS.items():
            neighbours = graph.neighbors(seed.uid, rel_type=rel_type)
            for nb in neighbours[:2]:
                nb_kind = getattr(nb.kind, "value", str(nb.kind))
                nb_phase = getattr(nb, "phase", "") or (nb.annotations or {}).get("status.phase", "")
                nb_state = f" [{nb_phase}]" if nb_phase else ""
                # Flag if the linked entity itself looks problematic
                flag = ""
                if nb_phase in ("Pending", "Failed", "CrashLoopBackOff"):
                    flag = " ← LIKELY CAUSE"
                elif nb_kind in ("Secret", "ConfigMap") and nb_phase == "":
                    flag = " ← check exists"
                children.append(
                    f"    → {rel_label} → {nb_kind}/{nb.namespace}/{nb.name}{nb_state}{flag}"
                )
        if children:
            chain_lines.append(header)
            chain_lines.extend(children)

    if chain_lines:
        block = "ONTOLOGY CAUSAL CHAINS (graph traversal):\n"
        for line in chain_lines:
            block += f"  {line}\n"
        sections.append(block)

    return "\n\n".join(sections), rule_sources


_ANCHOR_FIELD_HYPOTHESES: list[tuple[str, str]] = [
    ("spec.replicas",           "Replica count drift — deployment scaling mismatch"),
    (".image",                  "Container image drift — wrong tag deployed"),
    ("resources.limits.memory", "Memory resource drift — OOM/throttling risk"),
    ("resources.limits.cpu",    "CPU resource drift — throttling/starvation risk"),
    ("resources.requests",      "Resource request drift — scheduling affected"),
    ("spec.template",           "Pod template drift — rolling update may stall"),
    ("env.",                    "Environment variable drift — misconfiguration"),
    ("volumeMounts",            "Volume mount drift — storage misconfiguration"),
    ("containers.",             "Container spec drift — deployment mismatch"),
    ("serviceAccountName",      "ServiceAccount drift — RBAC/identity mismatch"),
    ("imagePullPolicy",         "Image pull policy drift — stale image may be used"),
    ("port",                    "Port configuration drift — traffic routing broken"),
]


def _anchor_hit_to_hypothesis(text: str) -> str | None:
    """Map an anchor violation search hit to a testable hypothesis string."""
    for field_fragment, hypothesis in _ANCHOR_FIELD_HYPOTHESES:
        if field_fragment in text:
            return hypothesis
    return None


def hypothesize_node(state: RCAState, config: RunnableConfig) -> dict:
    """
    Generate up to MAX_PATHS root-cause hypotheses ordered by probability.

    Retrieval-first approach — the LLM is only a last-resort filler:

      Phase 1 — KB examples (FAISS):  proven past resolutions, score > 0.55.
                  Hypothesis: field extracted directly → weight = score + 1.0
      Phase 2 — RemediationEngine:    deterministic rule hits, weight-ordered.
      Phase 3 — Ontology chains:      Pod→PVC Pending, Pod→Secret missing …
                  structural graph evidence, boosted if dependency is unhealthy.
      Phase 4 — LLM fallback:         only fills remaining slots if < MAX_PATHS
                  gathered from the above, using all evidence as context.

    H1 = highest-probability path, H2/H3 = fallbacks explored if H1 is LOW.
    """
    from dedup.bfs import find_unhealthy
    from ontology.relationships import RelationshipType
    from rca.remediation_engine import RemediationEngine

    graph, store = _get_infra(config)
    if graph is None:
        return {}

    query = state.get("query", "")

    # (weight, hypothesis_text, source_tag)
    pool: list[tuple[float, str, str]] = []

    # ── Phase 1 + 2b: KB examples + anchor violations (single hybrid_search) ────
    # example: UIDs → proven resolutions; anchor: UIDs → manifest drift hypotheses
    if store is not None:
        try:
            hits = store.hybrid_search(query, top_k=10)
            for h in hits:
                uid = h["uid"]
                if uid.startswith("example:"):
                    # cosine score preferred; rrf_score as fallback (different scale)
                    score = float(h.get("score", h.get("rrf_score", 0.0)))
                    if score < 0.55:
                        continue
                    for line in h["text"].splitlines():
                        if line.startswith("Hypothesis:"):
                            hyp_text = line[11:].strip()
                            if len(hyp_text) > 15:
                                pool.append((score + 1.0, hyp_text, "example"))
                            break
                elif uid.startswith("anchor:"):
                    # anchor hits are SOURCE_WEIGHTS-boosted (×1.6) by hybrid_search
                    hyp = _anchor_hit_to_hypothesis(h["text"])
                    if hyp:
                        pool.append((0.88, hyp, "anchor"))
        except Exception as exc:
            log.debug("hypothesize: KB/anchor search failed: %s", exc)

    # ── Phase 2: RemediationEngine (deterministic, weight-ordered) ─────────────
    rule_hyps = RemediationEngine().score(graph)
    rule_sources: list[dict] = []
    for h in rule_hyps[:5]:
        pool.append((h.weight, f"{h.symptom} affecting {h.affected}", "rule"))
        rule_sources.append({
            "rule_id":  h.rule_id,
            "symptom":  h.symptom,
            "affected": h.affected,
            "weight":   round(h.weight, 2),
            "evidence": h.evidence,
        })

    # ── Phase 3: Ontology causal chains (graph topology) ─────────────────────
    # Relationship → (human-readable label, base weight)
    _CAUSAL: list[tuple[RelationshipType, str, float]] = [
        (RelationshipType.USES_PVC,         "PVC dependency failure",           0.85),
        (RelationshipType.MOUNTS_SECRET,    "missing Secret dependency",        0.82),
        (RelationshipType.MOUNTS_CONFIGMAP, "missing ConfigMap dependency",     0.80),
        (RelationshipType.DRIFTS_FROM,      "Helm chart drift from declared",   0.78),
        (RelationshipType.EXPOSES,          "Service exposure broken",          0.65),
        (RelationshipType.USES_SERVICE_ACCOUNT, "ServiceAccount issue",         0.60),
    ]
    for seed in find_unhealthy(graph)[:4]:
        kind_str = getattr(seed.kind, "value", str(seed.kind))
        for rel_type, rel_desc, base_w in _CAUSAL:
            for nb in graph.neighbors(seed.uid, rel_type=rel_type):
                nb_kind  = getattr(nb.kind, "value", str(nb.kind))
                nb_phase = (
                    getattr(nb, "phase", "")
                    or (nb.annotations or {}).get("status.phase", "")
                )
                weight   = base_w + (0.10 if nb_phase in ("Pending", "Failed") else 0.0)
                suffix   = f" is {nb_phase}" if nb_phase else ""
                hyp = (
                    f"{rel_desc}: {kind_str}/{seed.name} "
                    f"→ {nb_kind}/{nb.name}{suffix}"
                )
                pool.append((weight, hyp, "ontology"))

    # ── Phase 2.5: Template catalog (community runbooks) ─────────────────────
    try:
        catalog = TemplateCatalog()
        for m in catalog.match(query, top_k=2):
            if m.score >= 0.25:
                hyp = f"{m.template.title}: {m.template.root_cause_pattern}"
                pool.append((0.70 + m.score * 0.30, hyp, "template"))
    except Exception as exc:
        log.debug("hypothesize: template_catalog failed: %s", exc)

    # ── Dedup + rank ──────────────────────────────────────────────────────────
    seen_hyps: set[str] = set()
    ordered: list[str] = []
    for _w, hyp, _src in sorted(pool, key=lambda x: x[0], reverse=True):
        if hyp not in seen_hyps and len(hyp) > 15:
            seen_hyps.add(hyp)
            ordered.append(hyp)

    # ── Phase 4: LLM fills remaining slots ────────────────────────────────────
    llm = _get_llm(config)
    if len(ordered) < MAX_PATHS:
        snapshot = _graph_snapshot(graph)
        evidence_block, _ = _build_hypothesis_context(graph, store, query)
        needed = MAX_PATHS - len(ordered)
        existing = "\n".join(f"  - {h}" for h in ordered) if ordered else "  (none yet)"

        prompt = (
            f"Kubernetes SRE expert. The following hypotheses were already identified "
            f"from deterministic evidence:\n{existing}\n\n"
            f"CLUSTER SNAPSHOT:\n{snapshot}\n\n"
            + (f"\n{evidence_block}\n\n" if evidence_block else "")
            + f"INCIDENT QUERY: {query}\n\n"
            f"Generate exactly {needed} ADDITIONAL distinct hypothesis(es) "
            f"not already listed above, in order of likelihood.\n"
            f"Reply with ONLY {needed} line(s), one per line, no numbering, no prefix.\n"
        )
        try:
            raw = llm.generate(prompt)
            for h in _parse_hypotheses(raw):
                if h not in seen_hyps and len(ordered) < MAX_PATHS:
                    seen_hyps.add(h)
                    ordered.append(h)
        except Exception as exc:
            log.warning("hypothesize: LLM fill-in failed (%s)", exc)

    if not ordered:
        log.info("hypothesize: no hypotheses from any source — single-path fallback")
        return {}

    final = ordered[:MAX_PATHS]
    first, *rest = final
    llm_used = len(pool) < MAX_PATHS
    log.info(
        "hypothesize: %d path(s) computed; sources: rules=%d pool=%d llm_fill=%s",
        len(final), len(rule_sources), len(pool), llm_used,
    )
    return {
        "current_hypothesis": first,
        "candidate_paths":    rest,
        "reasoning_history":  [],
        "hypothesis_sources": rule_sources,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Path archival — save current analysis, switch to next hypothesis
# ─────────────────────────────────────────────────────────────────────────────

def _rerank_candidates(
    candidates: list[str],
    failed_raw: str,
    store,
) -> list[str]:
    """
    Re-rank remaining candidate hypotheses using evidence from the failed path.

    Strategy: hybrid_search on the failed analysis text — surface anchor/example
    hits that correlate with the remaining candidates, boost the ones whose keywords
    appear in the top-k results.  Returns a new ordering (highest P first).
    """
    if not candidates or store is None or not failed_raw:
        return candidates

    try:
        hits = store.hybrid_search(failed_raw[:400], top_k=6)
        # Collect signal tokens from high-scoring hits
        signal_tokens: set[str] = set()
        for h in hits:
            for word in h["text"].lower().split():
                if len(word) > 4:
                    signal_tokens.add(word)

        def _score(hyp: str) -> float:
            words = set(hyp.lower().split())
            overlap = len(words & signal_tokens)
            return overlap

        ranked = sorted(candidates, key=_score, reverse=True)
        if ranked != candidates:
            log.info("archive_path: re-ranked %d candidates", len(ranked))
        return ranked
    except Exception as exc:
        log.debug("archive_path: re-rank failed (%s) — keeping original order", exc)
        return candidates


def archive_path_node(state: RCAState, config: RunnableConfig) -> dict:
    """
    Called when confidence is LOW and the path is abandoned.

    Archives the current analysis into reasoning_history, re-ranks the remaining
    candidate hypotheses using evidence from the failed analysis (signal tokens from
    anchor/example hits), then pops the new best candidate.

    Re-ranking implements the beam-search principle: after a path's probability
    declines, update the global probability estimate for remaining paths using
    the evidence accumulated so far — the next path chosen is the one with the
    highest posterior probability given all signals observed.
    """
    _, store   = _get_infra(config)
    history    = list(state.get("reasoning_history") or [])
    hypothesis = state.get("current_hypothesis") or ""
    report_dict = state.get("report_dict") or {}

    history.append({
        "step":        len(history) + 1,
        "hypothesis":  hypothesis,
        "confidence":  state.get("confidence", ""),
        "summary":     report_dict.get("summary", ""),
        "raw_analysis": state.get("raw_analysis", ""),
        "report_dict": report_dict,
        "retry_count": state.get("retry_count", 0),
    })

    # Re-rank remaining candidates using evidence from the failed path
    raw_analysis = state.get("raw_analysis", "")
    candidates = _rerank_candidates(
        list(state.get("candidate_paths") or []),
        failed_raw=raw_analysis,
        store=store,
    )
    next_hypothesis = candidates.pop(0) if candidates else ""

    log.info(
        "archive_path: step=%d conf=%s → %d remaining",
        len(history), state.get("confidence"), len(candidates),
    )

    return {
        "reasoning_history":      history,
        "candidate_paths":        candidates,
        "current_hypothesis":     next_hypothesis,
        "retry_count":            0,
        "path_confidence_history": [],   # reset per-path history for the new path
    }


# ─────────────────────────────────────────────────────────────────────────────
# Best-path selection — before human review
# ─────────────────────────────────────────────────────────────────────────────

def select_best_node(state: RCAState, config: RunnableConfig) -> dict:
    """
    Before surfacing the result to the human operator, compare all explored paths
    and restore the one with the highest confidence if it beats the current result.

    If reasoning_history is empty (single-path run), this is a no-op.
    """
    history = state.get("reasoning_history") or []
    if not history:
        return {}

    rank = {"HIGH": 3, "MEDIUM": 2, "LOW": 1, "": 0}
    current_rank = rank.get((state.get("confidence") or "").upper(), 0)

    best = max(history, key=lambda h: rank.get((h.get("confidence") or "").upper(), 0))
    best_rank = rank.get((best.get("confidence") or "").upper(), 0)

    if best_rank > current_rank:
        log.info(
            "select_best: restoring path %d (conf=%s) over current conf=%s",
            best["step"], best["confidence"], state.get("confidence"),
        )
        return {
            "raw_analysis":       best["raw_analysis"],
            "confidence":         best["confidence"],
            "report_dict":        best["report_dict"],
            "current_hypothesis": best["hypothesis"],
        }

    log.info("select_best: current path is already best (conf=%s)", state.get("confidence"))
    return {}


# ─────────────────────────────────────────────────────────────────────────────
# Analysis (LLM)
# ─────────────────────────────────────────────────────────────────────────────

def analyze_node(state: RCAState, config: RunnableConfig) -> dict:
    """
    Build context window and call Mistral.
    BFS depth is widened by 1 on each retry attempt.
    """
    graph, store = _get_infra(config)
    if graph is None or store is None:
        return {"error": "analyze_node: graph or store missing from config"}

    query      = state.get("query", "")
    hypothesis = state.get("current_hypothesis") or ""
    retry      = state.get("retry_count", 0)
    bfs_depth  = cfg.BFS_MAX_DEPTH + retry

    # Focus the LLM on the current hypothesis when one is available
    focused_query = (
        f"HYPOTHESIS TO INVESTIGATE:\n{hypothesis}\n\nINCIDENT QUERY:\n{query}"
        if hypothesis else query
    )

    ctx_builder = ContextBuilder(graph, store, bfs_max_depth=bfs_depth)
    llm = _get_llm(config)
    analyzer = RCAAnalyzer(graph=graph, store=store, llm=llm)
    analyzer._ctx_builder = ctx_builder

    report = analyzer.analyze(focused_query)
    confidence = (report.confidence or "").strip().upper().split()[0] if report.confidence else ""

    log.info(
        "analyze: confidence=%s  retry=%d  hypothesis='%s'  chunks=%d",
        confidence, retry, (hypothesis or "—")[:60], report.context.total_chunks,
    )
    return {
        "raw_analysis": report.raw_analysis,
        "report_dict":  report.to_dict(),
        "confidence":   confidence,
        "kube_version": report.kube_version,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Human review (interrupt point)
# ─────────────────────────────────────────────────────────────────────────────

def human_review_node(state: RCAState, config: RunnableConfig) -> dict:
    """
    Pause execution and surface the report to a human operator.

    Raises a LangGraph Interrupt — caller resumes with:
        Command(resume="approve")  or  Command(resume="reject")

    Interrupt payload (what the human sees):
        summary, root_cause, remediation commands, confidence
    """
    report_dict  = state.get("report_dict") or {}
    history      = state.get("reasoning_history") or []
    confidence   = state.get("confidence", "")
    remediation  = report_dict.get("remediation") or []
    root_cause   = report_dict.get("root_cause", "")

    # No actionable solution: all paths exhausted with LOW confidence and/or
    # no remediation commands — operator needs to know before seeing the gate.
    no_solution = not remediation or (
        (confidence or "").upper() == "LOW" and not root_cause
    )

    payload = {
        "summary":            report_dict.get("summary", ""),
        "root_cause":         root_cause,
        "remediation":        remediation,
        "confidence":         confidence,
        "kube_version":       state.get("kube_version", ""),
        "current_hypothesis": state.get("current_hypothesis") or "",
        "reasoning_history":  history,
        "paths_explored":     len(history) + 1,
        "blast_radius":       state.get("blast_radius") or {},
        "dry_run_results":    state.get("dry_run_results") or [],
        "example_match":      state.get("example_match") or False,
        "matched_example_id": state.get("matched_example_id") or "",
        "no_solution":        no_solution,
        "edge_log":           list(state.get("edge_log") or []),
        "hypothesis_sources": state.get("hypothesis_sources") or [],
    }

    if config.get("configurable", {}).get("auto_approve"):
        log.info("human_review: auto-approve mode — skipping interrupt")
        return {"human_decision": "approve"}

    decision: str = interrupt(payload)

    normalised = (decision or "").strip().lower()
    if normalised not in ("approve", "reject"):
        normalised = "reject"

    log.info("human_review: decision=%s", normalised)
    return {"human_decision": normalised}


# ─────────────────────────────────────────────────────────────────────────────
# Dry-run validation
# ─────────────────────────────────────────────────────────────────────────────

def _helm_values_diff(
    release: str,
    ns_flags: list[str],
    set_flags: list[str],
) -> tuple[str, str, int]:
    """
    Show a clean before/after values diff for a helm upgrade when the chart
    path is unknown and the helm-diff plugin is not installed.

    Gets current values via `helm get values`, applies proposed --set overrides,
    then renders the diff as plain text.
    """
    import subprocess
    import json

    get_cmd = ["helm", "get", "values", release, "-o", "json"] + ns_flags
    try:
        r = subprocess.run(get_cmd, capture_output=True, text=True, timeout=15)
        current: dict = json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else {}
    except Exception:
        current = {}

    # Parse proposed overrides from --set flags (format: key=value or a.b.c=value)
    proposed: dict = {}
    i = 0
    while i < len(set_flags):
        if set_flags[i] == "--set" and i + 1 < len(set_flags):
            for pair in set_flags[i + 1].split(","):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    proposed[k.strip()] = v.strip()
            i += 2
        else:
            i += 1

    if not proposed:
        return " ".join(get_cmd), "[no --set overrides to evaluate]", 0

    # Build diff lines: unchanged keys are omitted for clarity
    lines = [f"helm values diff for release '{release}'", ""]
    for key, new_val in sorted(proposed.items()):
        old_val = _nested_get(current, key)
        if str(old_val) == str(new_val):
            lines.append(f"  {key}: {new_val}  (unchanged)")
        else:
            lines.append(f"- {key}: {old_val}")
            lines.append(f"+ {key}: {new_val}")

    display_cmd = f"helm get values {release} {' '.join(ns_flags)}  →  apply: {' '.join(set_flags)}"
    return display_cmd, "\n".join(lines), 0


def _nested_get(d: dict, dotted_key: str):
    """Traverse a nested dict with a dotted key path; return None if missing."""
    parts = dotted_key.split(".")
    cur = d
    for p in parts:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


def _exec_dry_run(cmd: str) -> tuple[str, str, int]:
    """
    Transform one remediation command into its dry-run equivalent and execute it.
    Returns (dry_run_command, output, exit_code).

    Strategy:
      helm upgrade/install → try `helm diff upgrade --reuse-values` first,
                              fall back to `helm upgrade --dry-run --reuse-values`
      kubectl *            → append `--dry-run=server`
      piped / heredoc      → not supported (shlex cannot parse them safely)
      other tools          → returned unchanged with an explanatory note
    """
    import subprocess
    import shlex

    # Reject shell constructs shlex cannot safely parse
    if any(tok in cmd for tok in ("<<", "&&", "||", "|", ";")):
        return cmd, "[dry-run skipped: shell construct — run manually to evaluate]", 0

    try:
        parts = shlex.split(cmd)
    except ValueError as exc:
        return cmd, f"[parse error: {exc}]", 1

    if not parts:
        return cmd, "", 0

    tool = parts[0]

    # ── helm upgrade / install ────────────────────────────────────────────────
    if tool == "helm" and len(parts) > 1 and parts[1] in ("upgrade", "install"):
        release = parts[2] if len(parts) > 2 else ""

        # Collect -n / --namespace value
        ns_flags: list[str] = []
        set_flags: list[str] = []
        i = 3
        while i < len(parts):
            p = parts[i]
            if p in ("-n", "--namespace") and i + 1 < len(parts):
                ns_flags += [p, parts[i + 1]]
                i += 2
            elif p in ("--set", "--set-string", "--set-file",
                       "-f", "--values") and i + 1 < len(parts):
                set_flags += [p, parts[i + 1]]
                i += 2
            else:
                i += 1

        # Try helm diff plugin first — shows a clean YAML diff without needing the chart
        diff_cmd = ["helm", "diff", "upgrade", release,
                    "--reuse-values", "--allow-unreleased"] + ns_flags + set_flags
        try:
            r = subprocess.run(diff_cmd, capture_output=True, text=True, timeout=30)
            out = r.stdout.strip()
            if r.returncode == 0:
                return " ".join(diff_cmd), out or "[no diff — values unchanged]", 0
            if out:   # diff found but exit != 0 means there ARE changes
                return " ".join(diff_cmd), out, r.returncode
        except FileNotFoundError:
            pass      # helm diff plugin not installed → fall through
        except subprocess.TimeoutExpired:
            return " ".join(diff_cmd), "[timeout after 30 s]", 1

        # helm diff not available → show a values diff instead:
        # current values (from cluster) vs proposed values (from --set flags)
        return _helm_values_diff(release, ns_flags, set_flags)

    # ── kubectl ───────────────────────────────────────────────────────────────
    elif tool == "kubectl":
        dry_parts = parts.copy()
        if "--dry-run" not in cmd:
            dry_parts.append("--dry-run=server")
        try:
            r = subprocess.run(dry_parts, capture_output=True, text=True, timeout=30)
            output = r.stdout or r.stderr or "[no output]"
            return " ".join(dry_parts), output.strip(), r.returncode
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            return " ".join(dry_parts), f"[error: {exc}]", 1

    else:
        return cmd, f"[dry-run not supported for '{tool}' — review manually]", 0



def blast_radius_node(state: RCAState, config: RunnableConfig) -> dict:
    """
    Compute the blast radius of proposed remediation commands before dry-run.
    Delegates to remediation.blast_radius.compute_blast_radius.
    """
    report      = state.get("report_dict") or {}
    remediation = report.get("remediation") or []
    affected    = report.get("affected") or []
    rollback    = report.get("rollback") or _generate_rollback(remediation)

    br = compute_blast_radius(remediation, affected, rollback)
    log.info("blast_radius: risk=%s  %s", br["risk"], br["summary"])
    return {"blast_radius": br}


def monte_carlo_node(state: RCAState, config: RunnableConfig) -> dict:
    """
    Run 200 Monte Carlo simulations on the pre-LLM confidence score to assess
    stability.  A win_rate < 0.80 forces HUMAN_REVIEW even when score is HIGH.
    """
    report  = state.get("report_dict") or {}
    pre_llm = report.get("pre_llm_confidence") or {}
    score   = float(pre_llm.get("score") if pre_llm.get("score") is not None else 0.5)

    mc = run_monte_carlo(score)
    log.info(
        "monte_carlo: score=%.2f  win_rate=%.0f%%  stable=%s",
        score, mc.win_rate * 100, mc.is_stable,
    )
    return {
        "mc_result": {
            "win_rate":      mc.win_rate,
            "mean_score":    mc.mean_score,
            "std_score":     mc.std_score,
            "is_stable":     mc.is_stable,
            "n_simulations": mc.n_simulations,
        }
    }


_DECISION_ENGINE = DecisionEngine()


def log_policy_decision_node(state: RCAState) -> dict:
    """
    Pre-router node: build the canonical IncidentReport, run the DecisionEngine,
    log the decision, and write _verdict_edge so verdict_router stays a pure reader.

    Score source: the LLM diagnosis confidence label (HIGH/MEDIUM/LOW/""), mapped
    to a gate score by the DecisionEngine.
    """
    report = IncidentReport.from_report_dict(state.get("report_dict"))
    # state["confidence"] is the authoritative LLM label for this run.
    report.confidence = state.get("confidence") or report.confidence

    br           = state.get("blast_radius") or {}
    risk         = br.get("risk", "HIGH")
    rollback_ok  = bool(br.get("rollback_available", False))
    # Use the first namespace touched by the remediation commands (from blast_radius);
    # fall back to the workflow-level namespace list if blast_radius didn't extract any.
    br_namespaces = br.get("namespaces") or []
    namespace = br_namespaces[0] if br_namespaces else (state.get("namespaces") or [""])[0]
    mc           = state.get("mc_result") or {}
    mc_win_rate  = float(mc.get("win_rate", 1.0))
    max_hit      = bool(state.get("max_switches_reached", False))

    result = _DECISION_ENGINE.decide(
        report,
        risk=risk,
        rollback_available=rollback_ok,
        namespace=namespace,
        mc_win_rate=mc_win_rate,
        max_switches_reached=max_hit,
    )
    verdict = result.verdict.value  # "AUTO" | "HUMAN_REVIEW" | "NO_GO"
    edge    = result.edge           # "auto" | "human_review" | "no_go"

    entry = {
        "router":     "policy",
        "edge_taken":  edge,
        "reason":     f"{verdict}: {'; '.join(result.reasons)}",
        "snapshot": {
            "score":              result.score,
            "risk":               risk,
            "rollback_available": rollback_ok,
            "namespace":          namespace,
            "mc_win_rate":        mc_win_rate,
            "max_switches_reached": max_hit,
            "verdict":            verdict,
        },
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    log.info("policy_gate → %s: %s", edge, "; ".join(result.reasons))
    edge_log = list(state.get("edge_log") or [])
    edge_log.append(entry)
    return {
        "edge_log":       edge_log,
        "verdict":        verdict,
        "verdict_reasons": result.reasons,
        "_verdict_edge":  edge,
    }


def verdict_router(state: RCAState) -> str:
    """Reads the pre-computed edge written by log_policy_decision_node."""
    return state.get("_verdict_edge") or "human_review"


def dry_run_node(state: RCAState, config: RunnableConfig) -> dict:
    """
    Execute each proposed remediation command in dry-run mode before the human
    review gate.  Results are surfaced in the interrupt payload so the operator
    can evaluate the exact changes that will be applied before approving.

    Fails silently on every error — a missing tool or a network blip must
    never block the workflow.
    """
    remediation = (state.get("report_dict") or {}).get("remediation", [])
    if not remediation:
        return {}

    results: list[dict] = []
    for cmd in remediation:
        dry_cmd, output, rc = _exec_dry_run(cmd)
        results.append({
            "original_cmd": cmd,
            "dry_cmd":      dry_cmd,
            "output":       output,
            "exit_code":    rc,
        })
        log.info("dry_run: exit=%d  %s", rc, cmd[:80])

    return {"dry_run_results": results}


# ─────────────────────────────────────────────────────────────────────────────
# Remediation
# ─────────────────────────────────────────────────────────────────────────────

def remediation_node(state: RCAState, config: RunnableConfig) -> dict:
    """Log the approved remediation commands (wire to kubectl executor if needed)."""
    commands = (state.get("report_dict") or {}).get("remediation", [])
    if commands:
        log.info("remediation: %d command(s) approved", len(commands))
        for cmd in commands:
            log.info("  $ %s", cmd)
    return {}


# ─────────────────────────────────────────────────────────────────────────────
# Routing
# ─────────────────────────────────────────────────────────────────────────────

def _extract_example_field(text: str, field_name: str) -> str:
    for line in text.splitlines():
        if line.startswith(f"{field_name}:"):
            return line[len(f"{field_name}:"):].strip()
    return ""


def example_lookup_node(state: RCAState, config: RunnableConfig) -> dict:
    """
    Search the FAISS store for resolved incidents similar to the current query.
    If cosine similarity ≥ EXAMPLE_MATCH_THRESHOLD the example's known fix is
    loaded into state and example_match=True is set, allowing the graph router
    to skip the full analyze loop and go straight to select_best.
    """
    # Reproducible captures / benchmarks force a fresh analysis every run instead
    # of short-circuiting on a previously-saved example (off by default — the
    # example cache is a feature in production).
    if os.getenv("EXAMPLE_LOOKUP_DISABLED", "").lower() in ("1", "true", "yes"):
        return {}

    _, store = _get_infra(config)
    if store is None:
        return {}

    query = state.get("current_hypothesis") or state.get("query", "")
    if not query:
        return {}

    hits = store.search(query, top_k=20)
    example_hits = [h for h in hits if h["uid"].startswith("example:")]
    if not example_hits or example_hits[0]["score"] < EXAMPLE_MATCH_THRESHOLD:
        return {}

    best = example_hits[0]
    log.info(
        "example_lookup: match uid=%s score=%.3f — short-circuiting analyze loop",
        best["uid"], best["score"],
    )

    text = best["text"]
    root_cause    = _extract_example_field(text, "Root cause")
    hypothesis    = _extract_example_field(text, "Hypothesis")
    remediation_s = _extract_example_field(text, "Fix")
    confidence    = (_extract_example_field(text, "Confidence") or "HIGH").upper()
    remediation   = [r.strip() for r in remediation_s.split(";") if r.strip()]

    report_dict = (state.get("report_dict") or {}).copy()
    report_dict.update({
        "root_cause":   root_cause or "Matched from example store",
        "summary":      f"[Example match {best['score']:.2f}] {root_cause or ''}",
        "remediation":  remediation,
        "confidence":   confidence,
    })

    return {
        "example_match":       True,
        "matched_example_id":  best["uid"],
        "confidence":          confidence,
        "current_hypothesis":  hypothesis or query,
        "report_dict":         report_dict,
    }


def save_example_node(state: RCAState, config: RunnableConfig) -> dict:
    """
    Persist the resolved incident to ExampleStore and index it into FAISS so
    future similar incidents can be matched directly.
    Called automatically after a human-approved remediation.
    """
    from knowledge.example_store import ExampleIndexer, ExampleStore, ResolvedIncident
    from dedup.bfs import find_unhealthy
    from rca.context_builder import anchor_fix_hints

    graph, store = _get_infra(config)

    report = IncidentReport.from_report_dict(state.get("report_dict"))
    # state holds the authoritative LLM label and query for this run.
    report.confidence = state.get("confidence") or report.confidence
    report.query = state.get("query") or report.query

    # Anchor violations from unhealthy entities (re-computed, cheap)
    anchor_violations: list[str] = []
    if graph:
        for hint in anchor_fix_hints(graph, find_unhealthy(graph)):
            parts = hint.split("→")
            if parts:
                anchor_violations.append(parts[0].strip())

    incident = ResolvedIncident.from_report(
        report,
        hypothesis=state.get("current_hypothesis", ""),
        anchor_violations=anchor_violations,
    )

    ExampleStore().save(incident)
    log.info("save_example: incident %s saved", incident.id)

    if store is not None:
        ExampleIndexer(store).index_example(incident)
        log.info("save_example: incident %s indexed into FAISS", incident.id)

    return _stats(state, "save_example", {"id": incident.id})


def example_router(state: RCAState) -> str:
    """Route to select_best (skip analyze) when a strong example match was found."""
    return "skip" if state.get("example_match") else "analyze"


def _ingestion_failures(state: RCAState) -> list[str]:
    """Return names of collectors that recorded fallback=True."""
    stats = state.get("ingestion_stats") or {}
    return [step for step, s in stats.items() if isinstance(s, dict) and s.get("fallback")]


def log_confidence_decision_node(state: RCAState) -> dict:
    """
    Pre-router node: evaluates the confidence routing logic, writes the
    decision and its full rationale into edge_log, then stores the chosen
    edge in _confidence_edge so confidence_router stays a pure reader.

    Probability-aware early switching:
      If confidence is LOW for >= 2 consecutive retries on the same path,
      the path probability is declining — switch to the next hypothesis
      immediately rather than exhausting max_retries on a dead end.
      This implements a beam-search-like strategy: abandon stagnant paths
      early to reallocate the retry budget to more promising candidates.
    """
    confidence      = (state.get("confidence") or "").upper()
    retry           = state.get("retry_count", 0)
    candidates      = list(state.get("candidate_paths") or [])
    failures        = _ingestion_failures(state)
    conf_history    = list(state.get("path_confidence_history") or [])
    beam_switches   = state.get("beam_switches_used") or 0

    # Append current confidence to per-path history
    conf_history.append(confidence)

    # beam_search.should_switch_path: stagnant if LOW×2 OR regressed (same impl).
    # Also respect MAX_SWITCHES: once exhausted, stop switching.
    beam_max_hit = beam_switches >= MAX_SWITCHES
    declining = (
        confidence == "LOW"
        and should_switch_path(conf_history)
        and not beam_max_hit
    )

    if confidence == "LOW":
        if declining and candidates:
            edge   = "next_path"
            reason = (
                f"probability declining — LOW×{conf_history.count('LOW')} on this path "
                f"(retry {retry}), switching to next hypothesis ({len(candidates)} remaining)"
                + (f"; ingestion failures: {failures}" if failures else "")
            )
        elif declining and not candidates:
            edge   = "review"
            reason = (
                f"probability declining — LOW×{conf_history.count('LOW')} and no more "
                f"candidates — escalating to human review"
                + (f"; ingestion failures: {failures}" if failures else "")
            )
        elif retry < MAX_RETRIES:
            edge   = "retry"
            reason = (
                f"confidence=LOW — retrying with wider context "
                f"({retry + 1}/{MAX_RETRIES})"
                + (f"; ingestion failures: {failures}" if failures else "")
            )
        elif candidates:
            edge   = "next_path"
            reason = (
                f"confidence=LOW — retries exhausted ({retry}/{MAX_RETRIES}), "
                f"switching to next hypothesis ({len(candidates)} remaining)"
                + (f"; ingestion failures: {failures}" if failures else "")
            )
        else:
            edge   = "review"
            reason = (
                f"confidence=LOW — retries exhausted ({retry}/{MAX_RETRIES}), "
                f"no more candidates — escalating to human review"
                + (f"; ingestion failures: {failures}" if failures else "")
            )
    else:
        edge   = "review"
        reason = f"confidence={confidence or 'UNKNOWN'} — forwarding to human review"

    new_beam_switches = beam_switches + (1 if edge == "next_path" else 0)
    entry = {
        "router":    "confidence",
        "edge_taken": edge,
        "reason":    reason,
        "snapshot": {
            "confidence":            confidence,
            "retry_count":           retry,
            "max_retries":           MAX_RETRIES,
            "candidates_remaining":  len(candidates),
            "ingestion_failures":    failures,
            "path_conf_history":     list(conf_history),
            "declining":             declining,
            "beam_switches":         new_beam_switches,
        },
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    log.info(
        "confidence_router → %s%s: %s",
        edge, " [early-switch]" if declining else "", reason,
    )
    edge_log = list(state.get("edge_log") or [])
    edge_log.append(entry)
    return {
        "edge_log":               edge_log,
        "_confidence_edge":       edge,
        "path_confidence_history": conf_history,
        "beam_switches_used":     new_beam_switches,
        "max_switches_reached":   new_beam_switches >= MAX_SWITCHES,
    }


def confidence_router(state: RCAState) -> str:
    """Reads the pre-computed edge written by log_confidence_decision_node."""
    return state.get("_confidence_edge") or "review"


def log_human_decision_node(state: RCAState) -> dict:
    """
    Pre-router node: evaluates the human routing logic, writes the decision
    and rationale into edge_log, stores the edge in _human_edge.
    """
    decision    = (state.get("human_decision") or "").strip().lower()
    report_dict = state.get("report_dict") or {}
    remediation = report_dict.get("remediation") or []
    confidence  = state.get("confidence", "")
    dry_runs    = state.get("dry_run_results") or []

    failed_dry  = [r for r in dry_runs if r.get("exit_code", 0) != 0]

    if decision == "approve":
        edge   = "approve"
        reason = (
            f"operator approved {len(remediation)} remediation command(s)"
            + (f"; {len(failed_dry)} dry-run warning(s)" if failed_dry else "")
        )
    else:
        edge = "reject"
        if not decision:
            reason = "no human decision received — defaulting to reject"
        elif failed_dry:
            reason = (
                f"operator rejected — {len(failed_dry)}/{len(dry_runs)} "
                f"dry-run(s) failed: "
                + "; ".join(r.get("dry_cmd", "?")[:80] for r in failed_dry[:3])
            )
        else:
            reason = f"operator rejected (confidence={confidence})"

    entry = {
        "router":     "human",
        "edge_taken":  edge,
        "reason":     reason,
        "snapshot": {
            "human_decision":     decision or "(none)",
            "confidence":         confidence,
            "remediation_count":  len(remediation),
            "dry_run_failures":   len(failed_dry),
        },
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    log.info("human_router → %s: %s", edge, reason)
    edge_log = list(state.get("edge_log") or [])
    edge_log.append(entry)
    return {"edge_log": edge_log, "_human_edge": edge}


def human_router(state: RCAState) -> str:
    """Reads the pre-computed edge written by log_human_decision_node."""
    return state.get("_human_edge") or "reject"
