"""
h022 — nothing is broken (tier C, family negative).

Spec: docs/test-cases.md → *Scenario catalog v2 — tier C cases* § h022. Every
ledger pod is Ready; the only decoy is a BackOff event from a restart 3 h ago.
The scripted LLM answers LOW "no incident". Decided 2026-10-12: a PASS needs a
LOW label and a HUMAN_REVIEW verdict (expect.json → expected_verdict).
"""
from __future__ import annotations

import pytest

from tests.integration.workflow_harness import CUTTABLE, run_case
from tools.veracity import grade

COUNT_KEYS = {"prometheus": "alerts", "loki": "logs", "otel": "traces"}


@pytest.fixture(scope="module")
def run():
    return run_case("h022")


@pytest.mark.parametrize("collector", CUTTABLE)
def test_nothing_found_is_not_an_outage(run, collector):
    stat = run.state["ingestion_stats"][collector]
    assert stat["fallback"] is False
    assert stat[COUNT_KEYS[collector]] == 0


def test_decoy_event_reaches_the_prompt(run):
    assert run.llm.analyze_prompts
    for prompt in run.llm.analyze_prompts:
        assert "BackOff" in prompt


def test_answer_is_low_and_names_no_cause(run):
    assert run.state["confidence"] == "LOW"
    root_cause = run.state["report_dict"]["root_cause"]
    expect = {k: v for k, v in run.expect.items() if k != "expected_verdict"}
    result = grade(root_cause, expect, confidence=run.state["confidence"])
    assert result["veracity"] == "PASS", result["reason"]


def test_verdict_is_human_review(run):
    """Read-only checks go to diagnostics, so there is no action left to block."""
    report = run.state["report_dict"]
    assert report["remediation"] == []
    assert report["diagnostics"]
    assert run.state["verdict"] == "HUMAN_REVIEW", run.state.get("verdict_reasons")
    result = grade(report["root_cause"], run.expect, confidence=run.state["confidence"],
                   verdict=run.state["verdict"])
    assert result["veracity"] == "PASS", result["reason"]
