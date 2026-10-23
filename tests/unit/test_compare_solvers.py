"""The comparison tables count what the records say — docs/veracity-replay.md § Comparison."""
from __future__ import annotations

import json

from tools import compare_solvers as cs
from tools.veracity_replay import native_cases


def _write(root, solver, case, run, **rec):
    p = root / solver / case / f"run_{run}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"case": case, "run": run, "tier": "A", **rec}))


def test_rates_and_missing_runs(tmp_path):
    case = native_cases()[0]
    _write(tmp_path, "llm", case, 1, veracity="PASS", explanation_veracity="PASS", outcome="cause",
           remediation=["kubectl rollout restart deployment/api -n prod"], unrecoverable=[], wall_s=10)
    _write(tmp_path, "llm", case, 2, veracity="PASS", explanation_veracity="FAIL", outcome="cause",
           llm_disagreement="named another cause", remediation=["kubectl delete pvc x"],
           unrecoverable=["kubectl delete pvc x"], wall_s=30)
    recs = [r for r in cs.load(tmp_path, "LLM", [case])]
    cells = dict(zip(cs.HEADER, cs.row("LLM", recs)))
    assert cells["Report correct"] == "67 % (2/3)"            # run 3 missing counts as a FAIL
    assert cells["Explanation correct"] == "33 % (1/3)"
    assert cells["LLM disagrees"] == "50 % (1/2)"
    assert cells["Remediation proposed"] == "100 % (2/2)"
    assert cells["No irreversible command"] == "50 % (1/2)"
    assert cells["Missing"] == "1"


def test_the_structured_configuration_has_no_llm_columns(tmp_path):              # control
    case = native_cases()[0]
    _write(tmp_path, "structured", case, 1, veracity="PASS", wall_s=1)
    cells = dict(zip(cs.HEADER, cs.row("none", cs.load(tmp_path, "none", [case]))))
    assert cells["Report correct"] == "100 % (1/1)" and cells["Explanation correct"] == "n.a."
