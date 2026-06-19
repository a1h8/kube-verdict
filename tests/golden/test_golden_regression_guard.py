"""Golden-scenario regression guard.

Replays the DecisionEngine over the h001–h010 decision fixtures and diffs the
verdict / blast-radius risk against the recorded baseline (``baseline.json``).
Any drift in the decision logic fails CI with an explicit per-scenario diff,
so a change to thresholds / blast-radius / policy gate can't silently alter
what KubeVerdict would decide.

Regenerate the baseline intentionally with:
    python -m tests.golden.update_baseline
"""
from __future__ import annotations

import json
from pathlib import Path

from tests.golden.scenarios import SCENARIOS, replay_all

BASELINE = Path(__file__).parent / "baseline.json"


def test_golden_baseline_covers_all_scenarios():
    baseline = json.loads(BASELINE.read_text())
    assert set(baseline) == set(SCENARIOS), "baseline and scenarios are out of sync"


def test_golden_decision_replay_matches_baseline():
    baseline = json.loads(BASELINE.read_text())
    current = replay_all()

    drift = {
        cid: {"baseline": baseline[cid], "current": current[cid]}
        for cid in baseline
        if current.get(cid) != baseline[cid]
    }
    assert not drift, "DecisionEngine verdict drift vs golden baseline:\n" + json.dumps(drift, indent=2)
