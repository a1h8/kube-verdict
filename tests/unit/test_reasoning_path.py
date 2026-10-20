"""Reasoning-path tests — docs/reasoning-path-tests.md.

Each case with ``expect.json["reasoning_path"]`` runs through the real offline workflow with an
LLM that only records what it is sent; steps 1–4 (seeds, cause, evidence, rule) are
graded by ``tools.reasoning_path``. A step that fails on a known defect is a strict
xfail naming the finding (docs/reasoning-path-tests.md § Findings), so a fix that makes
it pass is noticed.
"""
from __future__ import annotations

from functools import lru_cache

import pytest

from tools.reasoning_path import STEPS, cases_with_path, evaluate

KNOWN: dict[tuple[str, str], str] = {
    ("h007_hpa_no_metrics", "seeds"): "P3: the fixture has no Deployment, HPA or metrics-server",
    ("h016_multi_signal", "rule"): "P4: no connection-exhaustion rule (P5 fixed 2026-10-18, H26)",
}


@lru_cache(maxsize=None)
def _result(case: str) -> dict[str, tuple[str, str]]:
    return evaluate(case)


PARAMS = [pytest.param(case, step, id=f"{case.split('_')[0]}-{step}",
                       marks=[pytest.mark.xfail(reason=KNOWN[(case, step)], strict=True)]
                       if (case, step) in KNOWN else [])
          for case in cases_with_path() for step in STEPS]


@pytest.mark.parametrize("case,step", PARAMS)
def test_step(case, step):
    status, detail = _result(case)[step]
    assert status != "FAIL", f"{case} {step}: {detail}"


def test_known_failures_name_real_cases():
    cases = set(cases_with_path())
    assert {c for c, _ in KNOWN} <= cases
    assert {s for _, s in KNOWN} <= set(STEPS)
