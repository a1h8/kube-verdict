"""S2's families (docs/s2/): the committed fixtures are what the builders write, they load, the quota
arithmetic holds, and nothing in them names the cause or a decoy."""
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
NAMES = ["q0_quota_no_incident", "q1_quota_filled_by_request_drift", "q2_quota_filled_by_stuck_jobs",
         "n0_netpol_no_incident", "n1_netpol_namespace_relabel", "n2_netpol_pod_labels_changed",
         "d0_dependency_no_incident", "d1_dependency_service_wrong_port", "d2_dependency_redis_full",
         "l0_limitrange_no_incident", "l1_limitrange_default_injected", "l2_limitrange_max_lowered",
         "a0_hpa_no_incident", "a1_hpa_custom_metrics_api_points_away", "a2_hpa_scale_up_policy_slowed"]
BUILDERS = ["build_family_quota.py", "build_family_netpol.py", "build_family_dependency.py",
            "build_family_limitrange.py", "build_family_hpa.py"]


def _used(case: str) -> dict:
    return yaml.safe_load((CASES / case / "kube/quota.yaml").read_text())["status"]["used"]


def test_the_fixtures_are_what_the_builder_writes(tmp_path):
    before = {p.relative_to(CASES): p.read_bytes() for p in CASES.rglob("*") if p.is_file()}
    for builder in BUILDERS:
        subprocess.run([sys.executable, str(ROOT / "tests/s2" / builder)], check=True, cwd=ROOT, capture_output=True)
    after = {p.relative_to(CASES): p.read_bytes() for p in CASES.rglob("*") if p.is_file()}
    assert after == before


@pytest.mark.parametrize("case", NAMES)
def test_each_case_loads_with_the_products_loader(case):
    graph = build_graph(load_case(CASES / case))
    assert any(e.kind.value == "Pod" for e in graph.entities())


def test_the_netpol_cases_break_only_by_labels():
    import yaml as _y
    ns = {d["metadata"]["name"]: d["metadata"]["labels"] for d in _y.safe_load((CASES / "n1_netpol_namespace_relabel/kube/namespaces.yaml").read_text())["items"]}
    assert ns["shop"]["team"] == "shop"                                                  # n1: the relabel
    ok = {d["metadata"]["name"]: d["metadata"]["labels"] for d in _y.safe_load((CASES / "n0_netpol_no_incident/kube/namespaces.yaml").read_text())["items"]}
    assert ok["shop"]["team"] == "storefront"                                            # control
    pods = _y.safe_load((CASES / "n2_netpol_pod_labels_changed/kube/pods.yaml").read_text())["items"]
    orders = [p for p in pods if p["metadata"]["name"].startswith("orders-api-")]
    assert orders and all("app" not in p["metadata"]["labels"] for p in orders)             # n2: the label is gone


def test_the_dependency_cases_break_where_the_chain_says():
    import yaml as _y
    def target(case):
        svcs = _y.safe_load((CASES / case / "kube/services.yaml").read_text())["items"]
        return [s for s in svcs if s["metadata"]["name"] == "inventory"][0]["spec"]["ports"][0]["targetPort"]
    assert target("d1_dependency_service_wrong_port") == 8080                       # the pods listen on 9090
    assert target("d0_dependency_no_incident") == 9090                              # control
    assert "maxmemory" in (CASES / "d2_dependency_redis_full/loki/streams.json").read_text()
    assert "maxmemory" not in (CASES / "d0_dependency_no_incident/loki/streams.json").read_text()


def test_the_limitrange_cases_set_what_the_chain_says():
    import yaml as _y
    def lr(case):
        return _y.safe_load((CASES / case / "kube/limitrange.yaml").read_text())["spec"]["limits"][0]
    def worker_limit(case):
        pods = _y.safe_load((CASES / case / "kube/pods.yaml").read_text())["items"]
        p = [p for p in pods if p["metadata"]["name"].startswith("report-worker-")][0]
        return p["spec"]["containers"][0]["resources"]["limits"]["memory"], p["metadata"]["annotations"]
    limit, ann = worker_limit("l1_limitrange_default_injected")
    assert limit == "256Mi" == lr("l1_limitrange_default_injected")["default"]["memory"]
    assert "kubernetes.io/limit-ranger" in ann                                          # set by admission
    assert lr("l2_limitrange_max_lowered")["max"]["memory"] == "1Gi"
    assert lr("l0_limitrange_no_incident")["max"]["memory"] == "4Gi"                    # control
    assert worker_limit("l0_limitrange_no_incident")[0] == "1Gi"


EVIDENCE = yaml.safe_load((ROOT / "tests/s2/evidence.yaml").read_text())


def test_every_case_with_a_cause_has_its_evidence_table():
    with_cause = [c for c in NAMES if json.loads((CASES / c / "expect.json").read_text())["expected_outcome"] == "cause"]
    assert sorted(with_cause) == sorted(EVIDENCE)
    for case, hops in EVIDENCE.items():
        assert len(hops) >= 4, case                                        # a chain of several steps
        assert all(h["join"] for h in hops[:-1]) and hops[-1]["join"] is None, case


@pytest.mark.parametrize("case,hop", [(c, h) for c, hs in EVIDENCE.items() for h in hs],
                         ids=lambda x: x if isinstance(x, str) else x["hop"][:40])
def test_each_hop_is_shown_by_its_file(case, hop):
    text = (CASES / case / hop["file"]).read_text()
    assert hop["contains"] in text, f"{case}: {hop['hop']}"
    if hop.get("absent"):
        assert hop["absent"] not in text, f"{case}: {hop['hop']}"


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
            assert not any(w in text for w in ("decoy", "cause", "why_not", "s2", "q1_", "q2_", "q0_",
                                               "n1_", "n2_", "n0_", "d1_", "d2_", "d0_",
                                               "l1_", "l2_", "l0_", "a1_", "a2_", "a0_")), path


@pytest.mark.parametrize("case", NAMES)
def test_every_case_states_its_answer(case):
    expect = json.loads((CASES / case / "expect.json").read_text())
    assert expect["set"] == "S2" and expect["family"] in ("quota", "netpol", "dependency", "limitrange", "hpa") and expect["sources"]
    if expect["expected_outcome"] == "cause":
        kind = expect["cause_object"].split("/")[0]
        slashes = 1 if kind in ("Namespace", "Node", "ClusterRole", "PersistentVolume", "APIService") else 2
        assert expect["cause_object"].count("/") == slashes and expect["mechanism"] and len(expect["chain"]) >= 3
    assert len(expect["decoys"]) >= 2
