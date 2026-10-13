"""Veracity replay harness — docs/test-cases.md → *Veracity replay harness*.

No Ollama: the ``rules`` solver end to end, the pure helpers and the report.
"""
from __future__ import annotations

import json

import pytest

from tools import veracity_replay as vr


@pytest.mark.parametrize("case,tier", [
    ("h001_crashloopbackoff", "A"), ("h012_gitops_render_vs_live", "A"),
    ("h013_slo_error_budget_burn", "B"), ("h016_multi_signal", "B"),
    ("h022_no_incident", "C"), ("h029_x", "C"), ("h030_x", "M"),
])
def test_tier_from_case_number(case, tier):
    assert vr.tier_of(case, {}) == tier


def test_tier_override():
    assert vr.tier_of("h001_x", {"tier": "M"}) == "M"


def test_answerable_needs_the_ground_truth_in_the_prompt():
    expect = {"root_cause_contains": ["x509", "expired", "certificate"],
              "root_cause_must_not_contain": ["memory"]}
    assert vr.answerable(expect, ["logs: x509: certificate has expired"]) is True
    assert vr.answerable(expect, ["readiness probe failed", "memory 80%"]) is False


def test_answerable_is_none_for_negative_cases_or_no_prompt():
    assert vr.answerable({"expected_outcome": "no_incident"}, ["anything"]) is None
    assert vr.answerable({"root_cause_contains": ["x"]}, []) is None


def test_every_native_case_has_a_query_that_does_not_give_the_answer():
    for case in vr.native_cases():
        expect = json.loads((vr.CASES_ROOT / case / "expect.json").read_text())
        query = expect.get("query", "")
        assert query, case
        truth = expect.get("root_cause_contains") or [
            k for group in expect.get("root_cause_contains_all", []) for k in group]
        assert not [k for k in truth if k.lower() in query.lower()], (case, query)


@pytest.mark.parametrize("key,expected", [("h004", "PASS"), ("h022", "PASS")])
def test_rules_solver_end_to_end(tmp_path, key, expected):
    assert vr.main(["--solver", "rules", "--cases", key, "--out", str(tmp_path)]) == 0
    records = vr.load_records(tmp_path)
    assert len(records) == 1
    record = records[0]
    assert record["solver"] == "rules"
    assert record["case"].startswith(key)
    assert record["veracity"] == expected
    assert record["confidence"] == "LOW"
    assert (tmp_path / "REPORT.md").read_text().count(record["case"]) == 1


def test_resume_skips_recorded_runs(tmp_path):
    vr.main(["--solver", "rules", "--cases", "h004", "--out", str(tmp_path)])
    path = next(tmp_path.glob("rules/*/run_1.json"))
    path.write_text(json.dumps({**json.loads(path.read_text()), "marker": True}))
    vr.main(["--solver", "rules", "--cases", "h004", "--resume", "--out", str(tmp_path)])
    assert json.loads(path.read_text())["marker"] is True


def _rec(case, tier, veracity, **kw):
    base = {"case": case, "tier": tier, "solver": "llm", "model": "m", "run": 1,
            "veracity": veracity, "answerable": True, "confidence": "MEDIUM",
            "expected_outcome": "cause", "wall_s": 2.0}
    return {**base, **kw}


def test_report_counts():
    records = [
        _rec("h001_a", "A", "PASS"),
        _rec("h001_a", "A", "FAIL", confidence="HIGH", run=2),          # model error, overconfident
        _rec("h014_b", "B", "FAIL", answerable=False),                    # pipeline error
        _rec("h022_c", "C", "FAIL", answerable=None, expected_outcome="no_incident"),  # false alarm
    ]
    report = vr.render_report(records, "header")
    rows = {line.split("|")[1].strip(): [c.strip() for c in line.split("|")[2:-1]]
            for line in report.splitlines() if line.startswith("| ") and "---" not in line}
    assert rows["**Tier A**"] == ["1/2", "2/2", "1", "1", "0", "2.0"]
    assert rows["**Tier B**"] == ["0/1", "0/1", "0", "0", "0", "2.0"]
    assert rows["**Tier C**"] == ["0/1", "—", "0", "0", "1", "2.0"]
    assert "header" in report


def test_llm_solver_refuses_without_ollama(tmp_path, monkeypatch):
    class Down:
        url, model = "http://nowhere", "m"

        def __init__(self, *a, **k):
            pass

        def is_available(self):
            return False

    monkeypatch.setattr("llm.ollama_client.OllamaClient", Down)
    assert vr.main(["--solver", "llm", "--out", str(tmp_path)]) == 2
    assert not list(tmp_path.iterdir())


def test_replay_does_not_leak_example_lookup_disabled(tmp_path, monkeypatch):
    monkeypatch.delenv("EXAMPLE_LOOKUP_DISABLED", raising=False)
    vr.main(["--solver", "rules", "--cases", "h004", "--out", str(tmp_path)])
    import os
    assert "EXAMPLE_LOOKUP_DISABLED" not in os.environ
