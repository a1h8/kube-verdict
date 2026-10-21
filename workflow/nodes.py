"""
LangGraph node functions for the KubeVerdict RCA workflow.

Heavy objects (OntologyGraph, FAISSStore) are NOT stored in state — they are
passed via config["configurable"] so the MemorySaver checkpointer never tries
to serialise them.  Nodes that need them call _get_infra(config, state).
"""
from __future__ import annotations
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

import config as cfg
import telemetry
from decision.labels import normalize_label
from rca.analyzer import RCAAnalyzer, _generate_rollback, unrecoverable_commands
from rca.context_builder import ContextBuilder
from decision.decision_engine import DecisionEngine
from decision.models import IncidentReport
from reasoning.beam_search import MAX_SWITCHES, should_switch_path
from reasoning.monte_carlo import run_monte_carlo
from remediation.blast_radius import (
    compute_blast_radius,
    compute_blast_radius_from_diff,
)
from vectorstore.embedder import Embedder
from vectorstore.store import FAISSStore
from workflow.state import RCAState

# LangGraph does not propagate config mutations between nodes.
# Heavy non-serializable objects are cached here, keyed per *run* (state["_infra_key"], set by
# ingest_node), not per thread: an API session reuses its thread id, and a second investigation
# on it must collect the cluster again, not diagnose the previous snapshot (fix plan 001, H9).
# Bounded: the oldest runs are evicted in a long-lived process.
_INFRA_CACHE: dict[str, tuple[Any, Any]] = {}
_INFRA_CACHE_MAX = 64


def _cache_put(key: str, value: tuple[Any, Any]) -> None:
    _INFRA_CACHE.pop(key, None)
    _INFRA_CACHE[key] = value
    while len(_INFRA_CACHE) > _INFRA_CACHE_MAX:
        _INFRA_CACHE.pop(next(iter(_INFRA_CACHE)))

log = logging.getLogger(__name__)

MAX_RETRIES = 2
MAX_PATHS   = 3     # max distinct hypotheses to explore per run
EXAMPLE_MATCH_THRESHOLD = 0.65   # cosine similarity (IndexFlatIP, L2-normalised, all-MiniLM-L6-v2)


def _stats(state: RCAState, step: str, data: dict) -> dict:
    """Merge per-step telemetry into ingestion_stats without overwriting other steps."""
    if data.get("fallback"):
        telemetry.record_collector_fallback(step)
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

def _get_infra(config: RunnableConfig, state: RCAState | None = None) -> tuple[Any, Any]:
    """Pull graph and store from config, else from this run's entry in the infra cache."""
    c = config.get("configurable", {})
    graph = c.get("graph")
    store = c.get("store")
    if graph is None:
        key = (state or {}).get("_infra_key") or c.get("thread_id", "")
        cached = _INFRA_CACHE.get(key)
        if cached:
            graph, store_cached = cached
            if store is None:
                store = store_cached
    return graph, store


# GitOps sources of a run, [(provider, charts_path), …], keyed like _INFRA_CACHE: gitops_node
# used to put the provider in config, which the next node never sees (fix plan 001, H10).
_PROVIDER_CACHE: dict[str, list[tuple[Any, str]]] = {}


def _run_key(config: RunnableConfig, state: RCAState | None) -> str:
    return (state or {}).get("_infra_key") or config.get("configurable", {}).get("thread_id", "")


def _get_providers(config: RunnableConfig, state: RCAState | None = None) -> list[tuple[Any, str]]:
    """The GitOps sources gitops_node used in this run (an injected provider first), or []."""
    injected = config.get("configurable", {}).get("provider")
    if injected is not None:
        return [(injected, cfg.GITOPS_CHARTS_PATH)]
    return _PROVIDER_CACHE.get(_run_key(config, state), [])


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
    if config.get("configurable", {}).get("graph") is not None:
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
        helm_failures = list(getattr(helm, "failures", None) or [])
        helm_stat = {"releases": helm_releases, "fallback": bool(helm_failures)}
        if helm_failures:
            helm_stat["error"] = "; ".join(helm_failures[:5])

        if cfg.HELMFILE_PATH:
            from ingestion import HelmfileCollector
            hf = HelmfileCollector(
                helmfile_path=cfg.HELMFILE_PATH,
                environment=cfg.HELMFILE_ENVIRONMENT,
                use_cli=cfg.HELMFILE_USE_CLI,
            )
            hf.collect(built_graph)

        drift_count = HelmDriftDetector().detect_all(built_graph)

        # B — the manifest Helm applied vs the live objects: an edit made outside Helm
        # (docs/three-states.md). Needs no Git repository, only the release Secrets.
        from ingestion.manifest_differ import compare_applied_to_live
        applied_releases, applied_drifts = compare_applied_to_live(
            built_graph, covered=getattr(collector, "covered", None))
        manifest_failures = [f for f in (getattr(helm, "failures", None) or [])
                             if str(f).startswith("helm get manifest")]
        applied_stat = {"releases": applied_releases, "drifts": applied_drifts,
                        "fallback": bool(manifest_failures)}
        if manifest_failures:
            applied_stat["error"] = "; ".join(manifest_failures[:5])

        # Policy reports (Kyverno / OPA PolicyReport) and mutating webhooks — collected on
        # every run, not only by the CLI's old --policy flag (docs/fix-plan-001.md, H2). Same
        # API client as the K8s collector, so impersonation applies. A cluster without the
        # PolicyReport CRD is not a failure; a refused or broken listing is (its own step, so
        # _ingestion_failures sees it).
        try:
            from ingestion import PolicyCollector
            policies = PolicyCollector(api_client=getattr(collector, "api_client", None))
            pr = policies.collect(
                built_graph, namespaces=state.get("namespaces") or cfg.KUBE_NAMESPACES or None)
            policy_stat = {"fail": pr.fail_count, "audit": pr.audit_count,
                           "webhooks": pr.mutation_webhooks,
                           "fallback": bool(policies.last_error)}
            if policies.last_error:
                policy_stat["error"] = policies.last_error
        except Exception as exc:  # noqa: BLE001 — policies must never break the investigation
            log.warning("policy collection failed: %s", exc)
            policy_stat = {"fallback": True, "error": str(exc)}

        kube_ver = getattr(collector, "kube_version", None)
        log.info("ingest: %s", built_graph.summary())
        # A refused listing (403, timeout) leaves a hole the diagnosis would read as "nothing
        # there": it is a collection failure (docs/fix-plan-001.md, H24).
        failed_listings = list(getattr(collector, "listing_failures", None) or [])
        discovery = getattr(collector, "_discovery", None)
        refused_kinds = list(getattr(discovery, "failures", None) or []) if discovery else []
        discovery_stat = {"refused": len(refused_kinds), "fallback": bool(refused_kinds)}
        if refused_kinds:
            discovery_stat["error"] = "; ".join(refused_kinds[:10])

        tid = config.get("configurable", {}).get("thread_id", "")
        infra_key = f"{tid}:{uuid.uuid4().hex}"
        _cache_put(infra_key, (built_graph, config.get("configurable", {}).get("store")))
        update = _stats(state, "policy", policy_stat)
        update = _stats({**state, **update}, "helm", helm_stat)
        update = _stats({**state, **update}, "discovery", discovery_stat)
        update = _stats({**state, **update}, "applied", applied_stat)
        return {"_infra_key": infra_key, **_stats({**state, **update}, "ingest", {
            "entities": built_graph.node_count,
            "edges": built_graph.edge_count,
            "helm_releases": helm_releases,
            "helm_drift": drift_count,
            "kube_version": str(kube_ver) if kube_ver else "",
            "fallback": bool(failed_listings),
            **({"error": "cannot list " + "; ".join(failed_listings)} if failed_listings else {}),
        })}

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

    graph, _ = _get_infra(config, state)
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
        if collector.last_error:
            log.warning("prometheus: unreachable (%s) — continuing without alert data",
                        collector.last_error)
            return _stats(state, "prometheus", {
                "alerts": count, "fallback": True, "error": collector.last_error,
            })
        log.info("prometheus: %d alert(s) correlated", count)
        return _stats(state, "prometheus", {"alerts": count, "fallback": False,
                                            "uncorrelated": collector.uncorrelated,
                                            "out_of_scope": collector.out_of_scope})
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

    graph, _ = _get_infra(config, state)
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
        stat = {"pods_annotated": count, "fallback": bool(collector.last_error)}   # fix plan H52
        if collector.last_error:
            stat["error"] = collector.last_error
        return _stats(state, "metrics", stat)
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

    Writes two ingestion_stats entries, ``otel`` (traces) and ``loki`` (logs), each
    with its own ``fallback`` flag, so a failing backend shows up in the B9 overlay,
    in _ingestion_failures() and in the kubeverdict.collector.fallback counter.
    """
    if not cfg.OTEL_ENABLED and not cfg.LOKI_ENABLED:
        log.info("otel: disabled — skipping")
        update = _stats(state, "otel", {"skipped": True})
        return _stats({**state, **update}, "loki", {"skipped": True})

    graph, _ = _get_infra(config, state)
    if graph is None:
        log.info("otel: no graph — skipping")
        return {}

    otel_stat: dict = {"skipped": True}
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
            otel_collector = OtelCollector(backend, lookback_hours=cfg.OTEL_LOOKBACK_HOURS)
            count = otel_collector.collect(graph)
            if otel_collector.last_error:
                log.warning("otel traces: backend failed (%s) — %d trace(s) collected",
                            otel_collector.last_error, count)
                otel_stat = {"traces": count, "fallback": True,
                             "error": otel_collector.last_error}
            else:
                log.info("otel: %d trace(s) ingested", count)
                otel_stat = {"traces": count, "fallback": False}
        except Exception as exc:
            log.warning("otel traces failed (%s) — continuing without trace data", exc)
            otel_stat = {"fallback": True, "error": str(exc)}

    loki_stat: dict = {"skipped": True}
    if cfg.LOKI_ENABLED:
        try:
            from ingestion.loki_source import LokiSource
            loki = LokiSource(
                url=cfg.LOKI_URL, token=cfg.LOKI_TOKEN, timeout=cfg.LOKI_TIMEOUT,
                lookback_hours=cfg.LOKI_LOOKBACK_HOURS,
                max_logs_per_pod=cfg.LOKI_MAX_LOGS_PER_POD,
                pod_label=cfg.LOKI_POD_LABEL, namespace_label=cfg.LOKI_NAMESPACE_LABEL,
            )
            count = loki.collect(graph, query=state.get("query", ""))
            if loki.last_error:
                log.warning("loki: query failed (%s) — %d log(s) collected",
                            loki.last_error, count)
                loki_stat = {"logs": count, "fallback": True, "error": loki.last_error}
            else:
                log.info("loki: %d log(s) ingested", count)
                loki_stat = {"logs": count, "fallback": False}
        except Exception as exc:
            log.warning("loki logs failed (%s) — continuing without log data", exc)
            loki_stat = {"fallback": True, "error": str(exc)}

    update = _stats(state, "otel", otel_stat)
    return _stats({**state, **update}, "loki", loki_stat)


# ─────────────────────────────────────────────────────────────────────────────
# GitOps drift detection
# ─────────────────────────────────────────────────────────────────────────────

def gitops_node(state: RCAState, config: RunnableConfig) -> dict:
    """
    Run GitOps drift detection: fetch chart from Git, helm template, diff vs cluster.
    Skipped when GITOPS_ENABLED=false or no GITOPS_REPO_URL is configured.
    Fails silently — gitops drift is enrichment, not a blocker.
    """
    # Every repository of GIT_REPOS (GIT_REPOS_JSON, or the legacy GITOPS_REPO_URL), as the UI
    # does (H11); each one cloned — GitHub too: the REST provider has no checkout, so the
    # collector never found a chart in it (H30).
    repos = cfg.GIT_REPOS
    if not cfg.GITOPS_ENABLED or not repos:
        log.info("gitops: disabled or no repository — skipping")
        return _stats(state, "gitops", {"skipped": True})

    graph, _ = _get_infra(config, state)
    if graph is None:
        log.info("gitops: no graph — skipping")
        return {}

    from ingestion.git_provider import make_provider
    from ingestion.gitops_collector import GitopsCollector

    drifts: list = []
    sources: list[tuple[Any, str]] = []
    errors: list[str] = []
    for repo in repos:
        charts_path = repo.get("charts_path") or cfg.GITOPS_CHARTS_PATH
        try:
            provider = make_provider(repo["url"], branch=repo.get("branch") or cfg.GITOPS_BRANCH,
                                     token=repo.get("token") or None)
            collector = GitopsCollector(provider, charts_path=charts_path)
            drifts += collector.collect(graph)
            if collector.missing_applied:
                errors.append(f"{repo.get('label') or repo['url']}: applied manifest unavailable for "
                              + ", ".join(collector.missing_applied[:5]) + " — Git not compared")
            if getattr(provider, "last_error", None):
                # an unreadable repository renders nothing: no drift ≠ no divergence (H5)
                errors.append(f"{repo.get('label') or repo['url']}: {provider.last_error}")
            sources.append((provider, charts_path))
        except Exception as exc:  # noqa: BLE001 — one repository must not stop the others
            log.warning("gitops: %s failed (%s)", repo.get("label") or repo["url"], exc)
            errors.append(f"{repo.get('label') or repo['url']}: {exc}")
    _PROVIDER_CACHE[_run_key(config, state)] = sources
    while len(_PROVIDER_CACHE) > _INFRA_CACHE_MAX:
        _PROVIDER_CACHE.pop(next(iter(_PROVIDER_CACHE)))

    critical = sum(1 for d in drifts if d.severity == "critical")
    log.info("gitops: %d drift(s) found, %d critical, %d repo(s)", len(drifts), critical, len(repos))
    stat = {"drifts": len(drifts), "critical": critical, "repos": len(repos),
            "fallback": bool(errors)}
    if errors:
        stat["error"] = "; ".join(errors)
    update = _stats(state, "gitops", stat)
    update["drift_evidence"] = _render_evidence_rows(graph)
    return update


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
    graph, _ = _get_infra(config, state)
    if graph is None:
        log.info("anchor: no graph — skipping")
        return {}

    sources = _get_providers(config, state)  # [] when gitops is disabled

    try:
        from ingestion.anchor_engine import AnchorEngine
        engine = AnchorEngine()
        records = engine.collect(graph, sources=sources)
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
    """Build this investigation's FAISSStore. Skipped only when a caller injects one (replay).

    The store holds the cluster as collected by this run, plus the knowledge base — resolved
    incidents and documents — for every entry point, not only the UI (docs/fix-plan-001.md,
    H39). It is not saved: a store from an earlier run (another moment, another cluster) put
    its pods in this run's prompt (H40).
    """
    graph, store = _get_infra(config, state)
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

    # Knowledge base: resolved incidents (ExampleStore) and enterprise documents (DocStore).
    examples = docs = 0
    try:
        from knowledge import DocIndexer, DocStore, ExampleIndexer, ExampleStore
        examples = ExampleIndexer(built_store).index_all(ExampleStore())
        docs = DocIndexer(built_store).index_all(DocStore())
    except Exception as exc:  # noqa: BLE001 — the knowledge base must never break RCA
        log.warning("index: knowledge base skipped: %s", exc)

    log.info("index: %d vectors, %d anchor violation(s), %d chart anchor(s), %d example(s), "
             "%d doc chunk(s)", built_store.size, anchor_count, chart_anchor_count, examples, docs)

    config.setdefault("configurable", {})["store"] = built_store
    # Also refresh the thread-local infra cache: LangGraph does not reliably
    # share a mutated config["configurable"] across nodes, so downstream nodes
    # (analyze, example_lookup) read the store via _INFRA_CACHE. Without this,
    # a live run that did not pre-pass a store sees store=None in analyze_node
    # → silent error early-return → confidence=UNKNOWN → spurious NO_GO.
    # Only *update* the entry ingest_node established for this thread — never
    # create one — so isolated node tests don't leak a graph into the global
    # cache across the suite.
    key = state.get("_infra_key") or ""
    cached = _INFRA_CACHE.get(key) if key else None
    if cached is not None:
        _cache_put(key, (cached[0], built_store))
    return {}


# ─────────────────────────────────────────────────────────────────────────────
# Structured diagnosis — docs/structured-in-product.md
# ─────────────────────────────────────────────────────────────────────────────

def diagnose_node(state: RCAState, config: RunnableConfig) -> dict:
    """Typed signals → ranked diagnoses, or an abstention. The report's cause (rule 2).

    A ``no_incident`` with a collector that reported a failure is ``insufficient_evidence``:
    what could not be read is not known to be healthy (rule 3, fix plan H5)."""
    from rca.diagnosis import diagnose
    from rca.signals import extract_signals
    graph, _ = _get_infra(config, state)
    if graph is None:
        return {}
    try:
        outcome = diagnose(extract_signals(graph))
    except Exception as exc:  # noqa: BLE001 — a diagnosis failure is an abstention, not a crash
        log.error("diagnose failed: %s", exc)
        return {"diagnosis": {"outcome": "insufficient_evidence", "confidence": "LOW", "score": 0.0,
                              "root_cause": f"Insufficient evidence: the diagnosis failed ({exc}).",
                              "failed_collectors": ["diagnose"], "diagnoses": []}}
    failures = _ingestion_failures(state)
    abstention = outcome.outcome
    root_cause = outcome.root_cause()
    if abstention == "no_incident" and failures:
        abstention = "insufficient_evidence"
        root_cause = ("Insufficient evidence: no fault signal, but collection failed for "
                      + ", ".join(failures) + " — what they hold is unknown.")
    diagnosis = {
        "outcome": abstention,
        "root_cause": root_cause,
        "confidence": outcome.confidence if abstention == "cause" else "LOW",
        "score": round(outcome.diagnoses[0].score, 3) if abstention == "cause" else 0.0,
        "failed_collectors": failures,
        "diagnoses": [{"family": d.family, "cause": d.cause, "location": d.cause_location,
                       "score": round(d.score, 3), "label": d.label, "rule": d.rule,
                       "checks": list(d.checks), "signal_ids": list(d.signal_ids)}
                      for d in outcome.diagnoses],
    }
    log.info("diagnose: %s — %s", abstention, root_cause[:120])
    return {"diagnosis": diagnosis}


def _location_name(location: str) -> str:
    return location.rsplit("/", 1)[-1].lower()


def _report_from_diagnosis(state: RCAState, llm_report: dict | None) -> tuple[dict, str]:
    """The report: the diagnosis decides, the LLM (if any) explains and proposes (rule 4).

    Returns (report_dict, disagreement). The LLM disagrees when its root cause names none of
    the reported diagnoses' locations."""
    diag = state.get("diagnosis") or {}
    reported = diag.get("diagnoses") or []
    report = dict(llm_report or {})
    # the LLM report's own `decision` re-ran the gate on the pre-LLM context score and could
    # contradict the workflow's verdict (h013: NO_GO beside HUMAN_REVIEW); the verdict is the
    # policy gate's, on the diagnosis (spec rule 6, fix plan H66)
    report.pop("decision", None)
    llm_cause = str(report.get("root_cause") or "")
    disagreement = ""
    if llm_report is not None and reported and not any(
            _location_name(d["location"]) in llm_cause.lower() for d in reported):
        disagreement = (f"the LLM named another cause ({llm_cause[:200]!r}); "
                        f"the structured diagnosis stands")
    report.update({
        "query":       state.get("query", ""),
        "root_cause":  diag.get("root_cause", ""),
        "affected":    [d["location"] for d in reported],
        "confidence":  diag.get("confidence", "LOW"),
        "diagnostics": [{"command": c, "source": "rule", "hypothesis": d["rule"],
                         "affected": d["location"], "purpose": "", "confirms_if": "",
                         "refutes_if": ""} for d in reported for c in d.get("checks", [])]
                       + list(report.get("diagnostics") or []),
        "diagnosis":   diag,
        "explanation": llm_cause,
        "llm_disagreement": disagreement,
    })
    report.setdefault("summary", diag.get("root_cause", ""))
    report.setdefault("remediation", [])
    report.setdefault("rollback", [])
    if llm_report is None:
        report["summary"] = diag.get("root_cause", "")
    return report, disagreement


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
    graph, _ = _get_infra(config, state)
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
        analyzer = SignalAnalyzer(prometheus_source=prom_source)
        results = analyzer.analyze(graph)
        anomalous = [r for r in results if r.is_anomalous and not r.synthetic]
        mode = analyzer.last_mode
        log.info(
            "signal_analysis[%s]: %d metrics analysed, %d anomalous",
            mode, len(results), len(anomalous),
        )
        return _stats(state, "signals", {
            "total": len(results), "anomalous": len(anomalous),
            "synthetic": analyzer.last_synthetic, "mode": mode, "fallback": False,
        })
    except Exception as exc:
        log.warning("signal_analysis failed (%s) — continuing without signal data", exc)
        return _stats(state, "signals", {"fallback": True, "error": str(exc)})


# ─────────────────────────────────────────────────────────────────────────────
# Hypotheses — the ranked structured diagnoses
# ─────────────────────────────────────────────────────────────────────────────


def hypothesize_node(state: RCAState, config: RunnableConfig) -> dict:
    """
    The paths to explain are the ranked structured diagnoses (docs/structured-in-product.md,
    rule 7): H1 = the top diagnosis, H2/H3 the next ones. The old RemediationEngine, the
    ontology chains, the template catalog and the LLM fill-in no longer make hypotheses — the
    first claimed precise causes on broad clues (fix plan H22), the last let the model decide.
    An abstention gives no path.
    """
    diag = state.get("diagnosis") or {}
    reported = diag.get("diagnoses") or []
    if not reported:
        log.info("hypothesize: %s — no path to explain", diag.get("outcome", "no diagnosis"))
        return {"hypothesis_sources": []}
    final = [d["cause"] for d in reported[:MAX_PATHS]]
    first, *rest = final
    log.info("hypothesize: %d path(s) from the structured diagnosis", len(final))
    return {
        "current_hypothesis": first,
        "candidate_paths":    rest,
        "reasoning_history":  [],
        "hypothesis_sources": [{"rule_id": d["rule"], "family": d["family"],
                                "affected": d["location"], "weight": d["score"],
                                "evidence": d["signal_ids"]} for d in reported[:MAX_PATHS]],
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
    _, store   = _get_infra(config, state)
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
    graph, store = _get_infra(config, state)
    if graph is None or store is None:
        return {"error": "analyze_node: graph or store missing from config"}

    llm = _get_llm(config)
    if llm is None:
        # LLM_PROVIDER=none: the report is the structured diagnosis (spec rule 5)
        report_dict, _ = _report_from_diagnosis(state, None)
        log.info("analyze: no LLM — report = structured diagnosis (%s)", report_dict["confidence"])
        return {"raw_analysis": "", "report_dict": report_dict,
                "confidence": report_dict["confidence"], "llm_disagreement": ""}

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
    analyzer = RCAAnalyzer(graph=graph, store=store, llm=llm)
    analyzer._ctx_builder = ctx_builder

    report = analyzer.analyze(focused_query)
    llm_label = normalize_label(report.confidence)   # "MEDIUM:" → MEDIUM (H20)
    llm_dict = report.to_dict()
    llm_dict["llm_confidence"] = llm_label           # recorded, not used by the gate (rule 6)
    report_dict, disagreement = (_report_from_diagnosis(state, llm_dict)
                                 if state.get("diagnosis") else (llm_dict, ""))
    confidence = (normalize_label(str(report_dict.get("confidence") or ""))
                  if state.get("diagnosis") else llm_label)
    if disagreement:
        log.warning("analyze: %s", disagreement)

    log.info(
        "analyze: confidence=%s (llm=%s)  retry=%d  hypothesis='%s'  chunks=%d",
        confidence, llm_label, retry, (hypothesis or "—")[:60], report.context.total_chunks,
    )
    return {
        "raw_analysis":     report.raw_analysis,
        "report_dict":      report_dict,
        "confidence":       confidence,
        "kube_version":     report.kube_version,
        "llm_disagreement": disagreement,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Human review (interrupt point)
# ─────────────────────────────────────────────────────────────────────────────

def human_review_node(state: RCAState, config: RunnableConfig) -> dict:
    """
    Pause execution and surface the report to a human operator.

    Raises a LangGraph Interrupt — caller resumes with:
        Command(resume="approve")  or  Command(resume="reject"), or
        Command(resume={"decision": "approve", "by": "<who>"}) to record the approver.

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
        "diagnostics":        report_dict.get("diagnostics") or [],   # read-only, not approved
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
        return {"human_decision": "approve", "human_decided_by": "auto-approve"}

    answer = interrupt(payload)
    if isinstance(answer, dict):
        decision, decided_by = answer.get("decision"), str(answer.get("by") or "")
    else:
        decision, decided_by = answer, ""

    normalised = (decision or "").strip().lower() if isinstance(decision, str) else ""
    if normalised not in ("approve", "reject"):
        normalised = "reject"

    log.info("human_review: decision=%s by=%s", normalised, decided_by or "(unknown)")
    return {"human_decision": normalised, "human_decided_by": decided_by}


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
    # An unreadable current state gives no diff, not a diff against {} that shows every key as
    # new (docs/fix-plan-001.md, H13). `null` / empty output = no user-supplied values.
    try:
        r = subprocess.run(get_cmd, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as exc:
        return " ".join(get_cmd), f"[current values unreadable: {exc} — no diff computed]", 1
    if r.returncode != 0:
        why = (r.stderr or r.stdout or "").strip()[:300] or f"exit {r.returncode}"
        return " ".join(get_cmd), f"[current values unreadable: {why} — no diff computed]", r.returncode
    try:
        current = json.loads(r.stdout) if r.stdout.strip() else {}
    except json.JSONDecodeError as exc:
        return " ".join(get_cmd), f"[current values unreadable: {exc} — no diff computed]", 1
    current = current if isinstance(current, dict) else {}

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


# kubectl verbs whose --dry-run=server only simulates (checked against `kubectl <verb>
# --help`, v1.37). Anything else (exec, cp, port-forward, attach, edit, debug, rollout
# restart, plugins…) has no dry-run and would really run, or fail on the unknown flag.
_DRY_RUN_VERBS = {"apply", "create", "delete", "patch", "replace", "scale", "label",
                  "annotate", "autoscale", "expose", "run", "cordon", "uncordon", "drain", "taint"}
_DRY_RUN_SUBVERBS = {"rollout": {"undo"},
                     "set": {"image", "env", "resources", "selector", "serviceaccount", "subject"}}
# Flags that take a value; the verb is the first word that is not a flag or a flag value.
_KUBECTL_VALUE_FLAGS = {"-n", "--namespace", "-l", "--selector", "-f", "--filename", "-o", "--output"}
# Flags that would point the dry-run at another cluster or identity.
_KUBECTL_TARGET_FLAGS = ("--kubeconfig", "--context", "--cluster", "--user", "--server", "-s",
                         "--token", "--as", "--as-group", "--as-uid")


def _kubectl_dry_run_refusal(args: list[str]) -> str:
    """Why a kubectl command must not be run as a dry-run, or "" when it can be."""
    words: list[str] = []
    skip = False
    for a in args:
        if a == "--":
            return "arguments passed to another command"
        if a.split("=", 1)[0] in _KUBECTL_TARGET_FLAGS:
            return f"{a.split('=', 1)[0]} changes the target cluster or identity"
        if a.startswith("--dry-run") and a != "--dry-run=server":
            return f"{a} is not a server-side dry-run"
        if skip:
            skip = False
        elif a in _KUBECTL_VALUE_FLAGS:
            skip = True
        elif not a.startswith("-"):
            words.append(a)
    verb = words[0] if words else ""
    if verb in _DRY_RUN_SUBVERBS:
        sub = words[1] if len(words) > 1 else ""
        return "" if sub in _DRY_RUN_SUBVERBS[verb] else f"'kubectl {verb} {sub}' has no dry-run"
    if verb in _DRY_RUN_VERBS:
        return ""
    return f"'kubectl {verb}' has no dry-run" if verb else "no kubectl verb"


# What an operator may run from the UI's manual remediation panel (docs/fix-plan-001.md, H46):
# kubectl and helm only, against the current context, never a command that runs code in a pod,
# opens a tunnel or calls another binary.
_MANUAL_KUBECTL_VERBS = (_DRY_RUN_VERBS - {"run"}) | {"rollout", "set", "get", "describe",
                                                       "logs", "top", "explain"}
_MANUAL_HELM_VERBS = {"upgrade", "install", "rollback", "status", "get", "history", "list", "diff"}
_HELM_TARGET_FLAGS = ("--kube-context", "--kubeconfig", "--kube-apiserver", "--kube-token",
                      "--kube-as-user", "--kube-as-group", "--kube-ca-file")


def manual_apply_refusal(cmd: str) -> str:
    """Why the UI must not run *cmd* for the operator, or "" when it may."""
    import shlex
    if any(tok in cmd for tok in ("<<", "&&", "||", "|", ";", "`", "$(", ">")):
        return "shell construct"
    try:
        parts = shlex.split(cmd)
    except ValueError as exc:
        return f"cannot parse ({exc})"
    if not parts:
        return "empty command"
    tool, args = parts[0], parts[1:]
    if tool == "kubectl":
        from rca.analyzer import _SECRET_READ
        if _SECRET_READ.search(cmd):
            return "it prints Secret data — kube-verdict reads no Secret (fix plan H51)"
        words, skip = [], False
        for a in args:
            if a == "--":
                return "arguments passed to another command"
            if a.split("=", 1)[0] in _KUBECTL_TARGET_FLAGS:
                return f"{a.split('=', 1)[0]} changes the target cluster or identity"
            if skip:
                skip = False
            elif a in _KUBECTL_VALUE_FLAGS:
                skip = True
            elif not a.startswith("-"):
                words.append(a)
        verb = words[0] if words else ""
        return "" if verb in _MANUAL_KUBECTL_VERBS else f"'kubectl {verb}' is not run from the UI"
    if tool == "helm":
        for a in args:
            flag = a.split("=", 1)[0]
            if flag in _HELM_TARGET_FLAGS:
                return f"{flag} changes the target cluster or identity"
            if flag == "--post-renderer":
                return "--post-renderer runs another binary"
        verb = next((a for a in args if not a.startswith("-")), "")
        return "" if verb in _MANUAL_HELM_VERBS else f"'helm {verb}' is not run from the UI"
    return f"only kubectl and helm commands are run from the UI, not '{tool}'"


def _exec_dry_run(cmd: str) -> tuple[str, str, int]:
    """
    Transform one remediation command into its dry-run equivalent and execute it.
    Returns (dry_run_command, output, exit_code).

    Strategy:
      helm upgrade/install → try `helm diff upgrade --reuse-values` first,
                              fall back to `helm upgrade --dry-run --reuse-values`
      kubectl <verb>       → append `--dry-run=server`, only for verbs that support it
                              (``_kubectl_dry_run_refusal``); never run otherwise
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
        refused = _kubectl_dry_run_refusal(parts[1:])
        if refused:
            return cmd, f"[dry-run not run: {refused} — review manually]", 0
        dry_parts = parts + ([] if "--dry-run=server" in parts else ["--dry-run=server"])
        try:
            r = subprocess.run(dry_parts, capture_output=True, text=True, timeout=30)
            output = r.stdout or r.stderr or "[no output]"
            return " ".join(dry_parts), output.strip(), r.returncode
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            return " ".join(dry_parts), f"[error: {exc}]", 1

    else:
        return cmd, f"[dry-run not supported for '{tool}' — review manually]", 0


def _drifts_from_evidence(rows: list[dict]) -> tuple[list, list[str]]:
    """Flatten grouped render-vs-live drift rows into duck-typed drift objects.

    ``rows`` is the per-entity structure produced by ``_render_evidence_rows``:
    ``{kind, name, namespace, diffs:[{field_path, severity, ...}]}``. We flatten
    it into ``field_path``/``severity`` objects that ``compute_blast_radius_from_diff``
    can classify, prefixing the field path with the entity kind/name so it stays a
    unique resource identifier and cluster-scoped kinds are still detectable.
    Returns the drift objects and the sorted set of impacted namespaces.
    """
    from types import SimpleNamespace

    drifts: list = []
    namespaces: set[str] = set()
    for row in rows:
        kind = row.get("kind", "")
        name = row.get("name", "")
        if row.get("namespace"):
            namespaces.add(row["namespace"])
        for d in row.get("diffs", []):
            drifts.append(SimpleNamespace(
                field_path=f"{kind} {name}.{d.get('field_path', '')}".strip(),
                severity=d.get("severity", "info"),
            ))
    return drifts, sorted(namespaces)


def blast_radius_node(state: RCAState, config: RunnableConfig) -> dict:
    """
    Compute the blast radius of proposed remediation before dry-run.

    Prefers the **render-vs-live** path (h012): when ``drift_evidence`` is present
    (gitops_node rendered the chart with `helm template` and diffed it against the
    live cluster), the risk is classified from the actual changed objects via
    ``compute_blast_radius_from_diff``. Falls back to the command-string heuristic
    (``compute_blast_radius``) when no rendered diff is available.
    """
    report      = state.get("report_dict") or {}
    remediation = report.get("remediation") or []
    affected    = report.get("affected") or []
    rollback    = report.get("rollback") or _generate_rollback(remediation)

    unrecoverable = unrecoverable_commands(remediation)

    drift_rows = state.get("drift_evidence") or []
    br = None
    if drift_rows:
        drifts, drift_ns = _drifts_from_evidence(drift_rows)
        if drifts:
            br = compute_blast_radius_from_diff(drifts, rollback, namespaces=drift_ns)
            log.info("blast_radius[rendered-diff]: risk=%s  %s", br["risk"], br["summary"])
    if br is None:
        br = compute_blast_radius(remediation, affected, rollback)
        log.info("blast_radius[heuristic]: risk=%s  %s", br["risk"], br["summary"])

    # Every action needs its own way back (docs/fix-plan-001.md, H19).
    if unrecoverable:
        br = {**br, "rollback_available": False, "unrecoverable": unrecoverable,
              "summary": f"{br['summary']} — no rollback for: {'; '.join(unrecoverable[:3])}"}
        log.info("blast_radius: %d command(s) without rollback", len(unrecoverable))
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

    Score source: the structured diagnosis' score (docs/structured-in-product.md, rule 6);
    the LLM label's mapping only when no diagnosis ran.
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
    # The Monte Carlo varies the old pre-LLM score; it no longer decides (spec "Not in this
    # spec"): recorded in mc_result, not passed to the gate.
    mc_win_rate  = 1.0
    max_hit      = False   # the "beam exhausted" NO_GO was unreachable — removed (dead code)
    # The gate scores the structured diagnosis (spec rule 6); an abstention scores 0.
    diag = state.get("diagnosis")
    diag_score = float(diag.get("score", 0.0)) if diag else None

    if diag and diag.get("outcome") != "cause":
        # An abstention has no action to score: nothing to approve and nothing to block — the
        # operator reviews the finding (the h022 decision of 2026-10-12; spec rule 6)
        from decision.models import DecisionResult
        from decision.policy_gate import Verdict
        result = DecisionResult(
            verdict=Verdict.HUMAN_REVIEW,
            reasons=[f"{diag.get('outcome')}: no cause diagnosed — nothing to apply, the operator reviews"],
            score=0.0, risk=risk, rollback_available=rollback_ok, namespace=namespace,
            mc_win_rate=mc_win_rate, max_switches_reached=max_hit)
    else:
        result = _DECISION_ENGINE.decide(
            report,
            risk=risk,
            rollback_available=rollback_ok,
            namespace=namespace,
            mc_win_rate=mc_win_rate,
            max_switches_reached=max_hit,
            command_count=br.get("command_count"),
            score=diag_score,
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
    # opt-in: a similar past incident is evidence, not a reason to skip the analysis
    if not cfg.EXAMPLE_SHORTCUT_ENABLED:
        return {}

    _, store = _get_infra(config, state)
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

    graph, store = _get_infra(config, state)

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

    ExampleStore(config.get("configurable", {}).get("example_dir")).save(incident)
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

    if state.get("diagnosis"):
        # the structured diagnosis sets the confidence: retrying the LLM or switching path
        # cannot change it (docs/structured-in-product.md, rules 2 and 7)
        edge   = "review"
        reason = (f"confidence={confidence or 'UNKNOWN'} set by the structured diagnosis "
                  f"({(state['diagnosis'] or {}).get('outcome', '?')}) — forwarding to review"
                  + (f"; ingestion failures: {failures}" if failures else ""))
    elif confidence == "LOW":
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
            "decided_by":         state.get("human_decided_by") or "(unknown)",
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
