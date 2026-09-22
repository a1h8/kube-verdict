"""
h014 — expired mTLS certificate (the Loki-fixture wedge: the log-only signal).

Kubernetes shows the symptom — both pods Running but not Ready, readiness 503,
Deployment 0/2 — and even a cert-manager issuer warning, but nothing names the
expired certificate. Only the pods' logs do (x509: certificate has expired).
Together with h015 (traces) and h013 (metrics) this completes the three
observability signals.

The log streams (loki/streams.json, Loki's query_range shape) reach the graph
through the REAL LokiSource.collect(); only its private _query is replaced, and
it matches the LogQL label matchers against stream labels like Loki does. So the
pods are selected by Pod.needs_telemetry — they are Running, which phase-only
is_unhealthy would have skipped — and a decoy stream (same pod name, other
namespace) must never be returned.

Deterministic — no cluster, no Ollama, no FAISS embedding (stub store).
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from ontology.entities import Deployment, LokiLog, Pod, ResourceKind
from ontology.relationships import RelationshipType
from rca.context_builder import ContextBuilder
from tests.integration.cases.case_loader import build_graph, load_case

CASES_ROOT = Path(__file__).parent / "cases"
CASE_DIR = CASES_ROOT / "h014_cert_expiry"


class _StubStore:
    """No FAISS/embedding needed — this case exercises log wiring only."""

    last_retrieval_stats: dict = {}

    def search(self, query, top_k=10):
        return []

    def hybrid_search(self, query, top_k=10):
        return []


@pytest.fixture(scope="module")
def graph():
    return build_graph(load_case(CASE_DIR))


@pytest.fixture(scope="module")
def logs(graph):
    return [e for e in graph.entities(ResourceKind.LOKI_LOG) if isinstance(e, LokiLog)]


@pytest.fixture(scope="module")
def pods(graph):
    found = [e for e in graph.entities() if isinstance(e, Pod)]
    assert len(found) == 2
    return found


@pytest.fixture(scope="module")
def deployment(graph):
    deps = [e for e in graph.entities() if isinstance(e, Deployment)]
    assert len(deps) == 1
    return deps[0]


@pytest.fixture(scope="module")
def ctx(graph):
    return ContextBuilder(graph=graph, store=_StubStore()).build("why is billing-gateway not ready")


class TestRealLokiSourcePath:
    def test_fixture_holds_three_streams_including_the_decoy(self):
        raw = json.loads((CASE_DIR / "loki" / "streams.json").read_text())
        assert len(raw) == 3
        assert {s["stream"]["k8s_namespace_name"] for s in raw} == {"production", "staging"}

    def test_all_lines_of_both_production_pods_become_nodes(self, logs):
        assert len(logs) == 10

    def test_levels_come_from_the_real_detector(self, logs):
        assert Counter(rec.level for rec in logs) == {"error": 6, "warn": 2, "info": 2}

    def test_decoy_stream_from_another_namespace_is_never_returned(self, logs):
        assert not any("decoy" in rec.log_line for rec in logs)

    def test_each_pod_links_only_to_its_own_lines(self, graph, pods):
        for pod in pods:
            linked = graph.neighbors(pod.uid, RelationshipType.HAS_LOG)
            assert len(linked) == 5
            assert {rec.pod_name for rec in linked} == {pod.name}


class TestPodsAreRunningButNotReady:
    """Why the real collector selects them: needs_telemetry, not is_unhealthy."""

    def test_pods_are_not_phase_unhealthy(self, pods):
        assert all(not p.is_unhealthy for p in pods)

    def test_pods_need_telemetry(self, pods):
        assert all(p.is_not_ready and p.needs_telemetry for p in pods)

    def test_deployment_is_down(self, deployment):
        assert deployment.is_degraded
        assert deployment.ready_replicas == 0

    def test_logs_exist_only_thanks_to_the_readiness_aware_gate(self, monkeypatch):
        """Loki targets pods only; under the old phase-only selection nothing is fetched."""
        monkeypatch.setattr(Pod, "needs_telemetry", property(lambda self: self.is_unhealthy))
        g = build_graph(load_case(CASE_DIR))
        assert list(g.entities(ResourceKind.LOKI_LOG)) == []


class TestKubernetesShowsSymptomNotCause:
    def test_events_carry_the_symptom_and_the_issuer_warning(self, ctx):
        joined = " ".join(ctx.events)
        assert "Readiness probe failed" in joined
        assert "ErrRegisterACMEAccount" in joined or "ACME" in joined

    def test_no_event_names_the_expired_certificate(self, ctx):
        assert not any("x509" in e or "expired" in e.lower() for e in ctx.events)

    def test_only_the_logs_name_it(self, ctx):
        assert any("x509: certificate has expired" in line for line in ctx.logs)


class TestContextWindowSurfacesLogs:
    def test_only_error_and_warn_lines_reach_the_context(self, ctx):
        assert len(ctx.logs) == 8
        assert not any("level=info" in line for line in ctx.logs)

    def test_logs_are_the_only_observability_signal(self, ctx):
        assert ctx.traces == []
        assert ctx.alerts == []

    def test_prompt_block_carries_the_log_evidence(self, ctx):
        prompt = ctx.to_prompt_block()
        assert "LOGS" in prompt
        assert "x509" in prompt
        assert "payments-backend" in prompt


class TestFixtureHygiene:
    def test_no_false_missing_dependency(self, graph):
        missing = [k for e in graph.entities() for k in e.annotations if k.startswith("missing.")]
        assert missing == []


class TestLoaderStaysBackwardCompatible:
    def test_cases_without_loki_dir_load_no_streams(self):
        assert load_case(CASES_ROOT / "h001_crashloopbackoff")["loki_streams"] == []

    def test_cases_without_loki_dir_add_no_log_nodes(self):
        g = build_graph(load_case(CASES_ROOT / "h001_crashloopbackoff"))
        assert list(g.entities(ResourceKind.LOKI_LOG)) == []
