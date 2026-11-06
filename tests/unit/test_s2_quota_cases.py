"""S2's ResourceQuota family (docs/s2/family-quota.md): the committed fixtures are what the builder writes,
they load, their arithmetic holds, and nothing in them names the cause or a decoy."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tests.integration.cases.case_loader import build_graph, load_case

ROOT = Path(__file__).resolve().parents[2]
CASES = ROOT / "tests/s2/cases"
NAMES = ["q0_quota_no_incident", "q1_quota_filled_by_request_drift", "q2_quota_filled_by_stuck_jobs"]


def _used(case: str) -> dict:
    return yaml.safe_load((CASES / case / "kube/quota.yaml").read_text())["status"]["used"]


def test_the_fixtures_are_what_the_builder_writes(tmp_path):
    before = {p.relative_to(CASES): p.read_bytes() for p in CASES.rglob("*") if p.is_file()}
    subprocess.run([sys.executable, str(ROOT / "tests/s2/build_family_quota.py")], check=True, cwd=ROOT,
                   capture_output=True)
    after = {p.relative_to(CASES): p.read_bytes() for p in CASES.rglob("*") if p.is_file()}
    assert after == before


@pytest.mark.parametrize("case", NAMES)
def test_each_case_loads_with_the_products_loader(case):
    graph = build_graph(load_case(CASES / case))
    assert any(e.kind.value == "Pod" for e in graph.entities())


def test_the_quotas_are_full_where_the_chain_says_and_only_there():
    assert _used("q1_quota_filled_by_request_drift")["requests.memory"] == "31Gi"   # + a 2Gi pod > 32Gi
    assert _used("q2_quota_filled_by_stuck_jobs")["pods"] == "40"                  # the limit
    healthy = _used("q0_quota_no_incident")
    assert healthy["requests.memory"] == "15Gi" and healthy["pods"] == "15"       # control: room left


@pytest.mark.parametrize("case", NAMES)
def test_no_fixture_names_the_answer(case):
    for path in (CASES / case).rglob("*"):
        if path.is_file() and path.name != "expect.json":
            text = path.read_text().lower()
            assert not any(w in text for w in ("decoy", "cause", "why_not", "s2", "q1_", "q2_", "q0_")), path


@pytest.mark.parametrize("case", NAMES)
def test_every_case_states_its_answer(case):
    expect = json.loads((CASES / case / "expect.json").read_text())
    assert expect["set"] == "S2" and expect["family"] == "quota" and expect["sources"]
    if expect["expected_outcome"] == "cause":
        assert expect["cause_object"].count("/") == 2 and expect["mechanism"] and len(expect["chain"]) >= 3
    assert len(expect["decoys"]) >= 2
