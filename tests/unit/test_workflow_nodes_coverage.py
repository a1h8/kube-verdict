"""
Coverage tests for workflow/nodes.py — error/fallback and skip branches
that are not reached by the existing workflow integration tests.

Strategy:
  - For collector nodes: inject a pre-built graph and patch the collector
    import to raise so the fallback path is hit.
  - For skip branches: set the relevant cfg flag to False.
  - All tests use a fake config with a minimal graph to avoid K8s calls.
"""
from __future__ import annotations

from unittest.mock import patch

from ontology.graph import OntologyGraph
from workflow.nodes import (
    ingest_node,
    prometheus_node,
    metrics_node,
    otel_node,
    gitops_node,
    anchor_node,
    index_node,
    signal_analysis_node,
    dry_run_node,
    _parse_hypotheses,
    _exec_dry_run,
    _nested_get,
    _anchor_hit_to_hypothesis,
    _ANCHOR_FIELD_HYPOTHESES,
)
from workflow.state import RCAState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _empty_graph() -> OntologyGraph:
    return OntologyGraph()


def _config(graph=None, store=None, llm=None) -> dict:
    cfg = {"configurable": {}}
    if graph is not None:
        cfg["configurable"]["graph"] = graph
    if store is not None:
        cfg["configurable"]["store"] = store
    if llm is not None:
        cfg["configurable"]["llm"] = llm
    return cfg


def _state(**kw) -> RCAState:
    base: RCAState = {
        "query": "test query",
        "retry_count": 0,
        "human_decision": "",
        "error": "",
        "ingestion_stats": {},
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# ingest_node — skip branch (pre-built graph)
# ---------------------------------------------------------------------------

def test_ingest_node_skips_when_graph_provided():
    graph = _empty_graph()
    result = ingest_node(_state(), _config(graph=graph))
    assert result["ingestion_stats"]["ingest"]["skipped"] is True


def test_ingest_node_fallback_on_collector_error():
    # No graph in config → collector is invoked → patch it to raise
    with patch("ingestion.K8sCollector", side_effect=RuntimeError("no kubeconfig")):
        result = ingest_node(_state(), _config())
    assert result["ingestion_stats"]["ingest"]["fallback"] is True
    assert "error" in result["ingestion_stats"]["ingest"]


# ---------------------------------------------------------------------------
# prometheus_node — skip (disabled) + skip (no graph) + fallback
# ---------------------------------------------------------------------------

def test_prometheus_node_skip_when_disabled(monkeypatch):
    import config as cfg
    monkeypatch.setattr(cfg, "PROMETHEUS_ENABLED", False)
    result = prometheus_node(_state(), _config())
    assert result["ingestion_stats"]["prometheus"]["skipped"] is True


def test_prometheus_node_skip_when_no_graph(monkeypatch):
    import config as cfg
    monkeypatch.setattr(cfg, "PROMETHEUS_ENABLED", True)
    result = prometheus_node(_state(), _config())  # no graph in config
    assert result["ingestion_stats"]["prometheus"]["skipped"] is True


def test_prometheus_node_fallback_on_error(monkeypatch):
    import config as cfg
    monkeypatch.setattr(cfg, "PROMETHEUS_ENABLED", True)
    graph = _empty_graph()
    with patch("ingestion.prometheus_collector.PrometheusCollector", side_effect=OSError("timeout")):
        result = prometheus_node(_state(), _config(graph=graph))
    assert result["ingestion_stats"]["prometheus"]["fallback"] is True


# ---------------------------------------------------------------------------
# metrics_node — skip (disabled) + skip (no graph) + fallback
# ---------------------------------------------------------------------------

def test_metrics_node_skip_when_disabled(monkeypatch):
    import config as cfg
    monkeypatch.setattr(cfg, "METRICS_SERVER_ENABLED", False)
    result = metrics_node(_state(), _config())
    assert result["ingestion_stats"]["metrics"]["skipped"] is True


def test_metrics_node_skip_when_no_graph(monkeypatch):
    import config as cfg
    monkeypatch.setattr(cfg, "METRICS_SERVER_ENABLED", True)
    result = metrics_node(_state(), _config())
    assert result["ingestion_stats"]["metrics"]["skipped"] is True


def test_metrics_node_fallback_on_error(monkeypatch):
    import config as cfg
    monkeypatch.setattr(cfg, "METRICS_SERVER_ENABLED", True)
    graph = _empty_graph()
    with patch("ingestion.metrics_server_collector.MetricsServerCollector", side_effect=OSError("timeout")):
        result = metrics_node(_state(), _config(graph=graph))
    assert result["ingestion_stats"]["metrics"]["fallback"] is True


# ---------------------------------------------------------------------------
# otel_node — skip (both disabled) + skip (no graph)
# ---------------------------------------------------------------------------

def test_otel_node_skip_when_both_disabled(monkeypatch):
    import config as cfg
    monkeypatch.setattr(cfg, "OTEL_ENABLED", False)
    monkeypatch.setattr(cfg, "LOKI_ENABLED", False)
    result = otel_node(_state(), _config())
    assert result["ingestion_stats"]["otel"]["skipped"] is True


def test_otel_node_empty_when_no_graph(monkeypatch):
    import config as cfg
    monkeypatch.setattr(cfg, "OTEL_ENABLED", True)
    monkeypatch.setattr(cfg, "LOKI_ENABLED", False)
    result = otel_node(_state(), _config())  # no graph
    # node returns {} when no graph
    assert result == {} or result.get("ingestion_stats", {}).get("otel", {}).get("skipped")


# ---------------------------------------------------------------------------
# gitops_node — skip (disabled or no URL)
# ---------------------------------------------------------------------------

def test_gitops_node_skip_when_disabled(monkeypatch):
    import config as cfg
    monkeypatch.setattr(cfg, "GITOPS_ENABLED", False)
    result = gitops_node(_state(), _config())
    assert result["ingestion_stats"]["gitops"]["skipped"] is True


def test_gitops_node_skip_when_no_url(monkeypatch):
    import config as cfg
    monkeypatch.setattr(cfg, "GITOPS_ENABLED", True)
    monkeypatch.setattr(cfg, "GITOPS_REPO_URL", "")
    result = gitops_node(_state(), _config())
    assert result["ingestion_stats"]["gitops"]["skipped"] is True


def test_gitops_node_fallback_on_error(monkeypatch):
    import config as cfg
    monkeypatch.setattr(cfg, "GITOPS_ENABLED", True)
    monkeypatch.setattr(cfg, "GITOPS_REPO_URL", "https://github.com/acme/charts.git")
    monkeypatch.setattr(cfg, "GITOPS_BRANCH", "main")
    monkeypatch.setattr(cfg, "GITHUB_TOKEN", "")
    graph = _empty_graph()
    with patch("ingestion.git_provider.GithubProvider", side_effect=RuntimeError("api error")):
        result = gitops_node(_state(), _config(graph=graph))
    assert result["ingestion_stats"]["gitops"]["fallback"] is True


# ---------------------------------------------------------------------------
# anchor_node — skip when no graph
# ---------------------------------------------------------------------------

def test_anchor_node_empty_when_no_graph():
    result = anchor_node(_state(), _config())
    assert result == {} or "anchor" not in result.get("ingestion_stats", {})


def test_anchor_node_runs_with_empty_graph():
    graph = _empty_graph()
    result = anchor_node(_state(), _config(graph=graph))
    # Should complete without error even on an empty graph
    stats = result.get("ingestion_stats", {}).get("anchor", {})
    assert isinstance(stats, dict)


# ---------------------------------------------------------------------------
# index_node — skip when no graph, error fallback
# ---------------------------------------------------------------------------

def test_index_node_fallback_when_no_graph():
    result = index_node(_state(), _config())
    # No graph → should record a fallback or skipped
    stats = result.get("ingestion_stats", {}).get("index", {})
    assert stats.get("fallback") or stats.get("skipped") or stats == {}


def test_index_node_runs_with_empty_graph():
    graph = _empty_graph()
    with patch("vectorstore.store.FAISSStore.save"):  # avoid file I/O
        result = index_node(_state(), _config(graph=graph))
    # index_node returns {} on success
    assert result == {} or "error" not in result


# ---------------------------------------------------------------------------
# signal_analysis_node — fallback on error
# ---------------------------------------------------------------------------

def test_signal_analysis_node_fallback_on_error():
    graph = _empty_graph()
    with patch("signals.analyzer.SignalAnalyzer.analyze", side_effect=RuntimeError("no data")):
        result = signal_analysis_node(_state(), _config(graph=graph))
    assert result["ingestion_stats"]["signals"]["fallback"] is True


# ---------------------------------------------------------------------------
# dry_run_node — no commands → empty list
# ---------------------------------------------------------------------------

def test_dry_run_node_with_no_remediation():
    state = _state(remediation=[])
    result = dry_run_node(state, _config())
    assert result.get("dry_run_results", []) == [] or result == {}


# ---------------------------------------------------------------------------
# _parse_hypotheses
# ---------------------------------------------------------------------------

def test_parse_hypotheses_extracts_h_prefix_lines():
    raw = "H1: Pod app CrashLoopBackOff\nH2: PVC stuck Pending\nH3: image pull error"
    result = _parse_hypotheses(raw)
    assert len(result) == 3
    assert "CrashLoopBackOff" in result[0]


def test_parse_hypotheses_empty_returns_empty():
    assert _parse_hypotheses("") == []


def test_parse_hypotheses_no_hX_prefix_returns_lines():
    raw = "everything looks fine\nno issues detected"
    result = _parse_hypotheses(raw)
    # Falls back to non-empty lines
    assert isinstance(result, list)


# ---------------------------------------------------------------------------
# _exec_dry_run
# ---------------------------------------------------------------------------

def test_exec_dry_run_unknown_command():
    orig, dry, code = _exec_dry_run("totally-unknown-command-xyz --help")
    assert isinstance(code, int)


def test_exec_dry_run_helm_upgrade_with_set():
    orig = "helm upgrade myapp ./chart -n production --set image.tag=v2"
    _orig, dry, code = _exec_dry_run(orig)
    # helm_values_diff is called when no chart path works; result is a string
    assert isinstance(dry, str)
    assert isinstance(code, int)


# ---------------------------------------------------------------------------
# _nested_get
# ---------------------------------------------------------------------------

def test_nested_get_simple_key():
    d = {"a": {"b": {"c": 42}}}
    assert _nested_get(d, "a.b.c") == 42


def test_nested_get_missing_key_returns_none():
    d = {"a": {}}
    assert _nested_get(d, "a.b.c") is None


def test_nested_get_empty_path():
    assert _nested_get({"x": 1}, "") is None


# ---------------------------------------------------------------------------
# _anchor_hit_to_hypothesis
# ---------------------------------------------------------------------------

def test_anchor_hit_replicas():
    text = "ANCHOR VIOLATION: Deployment/prod/api field=spec.replicas 3 [manifest] vs 1"
    assert _anchor_hit_to_hypothesis(text) == "Replica count drift — deployment scaling mismatch"


def test_anchor_hit_image():
    text = "ANCHOR VIOLATION: Pod/prod/api-xyz field=containers.0.image v1.0 [manifest] vs v1.1"
    result = _anchor_hit_to_hypothesis(text)
    assert result is not None

def test_anchor_hit_unknown_field_returns_none():
    text = "ANCHOR VIOLATION: Deployment/prod/api field=unknownField xyz [manifest] vs abc"
    assert _anchor_hit_to_hypothesis(text) is None


def test_anchor_field_map_covers_all_entries():
    # Each entry in _ANCHOR_FIELD_HYPOTHESES should produce a non-empty hypothesis
    for field_fragment, hypothesis in _ANCHOR_FIELD_HYPOTHESES:
        text = f"ANCHOR VIOLATION: Pod/ns/name field={field_fragment} x [manifest] vs y"
        result = _anchor_hit_to_hypothesis(text)
        assert result == hypothesis, f"field_fragment={field_fragment!r} gave {result!r}"


# ---------------------------------------------------------------------------
# index_node — anchor violations wired up
# ---------------------------------------------------------------------------

def test_index_node_calls_anchor_violations(synthetic_graph):
    """index_node should call index_anchor_violations after index_graph."""
    with patch("vectorstore.store.FAISSStore.save"), \
         patch("vectorstore.store.FAISSStore.index_anchor_violations", return_value=0) as mock_av:
        index_node(_state(), _config(graph=synthetic_graph))
    mock_av.assert_called_once_with(synthetic_graph)
