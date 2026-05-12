"""
LangGraph node functions for the KubeWhisperer RCA workflow.

Heavy objects (OntologyGraph, FAISSStore) are NOT stored in state — they are
passed via config["configurable"] so the MemorySaver checkpointer never tries
to serialise them.  Nodes that need them call _get_infra(config).
"""
from __future__ import annotations
import logging
import re
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
MAX_PATHS   = 3     # max distinct hypotheses to explore per run
EXAMPLE_MATCH_THRESHOLD = 0.65   # cosine similarity (IndexFlatIP, L2-normalised, all-MiniLM-L6-v2)


def _stats(state: RCAState, step: str, data: dict) -> dict:
    """Merge per-step telemetry into ingestion_stats without overwriting other steps."""
    current = dict(state.get("ingestion_stats") or {})
    current[step] = data
    return {"ingestion_stats": current}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_infra(config: RunnableConfig) -> tuple[Any, Any]:
    """Pull graph and store from config["configurable"] (never from state)."""
    c = config.get("configurable", {})
    return c.get("graph"), c.get("store")


def _get_provider(config: RunnableConfig):
    """Return the GitProvider stored by gitops_node, or None."""
    return config.get("configurable", {}).get("provider")


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

        config.setdefault("configurable", {})["graph"] = built_graph
        return _stats(state, "ingest", {
            "entities": built_graph.node_count(),
            "edges": built_graph.edge_count(),
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
        return _stats(state, "gitops", {
            "drifts": len(drifts), "critical": critical, "fallback": False,
        })

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
    """Compact cluster snapshot for the hypothesis prompt — no BFS, no FAISS."""
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


def hypothesize_node(state: RCAState, config: RunnableConfig) -> dict:
    """
    Ask the LLM to generate up to MAX_PATHS distinct root-cause hypotheses from
    the cluster evidence (container states, phases, drifts).  Each hypothesis
    becomes a focused BFS+analysis path — if one path is a dead end (LOW confidence
    after retries), the workflow automatically explores the next.

    Falls back to single-path (empty return) when the LLM is unavailable or
    returns an unparseable response.
    """
    graph, _ = _get_infra(config)
    if graph is None:
        return {}

    llm      = _get_llm(config)
    query    = state.get("query", "")
    snapshot = _graph_snapshot(graph)

    prompt = (
        f"You are a Kubernetes SRE expert. Given the cluster snapshot below, "
        f"generate exactly {MAX_PATHS} distinct root-cause hypotheses to investigate, "
        f"ordered by likelihood.\n\n"
        f"CLUSTER SNAPSHOT:\n{snapshot}\n\n"
        f"INCIDENT QUERY: {query}\n\n"
        f"Reply with ONLY {MAX_PATHS} lines in this exact format "
        f"(no extra text, no numbering beyond H1/H2/H3):\n"
        f"H1: <concise testable hypothesis>\n"
        f"H2: <concise testable hypothesis>\n"
        f"H3: <concise testable hypothesis>\n\n"
        f"Each hypothesis must focus on a distinct, specific failure mode."
    )

    try:
        raw = llm.generate(prompt)
        hypotheses: list[str] = _parse_hypotheses(raw)

        if not hypotheses:
            log.info("hypothesize: no hypotheses parsed — single-path fallback")
            return {}

        candidates = hypotheses[:MAX_PATHS]
        first, *rest = candidates
        log.info("hypothesize: %d path(s) — leading with: %s", len(candidates), first)
        return {
            "current_hypothesis": first,
            "candidate_paths":    rest,
            "reasoning_history":  [],
        }
    except Exception as exc:
        log.warning("hypothesize: LLM call failed (%s) — single-path fallback", exc)
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# Path archival — save current analysis, switch to next hypothesis
# ─────────────────────────────────────────────────────────────────────────────

def archive_path_node(state: RCAState, config: RunnableConfig) -> dict:
    """
    Called when confidence is LOW and retries are exhausted but other hypotheses remain.

    Archives the current analysis into reasoning_history, pops the next candidate
    hypothesis, and resets retry_count so the next path gets a fresh retry budget.
    """
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

    candidates     = list(state.get("candidate_paths") or [])
    next_hypothesis = candidates.pop(0) if candidates else ""

    log.info(
        "archive_path: step=%d archived '%s' (conf=%s) → next '%s' (%d remaining)",
        len(history), hypothesis[:60], state.get("confidence"),
        next_hypothesis[:60], len(candidates),
    )

    return {
        "reasoning_history":  history,
        "candidate_paths":    candidates,
        "current_hypothesis": next_hypothesis,
        "retry_count":        0,
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
            "select_best: restoring path %d '%s' (conf=%s) over current conf=%s",
            best["step"], best["hypothesis"][:60], best["confidence"], state.get("confidence"),
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
        "dry_run_results":    state.get("dry_run_results") or [],
        "example_match":      state.get("example_match") or False,
        "matched_example_id": state.get("matched_example_id") or "",
        "no_solution":        no_solution,
    }

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
    import subprocess, json

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
                ns_flags += [p, parts[i + 1]]; i += 2
            elif p in ("--set", "--set-string", "--set-file",
                       "-f", "--values") and i + 1 < len(parts):
                set_flags += [p, parts[i + 1]]; i += 2
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
    report = state.get("report_dict") or {}

    # Anchor violations from unhealthy entities (re-computed, cheap)
    anchor_violations: list[str] = []
    if graph:
        for hint in anchor_fix_hints(graph, find_unhealthy(graph)):
            parts = hint.split("→")
            if parts:
                anchor_violations.append(parts[0].strip())

    # Entity kinds from affected resources
    entity_kinds: list[str] = []
    for r in report.get("affected_resources") or []:
        k = r.get("kind") if isinstance(r, dict) else str(r)
        if k and k not in entity_kinds:
            entity_kinds.append(k)

    incident = ResolvedIncident(
        query=state.get("query", ""),
        hypothesis=state.get("current_hypothesis", ""),
        root_cause=report.get("root_cause", ""),
        anchor_violations=anchor_violations,
        entity_kinds=entity_kinds,
        remediation=report.get("remediation") or [],
        confidence=state.get("confidence") or "",
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


def confidence_router(state: RCAState) -> str:
    """
    LOW + retries remaining       → retry (same hypothesis, wider BFS)
    LOW + retries exhausted + more candidates → next_path (switch hypothesis)
    everything else               → review (human gate)
    """
    confidence = (state.get("confidence") or "").upper()
    retry      = state.get("retry_count", 0)
    if confidence == "LOW":
        if retry < MAX_RETRIES:
            return "retry"
        if state.get("candidate_paths"):
            return "next_path"
    return "review"


def human_router(state: RCAState) -> str:
    """approve → remediation; anything else → end."""
    return state.get("human_decision") or "reject"
