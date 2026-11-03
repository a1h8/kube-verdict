#!/usr/bin/env python3
"""Reasoning-path evaluation — which step of the diagnosis loses the cause.

Spec: docs/reasoning-path-tests.md. Each native case declares ``expect.json["reasoning_path"]``;
this runs the case through the real offline workflow (``workflow_harness.run_case``)
with an LLM that only records what it is sent, and grades steps 0–4:

0. signals      the expected signal kinds are extracted (docs/structured-diagnosis.md)
1. seeds        ``find_unhealthy`` picks the faulty workloads, and only them
2. cause        the cause object is reached (or, when absent, reported as missing)
3. evidence     each marker reaches the analyze prompt, in the section it belongs to
4. rule         the expected rule is in the top 3 on the right object, above decoys

Usage::

    python tools/reasoning_path.py            # all cases with a path → tests/veracity/PATH.md
    python tools/reasoning_path.py h016 h024  # print those cases only
"""
from __future__ import annotations

import fnmatch
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CASES_ROOT = ROOT / "tests/integration/cases"
REPORT = ROOT / "tests/veracity/PATH.md"
STEPS = ("signals", "seeds", "cause", "evidence", "rule")
TOP_K = 3

# section name in expect.json → header of that section in ContextWindow.to_prompt_block()
SECTIONS = {
    "seeds": "### CRITICAL — Unhealthy resources",
    "drift": "### CRITICAL — Helm declared vs observed drift",
    "alerts": "### CRITICAL — Firing Prometheus alerts",
    "traces": "### TRACES —",
    "logs": "### LOGS —",
    "events": "### WARNING — Kubernetes events",
    "anchors": "### ANCHORS —",
}

_LOW = """### 1. Summary
Recording only.

### 3. Root cause
Not established.

### 7. Confidence
LOW
"""


class _RecordOnly:
    """Answers nothing useful: no hypotheses of its own, LOW to every analyze call."""

    model = "record-only"

    def is_available(self) -> bool:
        return True

    def model_is_pulled(self) -> bool:
        return True

    def generate(self, prompt: str, system: str | None = None, **_: object) -> str:
        return "" if prompt.startswith("Kubernetes SRE expert.") else _LOW


def cases_with_path() -> list[str]:
    return [d.name for d in sorted(CASES_ROOT.glob("h[0-9]*/"))
            if "reasoning_path" in json.loads((d / "expect.json").read_text())]


def _match(fqn: str, pattern: str) -> bool:
    return fnmatch.fnmatchcase(fqn, pattern)


def _section(prompt: str, header: str) -> str:
    start = prompt.find(header)
    if start < 0:
        return ""
    end = prompt.find("\n### ", start + len(header))
    return prompt[start:end if end >= 0 else len(prompt)]


def _signals(path: dict, kinds: set[str]) -> tuple[str, str]:
    """docs/structured-diagnosis.md § 4: the case's expected signal kinds are extracted."""
    if "signals_expected" not in path:
        return "n.a.", "no signals declared"
    missing = [k for k in path["signals_expected"] if k not in kinds]
    forbidden = [k for k in path.get("signals_forbidden", []) if k in kinds]
    if missing or forbidden:
        parts = ([f"not extracted: {', '.join(missing)}"] if missing else []) + \
                ([f"wrongly extracted: {', '.join(forbidden)}"] if forbidden else [])
        return "FAIL", "; ".join(parts)
    return "PASS", ", ".join(sorted(kinds)) or "none"


def _seeds(path: dict, seeds: set[str]) -> tuple[str, str]:
    missing = [p for p in path.get("seeds_include", []) if not any(_match(s, p) for s in seeds)]
    extra = sorted(s for s in seeds for p in path.get("seeds_exclude", []) if _match(s, p))
    if missing or extra:
        parts = ([f"not a seed: {', '.join(missing)}"] if missing else []) + \
                ([f"wrongly a seed: {', '.join(extra)}"] if extra else [])
        return "FAIL", "; ".join(parts)
    return "PASS", f"{len(seeds)} seed(s)"


def _cause(path: dict, graph, seed_entities, reached: set[str]) -> tuple[str, str]:
    cause = path.get("cause")
    if not cause:
        return "n.a.", "no cause (negative case)"
    problems, notes = [], []
    if "object" in cause:
        if cause["object"] in reached:
            notes.append(f"reached {cause['object']}")
        else:
            problems.append(f"{cause['object']} not reached from the seeds")
    if "absent" in cause:
        kind, _, name = cause["absent"].split("/", 2)
        key = f"missing.{kind.lower()}.{name}"
        if any(key in e.annotations for e in graph.entities()):
            notes.append(f"absence recorded ({key})")
        else:
            problems.append(f"absence of {cause['absent']} not recorded ({key})")
    if "outside_graph" in cause:
        notes.append(f"{cause['outside_graph']} is outside the graph: evidence only")
    if problems:
        return "FAIL", "; ".join(problems)
    return ("PASS" if "object" in cause or "absent" in cause else "n.a."), "; ".join(notes)


def _evidence(path: dict, prompt: str) -> tuple[str, str]:
    items = path.get("evidence", [])
    if not items:
        return "n.a.", "no evidence declared"
    if not prompt:
        return "FAIL", "the model was never called"
    problems = []
    for item in items:
        header = SECTIONS[item["section"]]
        if item["marker"] in _section(prompt, header):
            continue
        elsewhere = [s for s, h in SECTIONS.items() if item["marker"] in _section(prompt, h)]
        problems.append(f"'{item['marker']}' not in {item['section']}"
                        + (f" (found in {', '.join(elsewhere)})" if elsewhere else " (absent)"))
    return ("FAIL", "; ".join(problems)) if problems else ("PASS", f"{len(items)} marker(s)")


def _rank_of(ranked: list[tuple[str, str]], rule: str, affected: str | None) -> int | None:
    for i, (rid, fqn) in enumerate(ranked):
        if rid == rule and (affected is None or _match(fqn, affected)):
            return i
    return None


def _rule(path: dict, ranked: list[tuple[str, str]]) -> tuple[str, str]:
    top = ranked[:TOP_K]
    shown = ", ".join(f"{r} on {a}" for r, a in top) or "none"
    expected = path.get("rule")
    if expected is None:
        return ("PASS", "no rule fires") if not top else ("FAIL", f"a rule fires on a case with none: {shown}")
    if isinstance(expected, dict) and "missing" in expected:
        return "FAIL", f"no rule exists for: {expected['missing']} (top: {shown})"
    wanted = expected if isinstance(expected, list) else [expected]
    problems, best = [], len(ranked)
    for w in wanted:
        rank = _rank_of(top, w["id"], w.get("affected"))
        if rank is None:
            problems.append(f"{w['id']} on {w.get('affected')} not in top {TOP_K}")
        else:
            best = min(best, rank)
    for decoy in path.get("decoy_rules", []):
        d = decoy if isinstance(decoy, dict) else {"id": decoy}
        rank = _rank_of(ranked, d["id"], d.get("affected"))
        if rank is not None and rank < best:
            problems.append(f"decoy {d['id']} ranks {rank + 1}, above the expected rule")
    return ("FAIL", f"{'; '.join(problems)} (top: {shown})") if problems else ("PASS", shown)


def evaluate(case_name: str) -> dict[str, tuple[str, str]]:
    """{step: (PASS | FAIL | n.a., detail)} for one case."""
    from dedup.bfs import expand_incident_context, find_unhealthy
    from rca.diagnosis import diagnose
    from tests.integration.workflow_harness import run_case

    path = json.loads((CASES_ROOT / case_name / "expect.json").read_text())["reasoning_path"]
    run = run_case(case_name.split("_", 1)[0], llm=_RecordOnly())
    graph = run.graph
    seed_entities = find_unhealthy(graph)
    seeds = {e.fqn for e in seed_entities}
    reached = {e.fqn for e in expand_incident_context(graph, seed_entities)}
    prompt = run.llm.analyze_prompts[0] if run.llm.analyze_prompts else ""
    from rca.signals import extract_signals
    signals = extract_signals(graph)
    kinds = signals.kinds()
    # the rule step grades the structured diagnoses — the product's only engine since
    # docs/structured-in-product.md (rule 7); the old RemediationEngine is no longer graded
    ranked = [(d.rule, d.cause_location) for d in diagnose(signals).diagnoses]
    return {
        "signals": _signals(path, kinds),
        "seeds": _seeds(path, seeds),
        "cause": _cause(path, graph, seed_entities, reached),
        "evidence": _evidence(path, prompt),
        "rule": _rule(path, ranked),
    }


def render(results: dict[str, dict[str, tuple[str, str]]]) -> str:
    lines = ["# Reasoning-path report", "",
             "Generated by `tools/reasoning_path.py` — do not edit. "
             "Spec: `docs/reasoning-path-tests.md`.", "",
             "| Case | " + " | ".join(STEPS) + " | First failing step |",
             "|---|" + "---|" * (len(STEPS) + 1)]
    for case, steps in results.items():
        first = next((s for s in STEPS if steps[s][0] == "FAIL"), "—")
        lines.append(f"| {case} | " + " | ".join(steps[s][0] for s in STEPS) + f" | {first} |")
    lines += ["", "## Details", ""]
    for case, steps in results.items():
        lines.append(f"**{case}**")
        lines += [f"- {s}: {steps[s][0]} — {steps[s][1]}" for s in STEPS]
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    import logging
    logging.disable(logging.CRITICAL)
    names = [c for c in cases_with_path() if not argv or c.split("_", 1)[0] in argv]
    results = {c: evaluate(c) for c in names}
    report = render(results)
    if argv:
        print(report)
    else:
        REPORT.write_text(report + "\n")
        print(f"wrote {REPORT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
