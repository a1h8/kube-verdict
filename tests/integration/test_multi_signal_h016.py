"""
h016 — Prometheus, Loki and OTel evidence at once, with one decoy per signal.

Spec: docs/test-cases.md → *Broadened offline coverage* §4. Same harness as the
h013–h015 end-to-end and degraded-path tests: the workflow's own collector
nodes fetch the case fixtures, the scripted LLM answers HIGH only when a marker
from each of the three signals is in the prompt. Each decoy tests a filter the
single-signal cases do not: the namespace check on alert correlation, pod
selection for Loki, the namespace filter on trace search.
"""
from __future__ import annotations

import pytest

from ontology.entities import LokiLog, OtelTrace, PrometheusAlert, ResourceKind
from tests.integration.workflow_harness import MULTI_SIGNAL_SCRIPTS, run_case

SCRIPT = MULTI_SIGNAL_SCRIPTS["h016"]

EXPECTED_COUNTS = {"prometheus": ("alerts", 2), "loki": ("logs", 9), "otel": ("traces", 3)}
SECTION_HEADERS = ("Firing Prometheus alerts", "### TRACES", "### LOGS")
DECOY_MARKERS = ("staging canary", "catalog-index", "payment-sandbox")
DECOY_TRACE_ID = "f0e1d2c3b4a5968778695a4b3c2d1e0f"


@pytest.fixture(scope="module")
def run():
    return run_case("h016")


class TestAllThreeSignalsCollected:
    @pytest.mark.parametrize("collector", sorted(EXPECTED_COUNTS))
    def test_collector_connected_with_decoys_excluded(self, run, collector):
        count_key, expected = EXPECTED_COUNTS[collector]
        stat = run.state["ingestion_stats"][collector]
        assert stat["fallback"] is False
        assert stat[count_key] == expected


class TestNoSignalCrowdsAnotherOut:
    def test_every_analyze_prompt_carries_the_three_sections(self, run):
        assert run.llm.analyze_prompts
        for prompt in run.llm.analyze_prompts:
            for header in SECTION_HEADERS:
                assert header in prompt

    def test_every_analyze_prompt_carries_a_marker_from_each_signal(self, run):
        for prompt in run.llm.analyze_prompts:
            for signal in SCRIPT.signals:
                assert signal.marker in prompt, signal.collector


class TestDecoysAreDropped:
    def test_no_decoy_reaches_any_prompt(self, run):
        for prompt in run.llm.analyze_prompts:
            for marker in DECOY_MARKERS:
                assert marker not in prompt

    def test_staging_alert_not_correlated(self, run):
        alerts = run.graph.entities(ResourceKind.PROMETHEUS_ALERT)
        assert alerts
        assert all(isinstance(a, PrometheusAlert) and a.namespace == "production" for a in alerts)

    def test_ready_pod_logs_not_queried(self, run):
        logs = run.graph.entities(ResourceKind.LOKI_LOG)
        assert logs
        assert all(isinstance(e, LokiLog) and e.pod_name.startswith("orders-api-") for e in logs)

    def test_staging_trace_not_attached(self, run):
        traces = run.graph.entities(ResourceKind.OTEL_TRACE)
        assert traces
        assert all(isinstance(t, OtelTrace) for t in traces)
        assert DECOY_TRACE_ID not in {t.trace_id for t in traces}


class TestRunConcludes:
    def test_reaches_high_with_a_verdict(self, run):
        assert run.state["confidence"] == "HIGH"
        assert run.state["verdict"] == "HUMAN_REVIEW"
        assert run.status == "AWAITING_REVIEW"


@pytest.mark.parametrize("signal", SCRIPT.signals, ids=lambda s: s.collector)
def test_cutting_one_collector_removes_only_its_signal(signal):
    cut = run_case("h016", cut=signal.collector)
    assert cut.state["ingestion_stats"][signal.collector]["fallback"] is True
    others = [s for s in SCRIPT.signals if s is not signal]
    for prompt in cut.llm.analyze_prompts:
        assert signal.marker not in prompt
        for other in others:
            assert other.marker in prompt
    assert cut.state["confidence"] != "HIGH"
