"""S2's blind grading (docs/s2/grading.md): the pack hides the arm, the key restores it, the verdicts combine
as written. On synthetic records: no S2 run exists before the measurement."""
from __future__ import annotations

import json

import pytest

from tools.s2_grading_pack import build, instructions, truth
from tools.s2_grading_score import combine, kappa, read_verdicts, score

Q1, Q0 = "q1_quota_filled_by_request_drift", "q0_quota_no_incident"


@pytest.fixture
def records(tmp_path):
    dirs = []
    for arm, solver, model, runs in (("d", "structured", "", 1), ("l", "context", "mistral", 3)):
        d = tmp_path / arm
        for case in (Q1, Q0):
            for run in range(1, runs + 1):
                p = d / solver / case / f"run_{run}.json"
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(json.dumps({"set": "S2", "case": case, "solver": solver, "model": model, "run": run,
                                         "root_cause": f"answer {arm} {case[:2]} {run}"}))
        dirs.append(d)
    return dirs


def test_the_instructions_are_the_documents_verbatim():
    text = instructions()
    assert text.startswith("You are grading answers") and "choose the stricter" in text and ">" not in text[:2]


def test_the_truth_comes_from_expect_and_a_negative_case_is_no_incident():
    gt, passes, partial = truth(json.loads(open(f"tests/s2/cases/{Q1}/expect.json").read()))
    assert gt.startswith("Deployment/shop/catalog-api:") and "LimitRange" not in gt and partial
    assert truth({"expected_outcome": "no_incident"})[0] == "No incident."


def test_the_pack_hides_the_arm_and_the_key_restores_it(records):
    pack, key = build(records, seed=1)
    assert len(key) == 8 and {k["arm"] for k in key} == {"D", "L0"}
    body = pack.split("There are 8 items.", 1)[1]
    assert not any(w in body for w in ("structured", "mistral", "qwen", "L0", "arm"))
    assert build(records, seed=1)[1] == key and build(records, seed=2)[1] != key       # the seed decides


def test_verdicts_combine_as_written():
    a = read_verdicts("| item | verdict | reason |\n|---|---|---|\n| 1 | YES | x |\n| 2 | NO | y |\n| 3 | PARTIAL | z |")
    b = {1: "YES", 2: "YES", 3: "PARTIAL"}
    assert combine(a, b, None) == {1: "YES", 2: "DISPUTED", 3: "PARTIAL"}
    assert combine(a, b, {2: "NO"})[2] == "NO"                                    # the third reading decides
    with pytest.raises(ValueError):
        combine(a, {1: "YES"}, None)                                              # every item by both graders
    assert kappa(["YES", "NO"], ["YES", "NO"]) == 1.0


def test_a_case_passes_on_two_runs_of_three_and_d_on_its_one(records):
    _, key = build(records, seed=1)
    item = {(k["arm"], k["case"], k["run"]): k["item"] for k in key}
    verdicts = {i: "NO" for i in item.values()}
    verdicts[item[("D", Q1, 1)]] = "YES"
    verdicts[item[("L0", Q1, 1)]] = verdicts[item[("L0", Q1, 2)]] = "YES"
    verdicts[item[("L0", Q0, 1)]] = "YES"                                         # 1 of 3: not a pass
    out = score(key, verdicts)
    assert out["D · all"] == {"pass": 1, "cases": 2, "no": 1}
    assert out["L0 · all"]["pass"] == 1 and out["L0 · quota"]["cases"] == 2
