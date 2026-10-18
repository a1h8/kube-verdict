"""Each case that declares a drift provenance finds its drift through that comparison, and only
through it — docs/three-states.md § Replay. The other cases have no configuration drift."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from rca.signals import extract_signals
from tests.integration.cases import case_loader as cl

CASES = sorted(Path(__file__).resolve().parents[1].joinpath("integration", "cases").glob("h0*"))


@pytest.mark.parametrize("case_dir", CASES, ids=lambda d: d.name.split("_")[0])
def test_drift_provenance(case_dir):
    expected = json.loads((case_dir / "expect.json").read_text()).get("drift_provenance")
    graph = cl.build_graph(cl.load_case(case_dir))
    found = {s.attr("provenance") or s.source for s in extract_signals(graph).of("drift.config")}
    if expected:
        assert found == {expected}
    else:
        assert "edited-outside-helm" not in found and "git-vs-deployed" not in found
