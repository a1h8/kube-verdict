"""
h013 / h014 / h015 end to end through the LangGraph workflow, offline.

Spec: docs/test-cases.md → *Broadened offline coverage* §1. Each case's
Kubernetes snapshot is pre-built; its Prometheus / Loki / OTel fixture is fetched
by the workflow's own collector nodes (tests/integration/workflow_harness.py).
The scripted LLM answers LOW twice on the first hypothesis, then HIGH, so the
multi-path loop really runs. What this does not prove: the real LLM's
root-cause quality (docs/veracity-benchmark.md).
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


class TestMultiPathLoop:
    def test_first_path_archived(self, run):
        _, r = run
        history = r.state["reasoning_history"]
        assert len(history) == 1
        assert history[0]["confidence"] == "LOW"

    def test_confidence_router_retried_switched_then_converged(self, run):
        _, r = run
        edges = [e["edge_taken"] for e in r.state["edge_log"] if e["router"] == "confidence"]
        assert edges == ["retry", "next_path", "review"]

    def test_final_path_is_high_and_differs_from_archived_one(self, run):
        _, r = run
        assert r.state["confidence"] == "HIGH"
        assert r.state["current_hypothesis"] != r.state["reasoning_history"][0]["hypothesis"]


class TestVerdict:
    def test_policy_decision_logged(self, run):
        _, r = run
        assert [e["edge_taken"] for e in r.state["edge_log"] if e["router"] == "policy"] == ["human_review"]

    def test_production_namespace_forces_human_review(self, run):
        _, r = run
        assert r.state["verdict"] == "HUMAN_REVIEW"
        assert any("production" in reason for reason in r.state["verdict_reasons"])

    def test_run_stops_at_the_human_gate(self, run):
        _, r = run
        assert r.status == "AWAITING_REVIEW"
        assert r.review_payload is not None

    def test_remediation_has_a_rollback_path(self, run):
        _, r = run
        assert r.state["blast_radius"]["rollback_available"] is True

    def test_dry_run_was_stubbed_not_executed(self, run):
        _, r = run
        assert r.state["dry_run_results"]
        assert all("fixture-replay" in d["output"] for d in r.state["dry_run_results"])


def test_h013_root_cause_names_the_dependency():
    """h013's correct hypothesis is the second one: the first (CPU) is archived."""
    r = run_case("h013")
    assert "payments-gateway" in r.state["current_hypothesis"]
    assert "CPU" in r.state["reasoning_history"][0]["hypothesis"]


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
