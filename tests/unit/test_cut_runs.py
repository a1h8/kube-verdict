"""The collector-cut runs — docs/veracity-replay.md § Amendment 2026-10-23 (the collector-cut
control). One run per case and collector, with that collector's backend unreachable."""
from __future__ import annotations

import json
import subprocess as sp

import pytest

from tests.unit.test_raw_context_solvers import FakeLLM
from tools import veracity_replay as vr

CUTTABLE = {"h005_rbac_forbidden": ["loki"], "h006_networkpolicy_blocked": ["loki"],
            "h013_slo_error_budget_burn": ["prometheus"], "h014_cert_expiry": ["loki"],
            "h015_etcd_compaction": ["otel"], "h016_multi_signal": ["prometheus", "loki", "otel"]}


def test_six_cases_and_only_them_have_a_collector_to_cut():
    found = {c: vr.cuttable(c) for c in vr.native_cases() if vr.cuttable(c)}
    assert found == CUTTABLE
    assert vr.cuttable("h003_oomkilled") == []                                    # control


def test_cut_auto_runs_eight_cut_runs_apart_from_the_connected_ones(tmp_path, monkeypatch):
    seen = []

    def spy(case_name, cut=None):
        seen.append((case_name, cut))
        return {"case": case_name, "run": 1, "veracity": "PASS", "reason": "", "tier": "B",
                "solver": "structured", "model": "", "confidence": "LOW", "fallbacks": [cut]}

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(vr, "solve_structured", spy)
    out = tmp_path / "out"
    assert vr.main(["--solver", "structured", "--cut", "auto", "--out", str(out), "--out-free"]) == 0
    assert sorted(seen) == sorted((c, k) for c, ks in CUTTABLE.items() for k in ks)
    assert len(seen) == 8
    record = json.loads((out / "structured/h016_multi_signal/cut-otel/run_1.json").read_text())
    assert record["cut"] == "otel"
    assert vr.load_records(out) == []                       # never mixed with connected runs
    assert (out / "CUT.md").exists() and not (out / "REPORT.md").exists()


def test_a_cut_structured_run_falls_back_and_is_less_confident():
    cut = vr.solve_structured("h013_slo_error_budget_burn", cut="prometheus")
    connected = vr.solve_structured("h013_slo_error_budget_burn")
    assert cut["fallbacks"] == ["prometheus"] and connected["fallbacks"] == []    # control
    order = ["", "LOW", "MEDIUM", "HIGH"]
    assert order.index(cut["confidence"]) < order.index(connected["confidence"])


def test_a_cut_context_run_falls_back():
    record = vr.solve_context("h014_cert_expiry", FakeLLM(), 1, cut="loki")
    assert record["fallbacks"] == ["loki"]
    assert vr.solve_context("h014_cert_expiry", FakeLLM(), 1)["fallbacks"] == []  # control


def test_the_raw_cut_leaves_the_collector_directory_out():
    case_dir = vr.CASES_ROOT / "h014_cert_expiry"
    _, connected = vr.raw_prompt(case_dir, "q")
    _, cut = vr.raw_prompt(case_dir, "q", "loki")
    assert any(f.startswith("loki/") for f in connected)                          # control
    assert cut and not any(f.startswith("loki/") for f in cut)
    assert set(cut) == {f for f in connected if not f.startswith("loki/")}


@pytest.mark.parametrize("argv", [
    ["--solver", "structured", "--cut", "loki", "--cases", "h003"],      # no loki in h003
    ["--solver", "rules", "--cut", "auto"],                              # reads no collector
])
def test_a_cut_that_cuts_nothing_is_refused(argv, tmp_path):
    with pytest.raises(SystemExit):
        vr.main([*argv, "--out", str(tmp_path / "out"), "--out-free"])
    assert not (tmp_path / "out").exists()


def test_the_child_receives_the_cut(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(vr.subprocess, "run", lambda cmd, **k: calls.append(cmd) or sp.CompletedProcess(cmd, 0))
    vr._run_in_child("llm:m", "h014_cert_expiry", 1, tmp_path, "loki")
    assert calls[0][-2:] == ["--cut", "loki"]
    # the child wrote no record: the failure names the cut, never mistaken for a connected run
    assert json.loads((tmp_path / "failures.jsonl").read_text().splitlines()[0])["cut"] == "loki"
    vr._run_in_child("llm:m", "h014_cert_expiry", 1, tmp_path)                    # control
    assert "--cut" not in calls[1]
