"""
LangGraph node functions for the KubeWhisperer RCA workflow.

Heavy objects (OntologyGraph, FAISSStore) are NOT stored in state — they are
passed via config["configurable"] so the MemorySaver checkpointer never tries
to serialise them.  Nodes that need them call _get_infra(config).
"""
from __future__ import annotations
import logging
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

import config as cfg
from llm.ollama_client import OllamaClient
from rca.analyzer import RCAAnalyzer
from rca.context_builder import ContextBuilder
from vectorstore.embedder import Embedder
from vectorstore.store import FAISSStore
from workflow.state import RCAState

log = logging.getLogger(__name__)

MAX_RETRIES = 2


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_infra(config: RunnableConfig) -> tuple[Any, Any]:
    """Pull graph and store from config["configurable"] (never from state)."""
    c = config.get("configurable", {})
    return c.get("graph"), c.get("store")


def _get_llm(config: RunnableConfig) -> OllamaClient:
    """Return injected LLM (tests) or a fresh OllamaClient."""
    return config.get("configurable", {}).get("llm") or OllamaClient()


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
        return {}

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
        helm.collect(built_graph, namespaces=state.get("namespaces") or None)

        if cfg.HELMFILE_PATH:
            from ingestion import HelmfileCollector
            hf = HelmfileCollector(
                helmfile_path=cfg.HELMFILE_PATH,
                environment=cfg.HELMFILE_ENVIRONMENT,
                use_cli=cfg.HELMFILE_USE_CLI,
            )
            hf.collect(built_graph)

        HelmDriftDetector().detect_all(built_graph)
        log.info("ingest: %s", built_graph.summary())

        # Store in config so subsequent nodes can access it
        config.setdefault("configurable", {})["graph"] = built_graph
        return {}

    except Exception as exc:
        log.error("ingest failed: %s", exc)
        return {"error": str(exc)}


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
        return {}

    graph, _ = _get_infra(config)
    if graph is None:
        log.info("prometheus: no graph — skipping")
        return {}

    try:
        from ingestion.prometheus_collector import PrometheusCollector
        collector = PrometheusCollector(
            url=cfg.PROMETHEUS_URL,
            token=cfg.PROMETHEUS_TOKEN,
            timeout=cfg.PROMETHEUS_TIMEOUT,
        )
        count = collector.collect(graph)
        log.info("prometheus: %d alert(s) correlated", count)
    except Exception as exc:
        log.warning("prometheus node failed (%s) — continuing without alert data", exc)

    return {}


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
        return {}

    graph, _ = _get_infra(config)
    if graph is None:
        log.info("metrics-server: no graph — skipping")
        return {}

    try:
        from ingestion.metrics_server_collector import MetricsServerCollector
        collector = MetricsServerCollector(
            kubeconfig=state.get("kubeconfig") or cfg.KUBECONFIG,
            context=state.get("kube_context") or cfg.KUBE_CONTEXT,
        )
        count = collector.collect(graph)
        log.info("metrics-server: %d pod(s) annotated", count)
    except Exception as exc:
        log.warning("metrics-server node failed (%s) — continuing without metrics data", exc)

    return {}


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
        return {}

    graph, _ = _get_infra(config)
    if graph is None:
        log.info("otel: no graph — skipping")
        return {}

    if cfg.OTEL_ENABLED:
        try:
            from ingestion.otel_backend import build_backend
            from ingestion.otel_collector import OtelCollector
            backend = build_backend(
                cfg.OTEL_BACKEND_TYPE,
                cfg.OTEL_BACKEND_URL,
                cfg.OTEL_TOKEN,
                cfg.OTEL_TIMEOUT,
            )
            count = OtelCollector(backend, lookback_hours=cfg.OTEL_LOOKBACK_HOURS).collect(graph)
            log.info("otel: %d trace(s) ingested", count)
        except Exception as exc:
            log.warning("otel traces failed (%s) — continuing without trace data", exc)

    if cfg.LOKI_ENABLED:
        try:
            from ingestion.loki_source import LokiSource
            loki = LokiSource(
                url=cfg.LOKI_URL,
                token=cfg.LOKI_TOKEN,
                timeout=cfg.LOKI_TIMEOUT,
                lookback_hours=cfg.LOKI_LOOKBACK_HOURS,
                max_logs_per_pod=cfg.LOKI_MAX_LOGS_PER_POD,
            )
            count = loki.collect(graph)
            log.info("loki: %d log(s) ingested", count)
        except Exception as exc:
            log.warning("loki logs failed (%s) — continuing without log data", exc)

    return {}


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
        return {}

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
    except Exception as exc:
        log.warning("gitops node failed (%s) — continuing without gitops data", exc)

    return {}


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
    built_store.save()
    log.info("index: %d vectors", built_store.size)

    config.setdefault("configurable", {})["store"] = built_store
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
    except Exception as exc:
        log.warning("signal_analysis failed (%s) — continuing without signal data", exc)

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

    query = state.get("query", "")
    retry = state.get("retry_count", 0)
    bfs_depth = cfg.BFS_MAX_DEPTH + retry

    ctx_builder = ContextBuilder(graph, store, bfs_max_depth=bfs_depth)
    llm = _get_llm(config)
    analyzer = RCAAnalyzer(graph=graph, store=store, llm=llm)
    analyzer._ctx_builder = ctx_builder

    report = analyzer.analyze(query)
    confidence = (report.confidence or "").strip().upper().split()[0] if report.confidence else ""

    log.info(
        "analyze: confidence=%s  retry=%d  chunks=%d",
        confidence, retry, report.context.total_chunks,
    )
    return {
        "raw_analysis": report.raw_analysis,
        "report_dict": report.to_dict(),
        "confidence": confidence,
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
    report_dict = state.get("report_dict") or {}
    payload = {
        "summary":     report_dict.get("summary", ""),
        "root_cause":  report_dict.get("root_cause", ""),
        "remediation": report_dict.get("remediation", []),
        "confidence":  state.get("confidence", ""),
        "kube_version": state.get("kube_version", ""),
    }

    decision: str = interrupt(payload)

    normalised = (decision or "").strip().lower()
    if normalised not in ("approve", "reject"):
        normalised = "reject"

    log.info("human_review: decision=%s", normalised)
    return {"human_decision": normalised}


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

def confidence_router(state: RCAState) -> str:
    """LOW confidence + retries remaining → retry; otherwise → human review."""
    confidence = (state.get("confidence") or "").upper()
    retry = state.get("retry_count", 0)
    if confidence == "LOW" and retry < MAX_RETRIES:
        return "retry"
    return "review"


def human_router(state: RCAState) -> str:
    """approve → remediation; anything else → end."""
    return state.get("human_decision") or "reject"
