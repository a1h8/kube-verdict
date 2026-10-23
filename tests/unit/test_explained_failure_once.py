"""A failure a specific rule explains strengthens no other drift — docs/fix-plan-001.md P7."""
from __future__ import annotations

import pytest

from rca.diagnosis import diagnose
from rca.signals import extract_signals
from tests.integration.workflow_harness import run_case
from tools.reasoning_path import _RecordOnly
from tools.veracity_replay import _key


def _rules(case: str) -> list[str]:
    return [d.rule for d in diagnose(extract_signals(run_case(_key(case), llm=_RecordOnly()).graph)).diagnoses]


@pytest.mark.parametrize("case", ["h003_oomkilled", "h012_gitops_render_vs_live"])
def test_the_oom_drift_is_not_outranked_by_another_drift_of_the_same_pod(case):
    # h003: requests.memory also drifted; h012: spec.replicas — each was lifted by the OOM
    assert _rules(case) == ["oom.memory_drift"]


def test_an_unexplained_failure_still_strengthens_its_drift():                  # control
    assert _rules("h009_liveness_probe_loop")[0] == "config.drift"
