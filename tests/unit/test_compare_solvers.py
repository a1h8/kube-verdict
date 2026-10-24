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


# ── docs/veracity-replay.md § Amendments 2026-10-22 and 2026-10-23: cases, controls, decisions ──

import pytest  # noqa: E402

CASES = native_cases()
POSITIVES = [c for c in CASES if c not in cs.negatives(CASES)]


def _recs(name, passing, cases=CASES, confidence="MEDIUM", **kw):
    """Every run of ``name``; a run passes when ``passing(case, run)`` (or case in ``passing``)."""
    out = []
    for case in cases:
        for run in range(1, cs.RUNS[name] + 1):
            ok = passing(case, run) if callable(passing) else case in passing
            out.append({"case": case, "run": run, "tier": "A", "veracity": "PASS" if ok else "FAIL",
                        "confidence": confidence, **kw})
    return out


def _cuts(confidence="LOW", cases=CASES):
    return [{"case": c, "cut": k, "veracity": "FAIL", "confidence": confidence}
            for c in cases for k in cs.cuttable(c)]


def _decide(configs, cuts=None):
    cuts = cuts or {n: _cuts() for n in configs}
    ctl = {n: cs.controls(n, r, cuts[n], CASES) for n, r in configs.items()}
    return {rule: outcome for rule, _, outcome in cs.decisions(configs, ctl, CASES)}, ctl


ALL = set(CASES)


def test_a_case_passes_on_two_runs_of_three():
    assert cs.case_passes([{"veracity": "PASS"}, {"veracity": "PASS"}, {"veracity": "FAIL"}], 3)
    assert not cs.case_passes([{"veracity": "PASS"}, {"veracity": "FAIL"}, {"veracity": "FAIL"}], 3)
    # an invalid run is never a pass (truncated, a store touched)
    assert not cs.case_passes([{"veracity": "PASS"}, {"veracity": "PASS", "invalid": ["truncated"]},
                               {"veracity": "FAIL"}], 3)


@pytest.mark.parametrize("raw_passes, outcome", [
    (16, "the structured diagnosis earns its place"),
    (17, "inconclusive (1 or 2 cases apart)"),
    (19, "the raw model passes as many cases or more: the README says so"),
])
def test_structured_against_the_better_raw(raw_passes, outcome):
    raw = set(cs.negatives(CASES)) | set(POSITIVES[:raw_passes - 2])
    out, _ = _decide({"none": _recs("none", ALL), "raw-LLM": _recs("raw-LLM", raw),
                      "raw-sLLM": _recs("raw-sLLM", set(cs.negatives(CASES)))})
    assert out["structured vs the better raw"] == outcome


@pytest.mark.parametrize("ctx_more, outcome", [(3, "they help"), (2, "inconclusive (1 or 2 cases apart)"),
                                               (0, "no measurable help")])
def test_the_context_against_raw_on_one_model(ctx_more, outcome):
    neg = set(cs.negatives(CASES))
    raw = neg | set(POSITIVES[:5])
    ctx = raw | set(POSITIVES[5:5 + ctx_more]) | {cs.POSITIVE_CONTROL}
    raw |= {cs.POSITIVE_CONTROL}
    out, _ = _decide({"none": _recs("none", ALL), "raw-LLM": _recs("raw-LLM", raw),
                      "ctx-LLM": _recs("ctx-LLM", ctx)})
    assert out["ontology + charts help LLM (ctx vs raw)"] == outcome


def test_a_false_alarm_fails_the_control_and_no_decision_is_drawn():
    no_h026 = ALL - {"h026_rollout_in_progress"}
    out, ctl = _decide({"none": _recs("none", ALL), "raw-LLM": _recs("raw-LLM", no_h026)})
    assert "h026_rollout_in_progress named a cause" in ctl["raw-LLM"]["failed"][0]
    assert out["structured vs the better raw"].startswith("not drawn: raw-LLM")
    out, ctl = _decide({"none": _recs("none", ALL), "raw-LLM": _recs("raw-LLM", ALL)})   # control
    assert ctl["raw-LLM"]["failed"] == [] and not out["structured vs the better raw"].startswith("not")


def test_the_positive_control_makes_a_context_configuration_invalid():
    without_h001 = ALL - {cs.POSITIVE_CONTROL}
    out, ctl = _decide({"none": _recs("none", ALL), "ctx-LLM": _recs("ctx-LLM", without_h001)})
    assert ctl["ctx-LLM"]["invalid"].startswith("positive control")
    assert out["the rules add to the context"].startswith("not drawn: ctx-LLM")
    _, ctl = _decide({"none": _recs("none", ALL), "ctx-LLM": _recs("ctx-LLM", ALL)})     # control
    assert ctl["ctx-LLM"]["invalid"] == ""


def test_the_collector_cut_control():
    h013 = "h013_slo_error_budget_burn"
    connected = [{"case": h013, "veracity": "PASS", "confidence": c} for c in ("HIGH", "MEDIUM", "MEDIUM")]
    check = lambda cut: cs.cut_control(connected, [{"case": h013, "cut": "prometheus", **cut}])[0]  # noqa: E731
    assert check({"veracity": "FAIL", "confidence": "MEDIUM"})["ok"]                 # equal: ok
    assert not check({"veracity": "FAIL", "confidence": "HIGH"})["ok"]               # above the median
    assert not check({"veracity": "MISSING"})["ok"]
    assert not check({"veracity": "FAIL", "confidence": "LOW", "invalid": ["truncated"]})["ok"]
    # two valid runs, HIGH and LOW: the lower median, so a lost run never raises it
    two = [{"case": h013, "veracity": "PASS", "confidence": c} for c in ("HIGH", "LOW")]
    assert not cs.cut_control(two, [{"case": h013, "cut": "prometheus", "veracity": "FAIL",
                                     "confidence": "MEDIUM"}])[0]["ok"]


def test_a_configuration_without_cut_runs_fails_the_cut_control():
    text = cs.render({"none": _recs("none", ALL)}, None, CASES)
    assert "cut run missing" in text
    assert "| none | none | no |" not in text                                     # it failed
    assert "| none | none | no |" in cs.render({"none": _recs("none", ALL)}, {"none": _cuts()}, CASES)


def _explainers(expl_ok, dis, cases=POSITIVES[:10], wall=10.0):
    """LLM runs on ``cases``: the first ``expl_ok`` explanations correct, ``dis`` disagreements."""
    recs = _recs("LLM", ALL, cases=cases, outcome="cause", wall_s=wall)
    for i, r in enumerate(recs):
        r["explanation_veracity"] = "PASS" if i < expl_ok else "FAIL"
        r["llm_disagreement"] = "named another cause" if i < dis else ""
    return recs


def _measured(configs):
    return lambda *names: "" if all(n in configs for n in names) else "not measured"


@pytest.mark.parametrize("expl_ok, dis, outcome", [
    (24, 3, "worth running as explainer"),            # 80 %, 10 %
    (23, 3, "LLM_PROVIDER=none recommended"),         # 77 %
    (24, 4, "LLM_PROVIDER=none recommended"),         # 13 %
])
def test_the_llm_as_explainer(expl_ok, dis, outcome):
    configs = {"LLM": _explainers(expl_ok, dis)}
    rows = {rule: out for rule, _, out in cs._explainer_rows(configs, _measured(configs))}
    assert rows["the LLM as explainer"] == outcome


@pytest.mark.parametrize("sllm, outcome", [
    (dict(expl_ok=23, dis=3, wall=5.0), "preferred"),        # 77 vs 80: within 5 points, faster
    (dict(expl_ok=22, dis=3, wall=5.0), "not preferred"),    # 73: more than 5 points below
    (dict(expl_ok=24, dis=4, wall=5.0), "not preferred"),    # disagrees more
    (dict(expl_ok=24, dis=3, wall=10.0), "not preferred"),   # not faster
])
def test_the_sllm_preferred_to_the_llm(sllm, outcome):
    configs = {"LLM": _explainers(24, 3), "sLLM": _explainers(**sllm)}
    rows = {rule: out for rule, _, out in cs._explainer_rows(configs, _measured(configs))}
    assert rows["the sLLM preferred to the LLM"] == outcome


def test_the_cut_runs_are_read_from_their_directory(tmp_path):
    path = tmp_path / "structured/h013_slo_error_budget_burn/cut-prometheus/run_1.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"case": "h013_slo_error_budget_burn", "cut": "prometheus",
                                "veracity": "FAIL", "confidence": "LOW"}))
    cuts = cs.load_cuts(tmp_path, "none", ["h013_slo_error_budget_burn", "h016_multi_signal"])
    assert [(c["case"], c["cut"], c["veracity"]) for c in cuts] == [
        ("h013_slo_error_budget_burn", "prometheus", "FAIL"),
        ("h016_multi_signal", "prometheus", "MISSING"), ("h016_multi_signal", "loki", "MISSING"),
        ("h016_multi_signal", "otel", "MISSING")]


def test_the_structured_configuration_is_required(tmp_path):
    with pytest.raises(SystemExit):
        cs.main(["--raw-llm", str(tmp_path)])


# ── protocol 001: C2 against C1 ─────────────────────────────────────────────────

def _layer(c1_pass, c2_pass, prompts=None):
    configs = {"ctx-LLM": _recs("ctx-LLM", c1_pass), "C2-LLM": _recs("C2-LLM", c2_pass)}
    if prompts:
        for name, recs in configs.items():
            for r in recs:
                r["prompts"] = [prompts(name, r["case"])]
    ctl = {n: cs.controls(n, r, None, CASES) for n, r in configs.items()}
    return cs.layer_outcome("ctx-LLM", "C2-LLM", configs, ctl, CASES)


BASE = set(cs.negatives(CASES)) | {cs.POSITIVE_CONTROL} | set(POSITIVES[1:6])


@pytest.mark.parametrize("more, outcome", [(3, "stays"), (2, "inconclusive"), (0, "leaves")])
def test_the_vector_search_decision(more, outcome):
    extra = set(POSITIVES[6:6 + more])
    assert _layer(BASE, BASE | extra)[0] == outcome


def test_a_net_loss_leaves():
    assert _layer(BASE, BASE - {POSITIVES[1]})[0] == "leaves"


def test_a_new_false_alarm_leaves_whatever_the_gain():
    outcome, counts = _layer(BASE, (BASE | set(POSITIVES[6:12])) - {"h022_no_incident"})
    assert outcome == "leaves" and "new false alarm: h022_no_incident" in counts


def test_a_c2_without_the_positive_control_draws_nothing():
    assert _layer(BASE, BASE - {cs.POSITIVE_CONTROL})[0] == "not drawn"


def test_on_the_same_prompt_a_difference_is_noise():
    same = lambda name, case: "Analysis timestamp: x\nsame"  # noqa: E731
    outcome, counts = _layer(BASE, BASE | {POSITIVES[7]}, prompts=same)
    assert f"of which 1 differ by run-to-run variation ({POSITIVES[7]})" in counts
    other = lambda name, case: f"{name}\nprompt"  # noqa: E731                       # control
    assert "same prompt: 0 cases" in _layer(BASE, BASE, prompts=other)[1]


@pytest.mark.parametrize("a, b, outcome", [
    ("stays", "stays", "stays in the default path"),
    ("stays", "inconclusive", "stays in the default path"),
    ("stays", "leaves", "inconclusive: the two models disagree, the README says why"),
    ("leaves", "leaves", "leaves the default path (kept in the code, off by default)"),
    ("inconclusive", "inconclusive", "inconclusive: unchanged, flagged; decided again on ≥ 30 cases"),
    ("leaves", "inconclusive", "leaves the default path, flagged: one model leaves, the other is inconclusive"),
    ("stays", "not drawn", "not drawn"),
])
def test_the_two_models_rule(a, b, outcome):
    assert cs.two_models(a, b) == outcome == cs.two_models(b, a)


def test_c2_is_not_held_to_the_collector_cut_control():
    text = cs.render({"none": _recs("none", ALL), "C2-LLM": _recs("C2-LLM", ALL)},
                     {"none": _cuts()}, CASES)
    assert "| C2-LLM | none | no |" in text
