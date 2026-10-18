"""
h024 — two independent faults at once (tier C, family multi).

Spec: docs/test-cases.md → *Scenario catalog v2 — tier C cases* § h024. The
scripted LLM answers HIGH only when the evidence of both causes is in the
prompt. Veracity against a real model is graded by the replay harness; this
test proves both causes reach the model and that the ground truth is readable
from what it gets.
"""
from __future__ import annotations

import pytest

from tests.integration.workflow_harness import TIER_C_SCRIPTS, run_case
from tools.veracity import grade

SCRIPT = TIER_C_SCRIPTS["h024"]


@pytest.fixture(scope="module")
def run():
    return run_case("h024")


@pytest.mark.xfail(strict=True, reason="F11/H48: the drift was made by the test loader; production compares no values file to the deployed values (docs/fix-plan-001.md)")
def test_both_causes_reach_every_analyze_prompt(run):
    assert run.llm.analyze_prompts
    for prompt in run.llm.analyze_prompts:
        for marker in SCRIPT.prompt_evidence:
            assert marker in prompt, marker


@pytest.mark.parametrize("group", range(2))
def test_each_cause_is_answerable_from_the_prompt(run, group):
    """Enough of each keyword group is in the context for a model to name that cause."""
    keywords = run.expect["root_cause_contains_all"][group]
    expect = {"root_cause_contains": keywords}
    for prompt in run.llm.analyze_prompts:
        assert grade(prompt, expect)["veracity"] == "PASS", keywords


@pytest.mark.xfail(strict=True, reason="F11/H48: the drift was made by the test loader; production compares no values file to the deployed values (docs/fix-plan-001.md)")
def test_final_answer_names_both_causes(run):
    assert run.state["confidence"] == "HIGH"
    root_cause = run.state["report_dict"]["root_cause"]
    result = grade(root_cause, run.expect, confidence=run.state["confidence"])
    assert result["veracity"] == "PASS", result["reason"]
