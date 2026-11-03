"""
Degraded paths — the case's own collector made unreachable, in turn.

Spec: docs/test-cases.md → *Broadened offline coverage* §3. The outage is
injected at the HTTP edge (``requests.get`` raising ConnectionError in the
collector's module), so the real collector code meets it; the other collectors
stay on their fixtures. The scripted LLM answers HIGH only when the case's
evidence is in the prompt. Not asserted: the pre-LLM context score, which does
not drop today (spec finding; roadmap *Evidence-aware pre-LLM score*).
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from tests.integration.workflow_harness import CASE_SCRIPTS, run_case

CASES = sorted(CASE_SCRIPTS)


@pytest.fixture(scope="module", params=CASES)
def degraded(request):
    key = request.param
    script = CASE_SCRIPTS[key]
    with patch("telemetry.record_collector_fallback") as counter:
        run = run_case(key, cut=script.collector)
    return key, script, run, [c.args[0] for c in counter.call_args_list]


class TestOutageIsVisible:
    def test_cut_collector_is_a_fallback_with_its_error(self, degraded):
        _, script, run, _ = degraded
        stat = run.state["ingestion_stats"][script.collector]
        assert stat["fallback"] is True
        assert "connection refused" in stat["error"]
        assert stat[script.count_key] == 0

    def test_fallback_counter_incremented_for_that_collector_only(self, degraded):
        _, script, _, counted = degraded
        assert counted == [script.collector]

    def test_other_collectors_untouched(self, degraded):
        _, script, run, _ = degraded
        failed = [k for k, v in run.state["ingestion_stats"].items()
                  if isinstance(v, dict) and v.get("fallback")]
        assert failed == [script.collector]

    def test_every_confidence_decision_names_the_failed_collector(self, degraded):
        _, script, run, _ = degraded
        decisions = [e for e in run.state["edge_log"] if e["router"] == "confidence"]
        assert decisions
        for entry in decisions:
            assert entry["snapshot"]["ingestion_failures"] == [script.collector]
            assert script.collector in entry["reason"]


class TestEvidenceIsGone:
    def test_signal_absent_from_every_analyze_prompt(self, degraded):
        _, script, run, _ = degraded
        assert run.llm.analyze_prompts
        for prompt in run.llm.analyze_prompts:
            assert script.signal_marker not in prompt


class TestVerdictIsNotSilentlyTheSame:
    def test_the_degraded_run_is_never_more_confident(self, degraded):
        # the confidence is the structured diagnosis' (docs/structured-in-product.md): losing a
        # collector can lower it, never raise it
        key, _, run, _ = degraded
        rank = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
        assert rank[run.state["confidence"]] <= rank[run_case(key).state["confidence"]]

    def test_a_degraded_no_incident_is_never_claimed(self, degraded):
        _, script, run, _ = degraded
        assert run.state["diagnosis"]["outcome"] != "no_incident"
        if run.state["diagnosis"]["outcome"] == "insufficient_evidence":
            assert script.collector in run.state["diagnosis"]["failed_collectors"]

    def test_a_verdict_is_still_produced(self, degraded):
        _, _, run, _ = degraded
        assert run.state["verdict"] in {"HUMAN_REVIEW", "NO_GO"}
        assert run.state["verdict"] != "AUTO"
