"""S2 regression guard (2026-11-09): what the engine reaches on the development set may grow, never shrink.

Two measures, offline and without a model, from docs/s2-context-reach.md:
- the cases whose cause object is in the context prompt (configuration B1, store off);
- the cases where D (the structured diagnosis with the engine's chains) names the expected cause object;
- and the outcome of each case with no incident, which must not change.
A run that reaches more is fine; update the floors below in the same change. A run that reaches less fails.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CASES = ROOT / "tests/s2/cases"

# floors measured at faf4c68 (M4-min with D's chains); a case listed here must stay reached
PROMPT_FLOOR = {"a2_hpa_scale_up_policy_slowed", "c1_configmap_env_changed", "c2_configmap_invalid_file",
                "d1_dependency_service_wrong_port", "l1_limitrange_default_injected", "l2_limitrange_max_lowered",
                "n1_netpol_namespace_relabel", "n2_netpol_pod_labels_changed", "q1_quota_filled_by_request_drift",
                "q2_quota_filled_by_stuck_jobs", "w2_webhook_fails_closed_expired_cert"}
D_FLOOR = {"c1_configmap_env_changed", "l1_limitrange_default_injected", "l2_limitrange_max_lowered",
           "q2_quota_filled_by_stuck_jobs"}
# q0 was a false alarm (an OOMKill over before the symptom) until the first time rule (signals/2, 2026-11-09)
NO_INCIDENT_OUTCOMES = {"a0_hpa_no_incident": "no_incident", "c0_configmap_no_incident": "no_incident",
                        "d0_dependency_no_incident": "no_incident", "l0_limitrange_no_incident": "no_incident",
                        "n0_netpol_no_incident": "no_incident", "q0_quota_no_incident": "no_incident",
                        "w0_webhook_no_incident": "no_incident"}


def _expect(case: str) -> dict:
    return json.loads((CASES / case / "expect.json").read_text())


@pytest.fixture(scope="module")
def s2():
    from tools import veracity_replay as vr
    vr.use_set("s2")
    try:
        with vr.isolated_stores():
            yield vr
    finally:
        vr.use_set("s1")


def test_the_cause_still_reaches_the_prompt(s2):
    from tools.s2_context_reach import present, prompt_for
    reached = set()
    for case in PROMPT_FLOOR:
        _, expect, prompt = prompt_for(case, "stub")
        if present(prompt, expect["cause_object"]):
            reached.add(case)
    assert PROMPT_FLOOR <= reached, f"no longer in the prompt: {sorted(PROMPT_FLOOR - reached)}"


def test_d_still_names_the_cause_and_keeps_the_no_incident_outcomes(s2):
    named, outcomes = set(), {}
    for case in sorted(D_FLOOR | set(NO_INCIDENT_OUTCOMES)):
        r = s2.solve_structured(case)
        exp = _expect(case)
        if exp["expected_outcome"] == "cause" and s2.cause_named(exp["cause_object"], r["root_cause"]):
            named.add(case)
        outcomes[case] = r["outcome"]
    assert D_FLOOR <= named, f"D no longer names the cause in: {sorted(D_FLOOR - named)}"
    changed = {c: (o, outcomes[c]) for c, o in NO_INCIDENT_OUTCOMES.items() if outcomes[c] != o}
    assert not changed, f"a case with no incident changed its outcome (expected, got): {changed}"
