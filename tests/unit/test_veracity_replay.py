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
    assert rows["**Tier A**"] == ["1/2", "0", "2/2", "1", "1", "0", "2.0"]
    assert rows["**Tier B**"] == ["0/1", "0", "0/1", "0", "0", "0", "2.0"]
    assert rows["**Tier C**"] == ["0/1", "0", "—", "0", "0", "1", "2.0"]
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


def test_hung_llm_run_is_killed_and_reported(tmp_path, monkeypatch, capsys):
    import subprocess

    calls = []

    def fake_run(cmd, timeout, check):
        calls.append((cmd, timeout))
        raise subprocess.TimeoutExpired(cmd, timeout)

    monkeypatch.setattr(vr.subprocess, "run", fake_run)
    monkeypatch.setenv("REPLAY_RUN_TIMEOUT", "7")
    vr._run_in_child("llm", "h004_missing_configmap", 2, tmp_path)
    (cmd, timeout), = calls
    assert timeout == 7
    assert cmd[cmd.index("--child-run") + 1] == "2"
    assert cmd[cmd.index("--cases") + 1] == "h004"
    assert "process killed" in capsys.readouterr().err
    # no run record, but the failure is kept (docs/fix-plan-001.md, R1)
    assert [p.name for p in tmp_path.iterdir()] == ["failures.jsonl"]
    assert "process killed" in (tmp_path / "failures.jsonl").read_text()


def test_regrade_rereads_records_without_touching_them(tmp_path):
    case_dir = tmp_path / "h004_missing_configmap"
    case_dir.mkdir()
    record = {"case": "h004_missing_configmap", "tier": "A", "solver": "llm", "run": 1,
              "veracity": "FAIL", "confidence": "MEDIUM", "verdict": "HUMAN_REVIEW",
              "root_cause": "The ConfigMap api-gateway-config has not been created."}
    path = case_dir / "run_1.json"
    path.write_text(json.dumps(record))
    assert vr.main(["--regrade", str(tmp_path)]) == 0
    report = (tmp_path / "REGRADED.md").read_text()
    assert "| h004_missing_configmap | 0/1 | 1/1 |" in report
    assert json.loads(path.read_text())["veracity"] == "FAIL"



# ── docs/fix-plan-001.md, H7 and R1 ───────────────────────────────────────────
def test_a_missing_run_counts_as_a_fail():
    case = vr.native_cases()[0]
    records = [_rec(case, vr.tier_of(case, {}), "PASS", run=1)]
    report = vr.render_report(records, "h", expected={"llm": [(case, 1), (case, 2)]})
    row = next(line for line in report.splitlines() if line.startswith(f"| {case} "))
    cells = [c.strip() for c in row.split("|")[2:-1]]
    assert cells[0] == "1/2" and cells[1] == "1"
    # control: without the expected runs, the lost run vanished from the denominator
    old = vr.render_report(records, "h")
    assert [c.strip() for c in next(line for line in old.splitlines() if line.startswith(f"| {case} ")).split("|")[2:-1]][0] == "1/1"


def test_a_child_that_dies_is_recorded(tmp_path, monkeypatch):
    import subprocess as sp
    monkeypatch.setattr(vr.subprocess, "run", lambda *a, **k: sp.CompletedProcess(a, 137))
    vr._run_in_child("llm:m", "h001_x", 2, tmp_path)
    lines = (tmp_path / "failures.jsonl").read_text().splitlines()
    assert len(lines) == 1 and '"child exited with code 137"' in lines[0]


def test_a_child_that_records_its_run_is_not_a_failure(tmp_path, monkeypatch):            # control
    import subprocess as sp

    def ok(*a, **k):
        p = vr.record_path(tmp_path, "llm", "h001_x", 2)
        p.parent.mkdir(parents=True)
        p.write_text("{}")
        return sp.CompletedProcess(a, 0)
    monkeypatch.setattr(vr.subprocess, "run", ok)
    vr._run_in_child("llm:m", "h001_x", 2, tmp_path)
    assert not (tmp_path / "failures.jsonl").exists()


def test_an_llm_run_records_its_prompts(monkeypatch):
    # docs/fix-plan-001.md: what the model read must be checkable afterwards
    from types import SimpleNamespace
    case = vr.native_cases()[0]
    expect = {"root_cause_contains": ["x"], "query": "q"}
    run = SimpleNamespace(expect=expect, llm=SimpleNamespace(analyze_prompts=["PROMPT ONE"], calls=[]),
                          state={"report_dict": {"root_cause": "x", "explanation": "x"},
                                 "confidence": "LOW", "verdict": "HUMAN_REVIEW"})
    monkeypatch.setattr("tests.integration.workflow_harness.run_case", lambda *a, **k: run)
    record = vr.solve_llm(case, SimpleNamespace(model="m"), 1)
    assert record["prompts"] == ["PROMPT ONE"]
