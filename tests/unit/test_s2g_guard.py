"""S2-GitOps guard (docs/s2g/gitops-cases.md): what D names may grow, never shrink; g0 stays no_incident.
Floors measured at the baseline of 2026-11-10 (engine 1b418bd): no case named."""
import json

import pytest

D_FLOOR: set[str] = set()
NO_INCIDENT_OUTCOMES = {"g0_gitops_no_incident": "no_incident"}


@pytest.fixture(scope="module")
def s2g():
    from tools import veracity_replay as vr
    vr.use_set("s2g")
    try:
        with vr.isolated_stores():
            yield vr
    finally:
        vr.use_set("s1")


def test_frozen_cases_unchanged(s2g):
    assert s2g.frozen_mismatches(s2g.native_cases()) == []


def test_d_floor_and_no_incident(s2g):
    named, outcomes = set(), {}
    for case in sorted(D_FLOOR | set(NO_INCIDENT_OUTCOMES)):
        r = s2g.solve_structured(case)
        exp = json.loads((s2g.CASES_ROOT / case / "expect.json").read_text())
        causes = exp.get("cause_objects") or [exp["cause_object"]]
        if exp["expected_outcome"] == "cause" and all(s2g.cause_named(c, r["root_cause"]) for c in causes):
            named.add(case)
        outcomes[case] = r["outcome"]
    assert D_FLOOR <= named
    assert outcomes == NO_INCIDENT_OUTCOMES
