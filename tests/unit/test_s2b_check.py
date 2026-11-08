"""tools/s2b_check.py: known verdicts on Gemini's round 2, and one failing mutant per check (docs/s2b-generation.md)."""
from __future__ import annotations

import copy
from pathlib import Path

import pytest

from tools import s2b_check as c

ROUND2 = Path(__file__).resolve().parents[2] / "tests/s2b/scenarios/gemini-round-2.yaml"


def _base() -> dict:
    return {
        "id": "x-1", "family": "quota", "question": "why can't api roll out in shop", "expected_outcome": "cause",
        "cause_object": "CronJob/shop/export", "mechanism": "its jobs never finish and fill the pods quota",
        "passes_if": "names CronJob/shop/export and why its jobs accumulate",
        "partial_if_only": ["the quota is full, without the CronJob"],
        "chain": [
            {"from": "ReplicaSet/shop/api-1", "relation": "quota", "to": "ResourceQuota/shop/q",
             "fields": "status.used.pods", "abnormal": "FailedCreate exceeded quota at T0-5m"},
            {"from": "ResourceQuota/shop/q", "relation": "pods counted", "to": "Job/shop/export-1",
             "fields": "status.active", "abnormal": "25 active jobs since T0-4h"},
        ],
        "decoys": [{"object": "Deployment/shop/cart", "looks_like": "tag drift", "why_not": "no effect"},
                   {"object": "Pod/shop/api-0", "looks_like": "old OOMKill at T0-5h", "why_not": "resolved"}],
        "timeline": ["T0-5h: OOMKill", "T0-4h: jobs pile up", "T0-5m: FailedCreate", "T0: question"],
        "objects": [f"Pod/shop/p{i}: Running" for i in range(20)] + [f"Pod/data/d{i}: Running" for i in range(5)],
        "sources": ["https://kubernetes.io/docs/concepts/policy/resource-quotas/"],
    }


def _negative() -> dict:
    s = _base()
    s.update(id="x-0", expected_outcome="no_incident", chain=[], mechanism="the quota is at 40 %, nothing is wrong",
             passes_if=c.NO_INCIDENT_PASSES, partial_if_only=[])
    s.pop("cause_object")
    return s


@pytest.fixture(scope="module")
def round2() -> dict[str, list[str]]:
    rows, errors = c.check_file(ROUND2)
    assert errors == []
    return dict(rows)


def test_known_verdicts_on_gemini_round_2(round2):
    assert len(round2) == 33
    assert any(r.startswith("A1: partial_if_only contains the cause object") for r in round2["netpol-1"])
    assert any(r.startswith("A1") for r in round2["limitrange-1"])
    assert not any(r.startswith("A2") for r in round2["quota-0"])        # its no-incident rules hold
    assert any("ValidatingWebhookConfiguration" in r for r in round2["webhook-1"])
    assert any("HelmRelease" in r for r in round2["dependency-1"])
    assert any("duplicate chain signature" in r for r in round2["limitrange-2"])


def test_the_bases_pass():
    assert c.check(_base()) == []
    assert c.check(_negative()) == []


@pytest.mark.parametrize("mutate, expected", [
    (lambda s: s.pop("sources"), "missing fields"),
    (lambda s: s.update(partial_if_only=["CronJob/shop/export alone"]), "A1"),
    (lambda s: s.update(partial_if_only=["the quota"], passes_if="names ResourceQuota/shop/q",
                        cause_object="CronJob/shop/export") or s["partial_if_only"].append("ResourceQuota/shop/q"),
     "A1"),
    (lambda s: s.update(chain=s["chain"][:1]), "A3"),
    (lambda s: s["chain"].__setitem__(1, {"from": "Deployment/shop/api", "to": "ReplicaSet/shop/api-1",
                                          "abnormal": "x"}), "A3"),
    (lambda s: s["chain"].__setitem__(1, {"from": "LimitRange/shop/l", "to": "Namespace/shop", "abnormal": "x"}),
     "A3"),
    (lambda s: s.update(cause_object="Secret/shop/tls"), "Secrets are never collected"),
    (lambda s: s["decoys"][0].update(object="APIService/v1beta1.custom.metrics.k8s.io"), "cluster-scoped"),
    (lambda s: s["decoys"][0].update(object="MetricsServer/x"), "not a kind the product reads"),
    (lambda s: s["chain"][0].update(abnormal="FailedCreate at T0-7m"), "A10"),
    (lambda s: s.update(decoys=s["decoys"][:1]), "fewer than 2 decoys"),
    (lambda s: s.update(objects=s["objects"][:24]), "A9"),
    (lambda s: s.update(objects=[f"Pod/shop/p{i}: Running" for i in range(30)]), "A9"),
    (lambda s: s.update(expected_outcome="maybe"), "neither cause nor no_incident"),
])
def test_each_check_fails_its_mutant(mutate, expected):
    s = copy.deepcopy(_base())
    mutate(s)
    assert any(expected in r for r in c.check(s)), c.check(s)


@pytest.mark.parametrize("mutate, expected", [
    (lambda s: s.update(passes_if="states no incident or names the old job"), "A2: passes_if"),
    (lambda s: s.update(chain=_base()["chain"]), "A2: a scenario with no incident has an empty chain"),
    (lambda s: s.update(mechanism="the latency is due to the external database"), "A2: the mechanism names a cause"),
])
def test_each_no_incident_check_fails_its_mutant(mutate, expected):
    s = copy.deepcopy(_negative())
    mutate(s)
    assert any(r.startswith(expected) for r in c.check(s)), c.check(s)


def test_file_level_checks_fail_their_mutants(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("id: [unclosed\n")
    assert c.check_file(bad)[1][0].startswith("invalid YAML")
    import yaml
    dup = tmp_path / "dup.yaml"
    two = copy.deepcopy(_base())
    dup.write_text(yaml.safe_dump_all([_base(), two]))
    rows, errors = c.check_file(dup)
    assert errors == ["duplicate ids: x-1"]
    assert any("duplicate chain signature" in r for r in rows[1][1])
