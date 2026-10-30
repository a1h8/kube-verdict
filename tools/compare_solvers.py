#!/usr/bin/env python3
"""The comparison's tables and decisions — docs/veracity-replay.md § Comparison and its
amendments (2026-10-22, 2026-10-23, 2026-10-24) — and protocol 001's vector-search decision
(docs/protocol-001-retrieval.md, C2 against C1).

Reads replay records (``<dir>/<solver>/<case>/run_<n>.json``, cut runs under
``cut-<collector>/``), counts every expected run (a missing one is a FAIL, fix plan H7; an
invalid one is never a pass) and prints Markdown: per configuration, per tier, cases passed
(2 of 3), per case, the controls, and the decisions with the thresholds fixed before any run.
Never hand-written: re-run this tool.

    python tools/compare_solvers.py --none DIR [--llm DIR] [--sllm DIR] [--raw-llm DIR]
        [--raw-sllm DIR] [--ctx-llm DIR] [--ctx-sllm DIR] [--c2-llm DIR] [--c2-sllm DIR]
        [--out FILE]
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.veracity_replay import TIERS, cuttable, native_cases, tier_of  # noqa: E402

# name → (solver directory, runs per case); the order of the tables
CONFIGS = {"none": ("structured", 1), "LLM": ("llm", 3), "sLLM": ("llm", 3),
           "raw-LLM": ("raw", 3), "raw-sLLM": ("raw", 3),
           "ctx-LLM": ("context", 3), "ctx-sLLM": ("context", 3),
           # protocol 001: the context with the product's vector search (--store faiss)
           "C2-LLM": ("context", 3), "C2-sLLM": ("context", 3)}
RUNS = {name: runs for name, (_, runs) in CONFIGS.items()}
SOLVER_DIR = {name: solver for name, (solver, _) in CONFIGS.items()}
EXPLAINERS = ("LLM", "sLLM")            # the model words the structured diagnosis
OWN_CAUSE = ("raw-LLM", "raw-sLLM", "ctx-LLM", "ctx-sLLM", "C2-LLM", "C2-sLLM")
# held to the collector-cut control (the comparison's configurations, not protocol 001's C2)
CUT_CONTROLLED = ("none", "LLM", "sLLM", "raw-LLM", "raw-sLLM", "ctx-LLM", "ctx-sLLM")
POSITIVE_CONTROL = "h001_crashloopbackoff"
LABELS = {"": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}

# decision thresholds, fixed before any run (docs/veracity-replay.md)
MORE_CASES = 3              # "at least 3 more cases"
EXPLAIN_MIN, DISAGREE_MAX = 80.0, 10.0
SLLM_WITHIN = 5.0           # points of explanation-correct rate
ANCHOR_MIN_CASES = 4        # fewer cases whose score label changes: not conclusive
ANCHOR_FOLLOW = 0.75        # "the follow rate is at least 3/4"

# the context quality score line every analyze prompt carries (rca/analyzer.py)
SCORE_LINE = re.compile(r"Context quality score:\s*(LOW|MEDIUM|HIGH)\b")


def _expect(case: str) -> dict:
    return json.loads((ROOT / "tests/integration/cases" / case / "expect.json").read_text())


def load(root: Path, name: str, cases: list[str]) -> list[dict]:
    """Every expected run of a configuration; a run without a record is MISSING."""
    out = []
    for case in cases:
        for run in range(1, RUNS[name] + 1):
            path = root / SOLVER_DIR[name] / case / f"run_{run}.json"
            if path.exists():
                rec = json.loads(path.read_text())
            else:
                rec = {"case": case, "run": run, "tier": tier_of(case, _expect(case)),
                       "veracity": "MISSING"}
            out.append(rec)
    return out


def load_cuts(root: Path, name: str, cases: list[str]) -> list[dict]:
    """Every expected cut run (one per case and collector it has); a missing one is MISSING."""
    out = []
    for case in cases:
        for cut in cuttable(case):
            path = root / SOLVER_DIR[name] / case / f"cut-{cut}" / "run_1.json"
            out.append(json.loads(path.read_text()) if path.exists()
                       else {"case": case, "cut": cut, "veracity": "MISSING"})
    return out


def passed(r: dict) -> bool:
    """A pass: graded PASS and not invalid (truncated, a store touched)."""
    return r["veracity"] == "PASS" and not r.get("invalid")


def case_passes(recs: list[dict], runs: int) -> bool:
    """1 of 1 for a deterministic configuration, at least 2 of 3 otherwise."""
    return sum(passed(r) for r in recs) >= (1 if runs == 1 else 2)


def cases_passed(name: str, recs: list[dict]) -> set[str]:
    by_case: dict[str, list[dict]] = {}
    for r in recs:
        by_case.setdefault(r["case"], []).append(r)
    return {c for c, rs in by_case.items() if case_passes(rs, RUNS[name])}


def negatives(cases: list[str]) -> list[str]:
    return [c for c in cases if _expect(c).get("expected_outcome", "cause") != "cause"]


def false_alarms(name: str, recs: list[dict], cases: list[str]) -> list[str]:
    """Negative cases the configuration does not pass: it named a cause (case level)."""
    ok = cases_passed(name, recs)
    return [c for c in negatives(cases) if c not in ok]


def _pct(n: int, d: int) -> str:
    return f"{100 * n / d:.0f} % ({n}/{d})" if d else "—"


def _rate(n: int, d: int) -> float | None:
    return 100 * n / d if d else None


def _median(values: list[float]) -> str:
    values = [v for v in values if v is not None]
    return f"{statistics.median(values):.0f}" if values else "—"


def _diagnosed(recs: list[dict]) -> list[dict]:
    return [r for r in recs if r.get("outcome", "cause") == "cause"
            and r["veracity"] != "MISSING" and not r.get("invalid")]


def row(name: str, recs: list[dict]) -> list[str]:
    explainer, model = name in EXPLAINERS, name != "none"
    diagnosed = _diagnosed(recs)
    proposed = [r for r in diagnosed if r.get("remediation")]
    tokens = [c.get("prompt_tokens_read") for r in recs for c in r.get("llm_calls") or []]
    return [
        name,
        _pct(sum(passed(r) for r in recs), len(recs)),
        _pct(sum(r.get("explanation_veracity") == "PASS" and not r.get("invalid") for r in recs),
             len(recs)) if explainer else "n.a.",
        _pct(sum(bool(r.get("llm_disagreement")) for r in diagnosed), len(diagnosed)) if explainer else "n.a.",
        _pct(len(proposed), len(diagnosed)) if model else "n.a.",
        _pct(sum(not r.get("unrecoverable") for r in proposed), len(proposed)) if explainer else "n.a.",
        str(sum(r["veracity"] == "MISSING" for r in recs)),
        str(sum(bool(r.get("invalid")) for r in recs)),
        str(sum(bool(r.get("did_not_fit")) for r in recs)) if name in OWN_CAUSE else "n.a.",
        _median([r.get("wall_s") for r in recs]),
        _median(tokens) if model else "n.a.",
    ]


HEADER = ["Configuration", "Report correct", "Explanation correct", "LLM disagrees",
          "Remediation proposed", "No irreversible command", "Missing", "Invalid", "Did not fit",
          "Median wall s", "Median prompt tokens"]


def table(header: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines)


# ── controls ────────────────────────────────────────────────────────────────────

def _label(r: dict) -> int:
    return LABELS.get((r.get("confidence") or "").upper(), 0)


def _label_name(value: int | None) -> str:
    return next((k for k, v in LABELS.items() if v == value and k), "—")


def score_label(r: dict) -> int | None:
    """The label of the context quality score in the run's prompt; None without the line."""
    m = SCORE_LINE.search("\n".join(r.get("prompts") or []))
    return LABELS[m.group(1)] if m else None


def cut_control(recs: list[dict], cuts: list[dict]) -> list[dict]:
    """Each cut run against the lower median label of its case's valid connected runs; a cut run
    missing or invalid, or with no valid connected run to compare with, fails."""
    out = []
    for cut in cuts:
        connected = [_label(r) for r in recs if r["case"] == cut["case"]
                     and r["veracity"] != "MISSING" and not r.get("invalid")]
        median = statistics.median_low(connected) if connected else None
        if cut["veracity"] == "MISSING":
            ok, why = False, "cut run missing"
        elif cut.get("invalid"):
            ok, why = False, "cut run invalid"
        elif median is None:
            ok, why = False, "no valid connected run"
        else:
            ok = _label(cut) <= median
            why = "" if ok else "more confident than its connected runs"
        out.append({"case": cut["case"], "cut": cut.get("cut"), "ok": ok, "why": why,
                    "label": cut.get("confidence") or "—", "median": _label_name(median),
                    "score": _label_name(score_label(cut)),
                    "names_cause": cut["veracity"] == "PASS"})
    return out


def controls(name: str, recs: list[dict], cuts: list[dict] | None, cases: list[str]) -> dict:
    """Which controls the configuration fails; ``invalid`` when protocol 001's positive control
    is missed (context configurations, C1 and C2). ``cuts`` None: no collector-cut control."""
    failed, invalid = [], ""
    if alarms := false_alarms(name, recs, cases):
        failed.append("negative control: " + ", ".join(alarms) + " named a cause")
    if cuts is not None and (bad := [c for c in cut_control(recs, cuts) if not c["ok"]]):
        failed.append("collector cut: " + ", ".join(f"{c['case']} ({c['cut']}): {c['why']}"
                                                    for c in bad))
    if name.startswith(("ctx-", "C2-")) and POSITIVE_CONTROL in cases \
            and POSITIVE_CONTROL not in cases_passed(name, recs):
        invalid = f"positive control {POSITIVE_CONTROL} under 2 of 3: re-run once, then invalid"
    return {"failed": failed, "invalid": invalid}


# ── decisions ───────────────────────────────────────────────────────────────────

def compare_counts(a: set[str], b: set[str]) -> tuple[int, list[str], list[str]]:
    """(cases A passes − cases B passes, only A, only B): the difference is protocol 001's net
    gain."""
    return len(a) - len(b), sorted(a - b), sorted(b - a)


def _three_way(diff: int, better: str, none_: str) -> str:
    if diff >= MORE_CASES:
        return better
    if diff <= 0:
        return none_
    return "inconclusive (1 or 2 cases apart)"


def _round(x: float | None) -> str:
    return "—" if x is None else f"{x:.0f}"


def decisions(configs: dict[str, list[dict]], ctl: dict[str, dict], cases: list[str]) -> list[list[str]]:
    """One row per decision rule: what is compared, the counts, the outcome."""
    passed_ = {n: cases_passed(n, r) for n, r in configs.items()}
    # the rules' "no more false alarms on h022 and h026" needs no branch of its own: one false
    # alarm already fails the negative control, and no decision is drawn from that configuration

    def blocked(*names: str) -> str:
        if missing := [n for n in names if n not in configs]:
            return "not measured: " + ", ".join(missing)
        bad = [n for n in names if ctl[n]["failed"] or ctl[n]["invalid"]]
        return ("not drawn: " + ", ".join(f"{n} fails a control or is invalid" for n in bad)) if bad else ""

    def counts(a: str, b: str) -> str:
        _, only_a, only_b = compare_counts(passed_[a], passed_[b])
        return (f"{a} {len(passed_[a])}/{len(cases)}, {b} {len(passed_[b])}/{len(cases)}; "
                f"only {a}: {', '.join(only_a) or '—'}; only {b}: {', '.join(only_b) or '—'}")

    rows = []
    raws = [n for n in ("raw-LLM", "raw-sLLM") if n in configs]
    best_raw = max(raws, key=lambda n: len(passed_[n])) if raws else "raw-LLM"
    if why := blocked("none", best_raw):
        rows.append(["structured vs the better raw", "—", why])
    else:
        diff = len(passed_["none"]) - len(passed_[best_raw])
        rows.append(["structured vs the better raw", counts("none", best_raw),
                     _three_way(diff, "the structured diagnosis earns its place",
                                "the raw model passes as many cases or more: the README says so")])
    for model in ("LLM", "sLLM"):
        ctx, raw = f"ctx-{model}", f"raw-{model}"
        label = f"ontology + charts help {model} (ctx vs raw)"
        if why := blocked(ctx, raw):
            rows.append([label, "—", why])
            continue
        diff = len(passed_[ctx]) - len(passed_[raw])
        rows.append([label, counts(ctx, raw), _three_way(diff, "they help", "no measurable help")])
    ctxs = [n for n in ("ctx-LLM", "ctx-sLLM") if n in configs]
    best_ctx = max(ctxs, key=lambda n: len(passed_[n])) if ctxs else "ctx-LLM"
    if why := blocked("none", best_ctx):
        rows.append(["the rules add to the context", "—", why])
    else:
        diff = len(passed_["none"]) - len(passed_[best_ctx])
        rows.append(["the rules add to the context", counts("none", best_ctx),
                     _three_way(diff, "they add", "no measurable addition")])
    if why := blocked("none", "raw-sLLM"):
        rows.append(["raw-sLLM vs structured (reported, no rule)", "—", why])
    else:
        rows.append(["raw-sLLM vs structured (reported, no rule)", counts("raw-sLLM", "none"), "—"])
    return rows + _explainer_rows(configs, blocked)


def _explain(recs: list[dict]) -> tuple[float | None, float | None, float | None]:
    diagnosed = _diagnosed(recs)
    walls = [r["wall_s"] for r in recs if r.get("wall_s") is not None]
    return (_rate(sum(r.get("explanation_veracity") == "PASS" for r in diagnosed), len(diagnosed)),
            _rate(sum(bool(r.get("llm_disagreement")) for r in diagnosed), len(diagnosed)),
            statistics.median(walls) if walls else None)


def _explainer_rows(configs: dict[str, list[dict]], blocked) -> list[list[str]]:
    rows = []
    if why := blocked("LLM"):
        rows.append(["the LLM as explainer", "—", why])
    else:
        expl, dis, _ = _explain(configs["LLM"])
        ok = expl is not None and dis is not None and expl >= EXPLAIN_MIN and dis <= DISAGREE_MAX
        rows.append(["the LLM as explainer",
                     f"explanation correct {_round(expl)} %, disagrees {_round(dis)} % (diagnosed runs)",
                     "worth running as explainer" if ok else "LLM_PROVIDER=none recommended"])
    if why := blocked("LLM", "sLLM"):
        rows.append(["the sLLM preferred to the LLM", "—", why])
    else:
        (e1, d1, w1), (e2, d2, w2) = _explain(configs["LLM"]), _explain(configs["sLLM"])
        ok = None not in (e1, d1, w1, e2, d2, w2) and e2 >= e1 - SLLM_WITHIN and d2 <= d1 and w2 < w1
        rows.append(["the sLLM preferred to the LLM",
                     f"explanation {_round(e2)} vs {_round(e1)} %, disagrees {_round(d2)} vs "
                     f"{_round(d1)} %, median wall {_round(w2)} vs {_round(w1)} s",
                     "preferred" if ok else "not preferred"])
    return rows


# ── protocol 001: does the vector search stay in the default path? ───────────────

def _prompt_body(r: dict) -> list[str]:
    return [line for line in "\n".join(r.get("prompts") or []).splitlines()
            if not line.startswith("Analysis timestamp")]


def same_prompt_cases(c1: list[dict], c2: list[dict]) -> list[str]:
    """Cases whose first C2 prompt equals the first C1 prompt, the timestamp line aside: there,
    a difference between C1 and C2 is run-to-run variation (protocol 001, amendment
    2026-10-23)."""
    first = lambda recs: {r["case"]: r for r in recs if r.get("run") == 1 and r.get("prompts")}  # noqa: E731
    a, b = first(c1), first(c2)
    return sorted(c for c in a.keys() & b.keys() if _prompt_body(a[c]) == _prompt_body(b[c]))


def layer_outcome(c1: str, c2: str, configs: dict[str, list[dict]], ctl: dict[str, dict],
                  cases: list[str]) -> tuple[str, str]:
    """(outcome, counts) of C2 against C1 on one model, by protocol 001's decision table:
    stays / inconclusive / leaves, or not measured / not drawn."""
    if missing := [n for n in (c1, c2) if n not in configs]:
        return "not measured", ", ".join(missing) + " missing"
    if bad := [n for n in (c1, c2) if ctl[n]["invalid"]]:
        return "not drawn", ", ".join(f"{n}: {ctl[n]['invalid']}" for n in bad)
    p1, p2 = cases_passed(c1, configs[c1]), cases_passed(c2, configs[c2])
    net, only2, only1 = compare_counts(p2, p1)
    new_alarms = sorted(set(false_alarms(c2, configs[c2], cases)) - set(false_alarms(c1, configs[c1], cases)))
    same = same_prompt_cases(configs[c1], configs[c2])
    noise = [c for c in same if (c in p1) != (c in p2)]
    counts = (f"net gain {net:+d} (only {c2}: {', '.join(only2) or '—'}; only {c1}: "
              f"{', '.join(only1) or '—'}); same prompt: {len(same)} cases, of which "
              f"{len(noise)} differ by run-to-run variation ({', '.join(noise) or '—'})")
    if new_alarms:
        return "leaves", counts + "; new false alarm: " + ", ".join(new_alarms)
    return ("stays" if net >= MORE_CASES else "leaves" if net <= 0 else "inconclusive"), counts


def two_models(a: str, b: str) -> str:
    """Protocol 001's rule over the two models."""
    pair = {a, b}
    if pair & {"not measured", "not drawn"}:
        return "not drawn"
    if pair <= {"stays", "inconclusive"} and "stays" in pair:
        return "stays in the default path"
    if pair == {"stays", "leaves"}:
        return "inconclusive: the two models disagree, the README says why"
    if pair == {"leaves"}:
        return "leaves the default path (kept in the code, off by default)"
    if pair == {"inconclusive"}:
        return "inconclusive: unchanged, flagged; decided again on ≥ 30 cases"
    # one leaves, the other is inconclusive: "stays only if" — it does not stay (CPI-12)
    return "leaves the default path, flagged: one model leaves, the other is inconclusive"


# ── the context quality score: does the model's label follow it? ─────────────────
# docs/veracity-replay.md § Amendment 2026-10-24 (CPI-16)

def _valid(recs: list[dict], case: str) -> list[dict]:
    return [r for r in recs if r["case"] == case and r["veracity"] != "MISSING" and not r.get("invalid")]


def _case_label(recs: list[dict], case: str) -> int | None:
    """The lower median of the case's valid runs' labels, as for the collector-cut control."""
    labels = [_label(r) for r in _valid(recs, case)]
    return statistics.median_low(labels) if labels else None


def _case_score(recs: list[dict], case: str) -> int | None:
    """The score label of the case's first run with a prompt (the pipeline is deterministic)."""
    first = sorted((r for r in recs if r["case"] == case and r.get("prompts")), key=lambda r: r.get("run", 0))
    return score_label(first[0]) if first else None


def agreement(recs: list[dict]) -> tuple[int, int, int]:
    """(valid runs whose label equals their prompt's score label, valid runs with the line,
    valid runs without it). Reported only: the evidence moves both."""
    valid = [r for r in recs if r["veracity"] != "MISSING" and not r.get("invalid")]
    scored = [(r, s) for r in valid if (s := score_label(r)) is not None]
    return sum(_label(r) == s for r, s in scored), len(scored), len(valid) - len(scored)


def anchoring(c1: list[dict], c2: list[dict]) -> dict:
    """C2 against C1 on one model. S: cases whose score label differs between the two prompts;
    N: cases whose prompt is the same. Shown when S has at least ANCHOR_MIN_CASES cases, the
    model's label moves strictly with the score's on at least ANCHOR_FOLLOW of them, and more
    often than it moves at all on N."""
    s1 = {c: _case_score(c1, c) for c in {r["case"] for r in c1}}
    s2 = {c: _case_score(c2, c) for c in {r["case"] for r in c2}}
    changed = sorted(c for c in s1.keys() & s2.keys()
                     if s1[c] is not None and s2[c] is not None and s1[c] != s2[c])
    followed = []
    for c in changed:
        l1, l2 = _case_label(c1, c), _case_label(c2, c)
        if l1 is not None and l2 is not None and (l2 - l1) * (s2[c] - s1[c]) > 0:
            followed.append(c)
    same = [c for c in same_prompt_cases(c1, c2)
            if _case_label(c1, c) is not None and _case_label(c2, c) is not None]
    moved = [c for c in same if _case_label(c1, c) != _case_label(c2, c)]
    follow = len(followed) / len(changed) if changed else None
    move = len(moved) / len(same) if same else 0.0
    if len(changed) < ANCHOR_MIN_CASES:
        verdict = "not conclusive: anchoring not measured"
    elif follow >= ANCHOR_FOLLOW and follow > move:
        verdict = "shown: the model's labels are not informative"
    else:
        verdict = "not shown"
    return {"changed": changed, "followed": followed, "same": same, "moved": moved,
            "follow": follow, "move": move, "verdict": verdict}


def anchoring_rows(configs: dict[str, list[dict]]) -> list[list[str]]:
    rows = []
    for model in ("LLM", "sLLM"):
        c1, c2 = f"ctx-{model}", f"C2-{model}"
        if c1 not in configs or c2 not in configs:
            rows.append([f"anchoring, {model} (C2 vs C1)", f"{', '.join(n for n in (c1, c2) if n not in configs)} missing",
                         "not measured"])
            continue
        a = anchoring(configs[c1], configs[c2])
        follow = f"{len(a['followed'])}/{len(a['changed'])}" if a["changed"] else "—"
        rows.append([f"anchoring, {model} (C2 vs C1)",
                     f"score label changes on {len(a['changed'])} cases ({', '.join(a['changed']) or '—'}); "
                     f"the label follows on {follow} ({', '.join(a['followed']) or '—'}); "
                     f"same prompt: the label moves on {len(a['moved'])}/{len(a['same'])} "
                     f"({', '.join(a['moved']) or '—'})", a["verdict"]])
    return rows


def protocol_001_rows(configs: dict[str, list[dict]], ctl: dict[str, dict], cases: list[str]) -> list[list[str]]:
    outcomes, rows = {}, []
    for model in ("LLM", "sLLM"):
        outcome, counts = layer_outcome(f"ctx-{model}", f"C2-{model}", configs, ctl, cases)
        outcomes[model] = outcome
        rows.append([f"vector search, {model} (C2 vs C1)", counts, outcome])
    rows.append(["vector search, both models", "—", two_models(outcomes["LLM"], outcomes["sLLM"])])
    return rows


# ── report ──────────────────────────────────────────────────────────────────────

def render(configs: dict[str, list[dict]], cuts: dict[str, list[dict]] | None = None,
           cases: list[str] | None = None) -> str:
    cases = cases or sorted({x["case"] for r in configs.values() for x in r})
    # every configuration of the comparison is held to the collector-cut control: no cut run is
    # a missing one; protocol 001's C2 is not
    cuts = {n: ((cuts or {}).get(n) or [{"case": c, "cut": k, "veracity": "MISSING"}
                                        for c in cases for k in cuttable(c)])
            if n in CUT_CONTROLLED else None for n in configs}
    ctl = {n: controls(n, r, cuts[n], cases) for n, r in configs.items()}
    # a context configuration whose model is shown to follow the score: its cut labels say nothing
    anchored = {f"ctx-{m}" for m in ("LLM", "sLLM") if f"ctx-{m}" in configs and f"C2-{m}" in configs
                and anchoring(configs[f"ctx-{m}"], configs[f"C2-{m}"])["verdict"].startswith("shown")}
    parts = ["# The comparison: structured, LLM, sLLM, raw, context", "",
             "Generated by `tools/compare_solvers.py` — do not edit. Protocol and decision rules: "
             "`docs/veracity-replay.md` § Comparison and its amendments.", "",
             "## Per configuration", "",
             table(HEADER, [row(n, r) for n, r in configs.items()]), "", "## Per tier", ""]
    for tier in TIERS:
        rows = [row(f"{n} · tier {tier}", [x for x in r if x["tier"] == tier]) for n, r in configs.items()]
        if any(x["tier"] == tier for r in configs.values() for x in r):
            parts += [f"### Tier {tier}", "", table(HEADER, rows), ""]
    neg = negatives(cases)
    parts += ["## Cases passed (1 of 1, or 2 of 3)", "", table(
        ["Configuration", "Cases passed", "False alarms (h022, h026)", "Runs naming a cause on them"],
        [[n, f"{len(cases_passed(n, r))}/{len(cases)}", ", ".join(false_alarms(n, r, cases)) or "none",
          str(sum(x["case"] in neg and x["veracity"] == "FAIL" for x in r))]
         for n, r in configs.items()]), ""]
    head = ["Case"] + [f"{n} report" for n in configs] + [f"{n} explanation" for n in configs
                                                          if n in EXPLAINERS]
    body = []
    for case in cases:
        cells = [case]
        for n, r in configs.items():
            rs = [x for x in r if x["case"] == case]
            cells.append(f"{sum(passed(x) for x in rs)}/{len(rs)}")
        for n, r in configs.items():
            if n in EXPLAINERS:
                rs = [x for x in r if x["case"] == case]
                cells.append(f"{sum(x.get('explanation_veracity') == 'PASS' for x in rs)}/{len(rs)}")
        body.append(cells)
    parts += ["## Per case", "", table(head, body), "", "## Controls", "",
              table(["Configuration", "Failed controls", "Invalid"],
                    [[n, "; ".join(c["failed"]) or "none", c["invalid"] or "no"] for n, c in ctl.items()]),
              "", "### Collector cut", "",
              "Not applicable (no Prometheus, Loki or OTel fixtures): "
              + (", ".join(c for c in cases if not cuttable(c)) or "—") + ".", "",
              table(["Configuration", "Case", "Cut", "Cut label", "Score label in its prompt",
                     "Connected median", "Still names the cause", "Control"],
                    [[n, c["case"], c["cut"], c["label"], c["score"], c["median"],
                      "yes" if c["names_cause"] else "no",
                      ("ok" if c["ok"] else f"FAIL — {c['why']}")
                      + (" (not informative: anchoring shown)" if n in anchored else "")]
                     for n in configs if cuts[n] is not None for c in cut_control(configs[n], cuts[n])]), "",
              "### The context quality score in the prompt", "",
              "`docs/veracity-replay.md` § Amendment 2026-10-24: shown when the score label changes on at "
              f"least {ANCHOR_MIN_CASES} cases, the model's label follows it on at least 3/4 of them, and "
              "more often than it moves on same-prompt cases. Graded causes and decisions are unaffected. "
              "C2 also adds a \"Related context\" list: the score is not changed alone.", "",
              table(["Configuration", "Label equals the score's", "Runs without the line"],
                    [[n, _pct(*agreement(r)[:2]), str(agreement(r)[2])]
                     for n, r in configs.items() if n.startswith(("ctx-", "C2-"))]), "",
              table(["Rule", "Counts", "Outcome"], anchoring_rows(configs)), "",
              "## Decisions", "", table(["Rule", "Counts", "Outcome"], decisions(configs, ctl, cases)), "",
              "## Protocol 001 — vector search (C2 against C1)", "",
              "`docs/protocol-001-retrieval.md`, amendments of 2026-10-23: net gain ≥ 3 stays, 1 or 2 "
              "inconclusive, ≤ 0 or a new false alarm on h022 or h026 leaves; h001 must pass.", "",
              table(["Rule", "Counts", "Outcome"], protocol_001_rows(configs, ctl, cases)), ""]
    return "\n".join(parts)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for name in CONFIGS:
        ap.add_argument(f"--{name.lower()}", type=Path, dest=name, metavar="DIR")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    given = {n: getattr(args, n) for n in CONFIGS if getattr(args, n)}
    if "none" not in given:
        ap.error("--none is required: every decision compares with the structured diagnosis")
    cases = native_cases()
    configs = {n: load(d, n, cases) for n, d in given.items()}
    cuts = {n: load_cuts(d, n, cases) for n, d in given.items() if n in CUT_CONTROLLED}
    text = render(configs, cuts, cases)
    if args.out:
        args.out.write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
