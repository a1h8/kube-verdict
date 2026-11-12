"""The replay's set S2 (docs/s2-correlation-cases.md): its plumbing, tested on a copy of an S1 case put
in S2's form, so that no S2 case is run before the measurement it belongs to."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import tools.veracity_replay as vr
from tests.integration.workflow_harness import run_case
from tests.s2.live_graph import build_graph_live, kube_docs, namespaces_of

ROOT = Path(__file__).resolve().parents[2]
S1_CASE = ROOT / "tests/integration/cases/h004_missing_configmap"


@pytest.fixture
def s2_like(tmp_path):
    case = tmp_path / "x1_like_h004"
    shutil.copytree(S1_CASE, case)
    expect = json.loads((case / "expect.json").read_text())
    expect.update(set="S2", family="test")
    (case / "expect.json").write_text(json.dumps(expect))
    return case


def test_a_case_outside_s1_runs_through_the_collectors_graph(s2_like):
    run = run_case("x1", llm="none", case_dir=s2_like,
                   graph_builder=lambda d: build_graph_live(d, wire_signals=False),
                   namespaces=namespaces_of(kube_docs(s2_like)))
    assert run.expect["set"] == "S2"
    assert run.graph is not None and any(e.kind.value == "Pod" for e in run.graph.entities())
    assert run.state.get("report_dict") and run.state.get("diagnosis")
    assert set(run.state["namespaces"]) == set(namespaces_of(kube_docs(s2_like)))


def test_s1_is_unchanged_without_the_new_arguments():                           # control
    run = run_case("h004", llm="none")
    assert run.expect.get("set") is None and run.graph is not None


def test_an_s2_case_is_tiered_s2_and_an_s1_case_keeps_its_tier():
    assert vr.tier_of("q1_quota_filled_by_request_drift", {"set": "S2"}) == "S2"
    assert vr.tier_of("h004_missing_configmap", {}) == "A"


def test_the_set_switches_the_root_and_the_record_directory():
    try:
        vr.use_set("s2")
        assert vr.CASES_ROOT == vr.S2_ROOT and len(vr.native_cases()) == 21
        assert vr.run_dir_name("structured", "", None).startswith("s2-structured")
    finally:
        vr.use_set("s1")
    assert vr.run_dir_name("structured", "", None).startswith("structured")       # control


def test_a_frozen_case_matches_and_a_changed_one_is_refused(tmp_path, monkeypatch):
    try:
        vr.use_set("s2")
        assert vr.frozen_mismatches(vr.native_cases()) == []
        copy = tmp_path / "cases"
        shutil.copytree(vr.S2_ROOT, copy)
        p = copy / "q1_quota_filled_by_request_drift/kube/quota.yaml"
        p.write_text(p.read_text().replace("31Gi", "30Gi"))
        monkeypatch.setattr(vr, "CASES_ROOT", copy)
        assert vr.frozen_mismatches(["q1_quota_filled_by_request_drift"]) == ["q1_quota_filled_by_request_drift"]
    finally:
        vr.use_set("s1")


@pytest.mark.parametrize("argv,why", [
    (["--set", "s2", "--solver", "structured", "--cut", "auto"], "--cut"),
    (["--set", "s2", "--solver", "rules"], "the arms are"),
    (["--set", "s2", "--solver", "raw:mistral"], "the arms are"),
])
def test_the_replay_refuses_what_s2_does_not_measure(argv, why, capsys):
    try:
        with pytest.raises(SystemExit):
            vr.main(argv)
        assert why in capsys.readouterr().err
    finally:
        vr.use_set("s1")
