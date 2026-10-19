"""
h013 / h014 / h015 end to end through the LangGraph workflow, offline.

Spec: docs/test-cases.md → *Broadened offline coverage* §1, and since 2026-10-18
docs/structured-in-product.md: the structured diagnosis decides the cause and its confidence;
the scripted LLM only explains it. Each case's Kubernetes snapshot is pre-built; its
Prometheus / Loki / OTel fixture is fetched by the workflow's own collector nodes
(tests/integration/workflow_harness.py). h014 and h015 are diagnosed (cert expiry, etcd); h013
is not (the structured rules have no SLO / dependency-latency family yet) and abstains — the
LLM's answer cannot turn that into a cause. What this does not prove: the real LLM's wording
(docs/veracity-benchmark.md).
"""
from __future__ import annotations

import pytest

from tests.integration.workflow_harness import CASE_SCRIPTS, run_case

CASES = sorted(CASE_SCRIPTS)


@pytest.fixture(scope="module", params=CASES)
def run(request):
    return request.param, run_case(request.param)


class TestSignalEvidenceReachesTheLLM:
    def test_every_analyze_prompt_carries_the_case_evidence(self, run):
        key, r = run
        assert r.llm.analyze_prompts
        for prompt in r.llm.analyze_prompts:
            for needle in CASE_SCRIPTS[key].prompt_evidence:
                assert needle in prompt, f"{key}: {needle!r} missing from analyze prompt"


class TestCollectorsRanInsideTheWorkflow:
    def test_case_collector_succeeded_with_data(self, run):
        key, r = run
        script = CASE_SCRIPTS[key]
        stat = r.state["ingestion_stats"][script.collector]
        assert stat["fallback"] is False
        assert stat[script.count_key] > 0

    def test_snapshot_was_prebuilt_not_ingested(self, run):
        _, r = run
        assert r.state["ingestion_stats"]["ingest"] == {"skipped": True, "reason": "pre-built"}

    def test_no_collector_fell_back(self, run):
        _, r = run
        failed = [k for k, v in r.state["ingestion_stats"].items()
                  if isinstance(v, dict) and v.get("fallback")]
        assert failed == []


def _diagnosed(r) -> bool:
    return r.state["diagnosis"]["outcome"] == "cause"


class TestOnePathPerDiagnosis:
    def test_no_llm_driven_path_switch(self, run):
        _, r = run
        assert not r.state.get("reasoning_history")
        edges = [e["edge_taken"] for e in r.state["edge_log"] if e["router"] == "confidence"]
        assert edges == ["review"]

    def test_confidence_is_the_diagnosis(self, run):
        _, r = run
        assert r.state["confidence"] == r.state["diagnosis"]["confidence"]

    def test_the_report_cause_is_the_diagnosis(self, run):
        _, r = run
        assert r.state["report_dict"]["root_cause"] == r.state["diagnosis"]["root_cause"]


class TestVerdict:
    def test_policy_decision_logged(self, run):
        _, r = run
        # a diagnosed cause in production, or an abstention (nothing to apply): the operator reviews
        assert [e["edge_taken"] for e in r.state["edge_log"] if e["router"] == "policy"] == ["human_review"]

    def test_production_namespace_forces_human_review(self, run):
        _, r = run
        if not _diagnosed(r):
            pytest.skip("abstention: HUMAN_REVIEW before the namespace rule")
        assert r.state["verdict"] == "HUMAN_REVIEW"
        assert any("production" in reason for reason in r.state["verdict_reasons"])

    def test_run_stops_at_the_human_gate(self, run):
        _, r = run
        assert r.status == "AWAITING_REVIEW" and r.review_payload is not None

    def test_dry_run_was_stubbed_not_executed(self, run):
        _, r = run
        if not _diagnosed(r):
            pytest.skip("abstention: nothing reaches the dry-run")
        assert r.state["dry_run_results"]
        assert all("fixture-replay" in d["output"] for d in r.state["dry_run_results"])


def test_h013_abstains_and_the_llm_cannot_make_it_a_cause():
    r = run_case("h013")
    assert r.state["diagnosis"]["outcome"] == "insufficient_evidence"
    assert r.state["verdict"] == "HUMAN_REVIEW"                 # nothing to approve, nothing to block
    assert r.state["report_dict"]["confidence"] == "LOW"
    assert r.state["report_dict"]["explanation"]          # the LLM's own text is kept, as text


_VOLATILE = {"ts", "timestamp"}


def _strip_volatile(node):
    if isinstance(node, dict):
        return {k: _strip_volatile(v) for k, v in node.items() if k not in _VOLATILE}
    if isinstance(node, list):
        return [_strip_volatile(v) for v in node]
    return node


@pytest.mark.parametrize("key", CASES)
def test_frozen_vitrine_fixture_matches_the_code(key):
    """dashboard/src/sampleJourneys/<key>.json is generated, never hand-edited:
    it must equal a fresh run (timestamps aside). Regenerate with
    ``python tools/freeze_journey_fixtures.py``."""
    import json
    from pathlib import Path

    from tools.freeze_journey_fixtures import OUT_DIR, journey

    frozen = json.loads((OUT_DIR / f"{key}.json").read_text())
    assert frozen["source"] == "fixture-replay"
    fresh = json.loads(json.dumps(journey(key)))
    assert _strip_volatile(frozen) == _strip_volatile(fresh), (
        f"{Path(OUT_DIR, key + '.json')} is stale — run tools/freeze_journey_fixtures.py"
    )
