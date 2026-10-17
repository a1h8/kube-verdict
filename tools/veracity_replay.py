#!/usr/bin/env python3
"""Veracity replay — grade a real model (or the rules alone) on the native cases.

Spec: docs/test-cases.md → *Veracity replay harness*; why: docs/veracity-replay.md.

Runs each case through the same offline workflow as the CI tests
(``workflow_harness.run_case``: real collectors on fixtures, real LangGraph
workflow), but answered by a real model instead of ``ScriptedLLM``. No cluster
is touched. Every run is graded with ``tools.veracity.grade`` and recorded as
``tests/veracity/replay/<solver>/<case>/run_<n>.json``; ``REPORT.md`` is
rewritten after each run.

Usage::

    python tools/veracity_replay.py --solver rules
    python tools/veracity_replay.py --solver llm --runs 3 --resume
    python tools/veracity_replay.py --solver llm:mistral --cases h013,h014
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.veracity import grade, normalize_label  # noqa: E402

CASES_ROOT = ROOT / "tests/integration/cases"
REPLAY_DIR = ROOT / "tests/veracity/replay"
GRADING_KEYS = ("root_cause_concepts", "root_cause_concepts_all",
                "root_cause_contains", "root_cause_contains_all",
                "root_cause_must_not_contain", "expected_outcome", "expected_verdict")
TIERS = ("A", "B", "C", "M")


# ── pure helpers (unit-tested) ──────────────────────────────────────────────────

def tier_of(case_name: str, expect: dict) -> str:
    """A h001–h012, B h013–h016, C h017–h029, M h030+; ``expect["tier"]`` overrides."""
    if expect.get("tier"):
        return expect["tier"]
    n = int(re.match(r"h(\d+)", case_name).group(1))
    return "A" if n <= 12 else "B" if n <= 16 else "C" if n <= 29 else "M"


def grading_expect(expect: dict) -> dict:
    return {k: expect[k] for k in GRADING_KEYS if k in expect}


def answerable(expect: dict, prompts: list[str]) -> bool | None:
    """Is the ground truth readable from what the model was given?

    ``None`` for negative cases (nothing to find) or when no prompt was recorded.
    """
    if expect.get("expected_outcome", "cause") != "cause" or not prompts:
        return None
    truth = {k: expect[k] for k in ("root_cause_concepts", "root_cause_concepts_all",
                                    "root_cause_contains", "root_cause_contains_all") if k in expect}
    return grade("\n".join(prompts), truth)["veracity"] == "PASS"


def native_cases() -> list[str]:
    return [d.name for d in sorted(CASES_ROOT.glob("h[0-9]*/"))
            if any(k in json.loads((d / "expect.json").read_text()) for k in GRADING_KEYS)]


def _key(case_name: str) -> str:
    return case_name.split("_", 1)[0]


# ── solvers ─────────────────────────────────────────────────────────────────────

def _base_record(case_name: str, expect: dict, solver: str, model: str, run: int) -> dict:
    from rca.analyzer import PROMPT_VERSION
    return {"case": case_name, "tier": tier_of(case_name, expect), "solver": solver,
            "model": model, "run": run, "prompt": PROMPT_VERSION,
            "expected_outcome": expect.get("expected_outcome", "cause"),
            "started_at": datetime.now(timezone.utc).isoformat()}


def solve_rules(case_name: str) -> dict:
    """Top RemediationEngine hypothesis on the case graph, no LLM, label LOW."""
    from rca.remediation_engine import RemediationEngine
    from tests.integration.cases.case_loader import build_graph, load_case

    t0 = time.perf_counter()
    case = load_case(CASES_ROOT / case_name)
    expect = case["expect"]
    hypotheses = RemediationEngine().score(build_graph(case))
    top = hypotheses[0] if hypotheses else None
    root_cause = f"{top.symptom}. {top.explanation} ({top.affected})" if top else ""
    record = _base_record(case_name, expect, "rules", "", 1)
    # no policy gate runs for the rules solver, so expected_verdict is not checked
    rules_expect = {k: v for k, v in grading_expect(expect).items() if k != "expected_verdict"}
    record.update(grade(root_cause, rules_expect, confidence="LOW"))
    record.update({
        "wall_s": round(time.perf_counter() - t0, 2), "answerable": None,
        "confidence": "LOW", "pre_llm_confidence_score": None, "verdict": None,
        "fallbacks": [], "signals_mode": None, "root_cause": root_cause,
        "rule_id": top.rule_id if top else None,
    })
    return record


def solve_structured(case_name: str) -> dict:
    """Typed signals → typed diagnoses, no LLM (docs/structured-diagnosis.md). Deterministic."""
    from rca.diagnosis import RULES_VERSION, diagnose
    from rca.signals import EXTRACTOR_VERSION, extract_signals
    from tests.integration.workflow_harness import run_case
    from tools.reasoning_path import _RecordOnly

    t0 = time.perf_counter()
    run = run_case(_key(case_name), llm=_RecordOnly())
    signals = extract_signals(run.graph)
    outcome = diagnose(signals)
    expect = run.expect
    record = _base_record(case_name, expect, "structured", "", 1)
    structured_expect = {k: v for k, v in grading_expect(expect).items() if k != "expected_verdict"}
    record.update(grade(outcome.root_cause(), structured_expect, confidence=outcome.confidence))
    record.update({
        "wall_s": round(time.perf_counter() - t0, 2), "answerable": None,
        "confidence": outcome.confidence, "pre_llm_confidence_score": None, "verdict": None,
        "fallbacks": [], "signals_mode": None, "root_cause": outcome.root_cause(),
        "outcome": outcome.outcome,
        "diagnoses": [{"rule": d.rule, "family": d.family, "location": d.cause_location,
                       "score": round(d.score, 2), "signals": d.signal_ids} for d in outcome.diagnoses],
        "signal_kinds": sorted(signals.kinds()), "unclassified": dict(signals.unclassified),
        "extractor_version": EXTRACTOR_VERSION, "rules_version": RULES_VERSION,
    })
    return record


def solve_llm(case_name: str, client, run: int) -> dict:
    """The offline workflow on the case fixtures, answered by ``client``."""
    from tests.integration.workflow_harness import run_case

    t0 = time.perf_counter()
    result = run_case(_key(case_name), llm=client)
    expect = result.expect
    state = result.state
    report = state.get("report_dict") or {}
    stats = state.get("ingestion_stats") or {}
    record = _base_record(case_name, expect, "llm", client.model, run)
    record.update(grade(report.get("root_cause", ""), grading_expect(expect),
                        confidence=state.get("confidence") or "",
                        verdict=state.get("verdict") or ""))
    record.update({
        "wall_s": round(time.perf_counter() - t0, 2),
        "answerable": answerable(expect, result.llm.analyze_prompts),
        "confidence": normalize_label(state.get("confidence") or ""),
        "confidence_raw": state.get("confidence") or "",
        "pre_llm_confidence_score": (report.get("pre_llm_confidence") or {}).get("score"),
        "verdict": state.get("verdict"),
        "fallbacks": sorted(k for k, v in stats.items() if isinstance(v, dict) and v.get("fallback")),
        "signals_mode": (stats.get("signals") or {}).get("mode"),
        "root_cause": report.get("root_cause", ""),
        # docs/case-audit-001.md F8: what the model was sent and what it read
        "llm_calls": list(getattr(result.llm, "calls", [])),
        "num_ctx_max": getattr(client, "num_ctx_max", None),
    })
    return record


# ── records and report ──────────────────────────────────────────────────────────

def record_path(out_dir: Path, solver: str, case_name: str, run: int) -> Path:
    return out_dir / solver / case_name / f"run_{run}.json"


def load_records(out_dir: Path) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(out_dir.glob("*/*/run_*.json"))]


def _rate(n: int, d: int) -> str:
    return f"{n}/{d}" if d else "—"


def render_report(records: list[dict], header: str = "") -> str:
    lines = ["# Veracity replay report", ""]
    if header:
        lines += [header, ""]
    lines += ["Generated by `tools/veracity_replay.py` — do not edit. "
              "Spec: `docs/test-cases.md` → *Veracity replay harness*.", ""]
    for solver in sorted({r["solver"] for r in records}):
        rows = [r for r in records if r["solver"] == solver]
        model = ", ".join(sorted({r["model"] for r in rows if r["model"]})) or "—"
        lines += [f"## {solver} ({model})", "",
                  "| Tier / case | Pass | Answerable | Model errors | Overconfident | False alarms | Mean wall s |",
                  "|---|---|---|---|---|---|---|"]
        groups: list[tuple[str, list[dict]]] = []
        for tier in TIERS:
            tier_rows = [r for r in rows if r["tier"] == tier]
            if not tier_rows:
                continue
            groups.append((f"**Tier {tier}**", tier_rows))
            for case in sorted({r["case"] for r in tier_rows}):
                groups.append((case, [r for r in tier_rows if r["case"] == case]))
        for label, rs in groups:
            passed = sum(r["veracity"] == "PASS" for r in rs)
            ans = [r for r in rs if r.get("answerable") is not None]
            model_err = sum(r["veracity"] == "FAIL" and r.get("answerable") is True for r in rs)
            over = sum(r["veracity"] == "FAIL" and (r.get("confidence") or "").upper() == "HIGH"
                       for r in rs)
            false_alarms = sum(r.get("expected_outcome", "cause") != "cause"
                               and r["veracity"] == "FAIL" for r in rs)
            wall = sum(r.get("wall_s") or 0 for r in rs) / len(rs)
            lines.append(f"| {label} | {_rate(passed, len(rs))} | "
                         f"{_rate(sum(bool(r['answerable']) for r in ans), len(ans))} | "
                         f"{model_err} | {over} | {false_alarms} | {wall:.1f} |")
        lines.append("")
    return "\n".join(lines) + "\n"


def _header(solver: str) -> str:
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                                capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        commit = "unknown"
    return f"Last run: {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC, solver `{solver}`, commit `{commit}`."


# ── CLI ─────────────────────────────────────────────────────────────────────────

def regrade(records_dir: Path) -> str:
    """Re-grade recorded runs with today's expect.json and synonyms; records untouched.

    The recorded answer, label and verdict are graded again, so a grading change
    (docs/evidence-score.md § Prerequisites) is measured without re-running a model.
    """
    rows = []
    for path in sorted(records_dir.glob("*/run_*.json")):
        record = json.loads(path.read_text())
        expect = json.loads((CASES_ROOT / record["case"] / "expect.json").read_text())
        grading = grading_expect(expect)
        if record.get("solver") == "rules":
            grading.pop("expected_verdict", None)
        new = grade(record.get("root_cause", ""), grading,
                    confidence=record.get("confidence", ""), verdict=record.get("verdict") or "")
        rows.append((record["tier"], record["case"], record["veracity"], new["veracity"]))
    lines = ["# Re-graded runs", "",
             f"Records: `{records_dir}`. Graded again with the current `expect.json` files and "
             "`tests/veracity/synonyms.yaml`; the records themselves are unchanged.", "",
             "| Tier / case | Pass before | Pass now |", "|---|---|---|"]
    for tier in TIERS:
        tier_rows = [r for r in rows if r[0] == tier]
        if not tier_rows:
            continue
        before = sum(r[2] == "PASS" for r in tier_rows)
        now = sum(r[3] == "PASS" for r in tier_rows)
        lines.append(f"| **Tier {tier}** | {before}/{len(tier_rows)} | {now}/{len(tier_rows)} |")
        for case in sorted({r[1] for r in tier_rows}):
            case_rows = [r for r in tier_rows if r[1] == case]
            b = sum(r[2] == "PASS" for r in case_rows)
            n = sum(r[3] == "PASS" for r in case_rows)
            lines.append(f"| {case} | {b}/{len(case_rows)} | {n}/{len(case_rows)} |")
    total_b = sum(r[2] == "PASS" for r in rows)
    total_n = sum(r[3] == "PASS" for r in rows)
    lines += ["", f"**All: {total_b}/{len(rows)} → {total_n}/{len(rows)}**", ""]
    return "\n".join(lines)


def _run_in_child(solver: str, case_name: str, run: int, out_dir: Path) -> None:
    """One llm run in its own process, killed after REPLAY_RUN_TIMEOUT seconds.

    A request can hang past the HTTP read timeout (a half-dead port-forward kept one
    open for 21 h on 2026-10-13); only a separate process can be stopped for sure.
    """
    timeout = int(os.environ.get("REPLAY_RUN_TIMEOUT", "1800"))
    cmd = [sys.executable, str(Path(__file__).resolve()), "--solver", solver,
           "--cases", _key(case_name), "--child-run", str(run), "--out", str(out_dir)]
    try:
        subprocess.run(cmd, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        print(f"{case_name} run {run}: ERROR — no answer within {timeout}s, process killed "
              "(not recorded; --resume retries it)", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Grade a real model or the rules on the native cases")
    ap.add_argument("--solver", help="rules | structured | llm | llm:<model>")
    ap.add_argument("--regrade", metavar="DIR",
                    help="re-grade the records under DIR (writes DIR/REGRADED.md), run nothing")
    ap.add_argument("--cases", default="", help="comma-separated keys, e.g. h001,h014 (default: all)")
    ap.add_argument("--runs", type=int, default=3, help="runs per case for llm (rules: always 1)")
    ap.add_argument("--resume", action="store_true", help="skip runs already recorded")
    ap.add_argument("--out", default=str(REPLAY_DIR), help=argparse.SUPPRESS)
    # internal: run exactly one (case, run) in this process — the parent's watchdog child
    ap.add_argument("--child-run", type=int, default=0, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if args.regrade:
        records_dir = Path(args.regrade)
        report = regrade(records_dir)
        (records_dir / "REGRADED.md").write_text(report)
        print(report)
        return 0
    if not args.solver:
        ap.error("--solver is required (or --regrade DIR)")

    out_dir = Path(args.out)
    solver, _, model = args.solver.partition(":")
    if solver not in ("rules", "structured", "llm"):
        ap.error("--solver must be rules, structured, llm or llm:<model>")
    wanted = {k.strip() for k in args.cases.split(",") if k.strip()}
    cases = [c for c in native_cases() if not wanted or _key(c) in wanted]

    client = None
    if solver == "llm":
        from llm.ollama_client import OllamaClient
        # CPU-only Ollama: one analyze call can take minutes (b13 notes). Passed to the
        # client directly: config.OLLAMA_TIMEOUT was read at import time.
        timeout = int(os.environ.get("REPLAY_OLLAMA_TIMEOUT", "900"))
        client = OllamaClient(model=model or None, timeout=timeout)
        if not client.is_available():
            print(f"Ollama not reachable at {client.url} — nothing written.", file=sys.stderr)
            return 2
        if not client.model_is_pulled():
            print(f"Model {client.model} not pulled — nothing written.", file=sys.stderr)
            return 2

    # never read data/examples/ during a replay; restored after, so a caller in the
    # same process (the test suite) does not inherit it
    previous = os.environ.get("EXAMPLE_LOOKUP_DISABLED")
    os.environ["EXAMPLE_LOOKUP_DISABLED"] = "1"
    try:
        runs = 1 if solver in ("rules", "structured") else args.runs
        run_range = [args.child_run] if args.child_run else range(1, runs + 1)
        for case_name in cases:
            for run in run_range:
                path = record_path(out_dir, solver, case_name, run)
                if args.resume and path.exists():
                    continue
                if solver == "llm" and not args.child_run:
                    _run_in_child(args.solver, case_name, run, out_dir)
                    continue
                try:
                    record = (solve_rules(case_name) if solver == "rules"
                              else solve_structured(case_name) if solver == "structured"
                              else solve_llm(case_name, client, run))
                except Exception as exc:  # one failed run must not stop an hours-long replay
                    print(f"{case_name} run {run}: ERROR — {type(exc).__name__}: {exc} "
                          "(not recorded; --resume retries it)", file=sys.stderr)
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
                print(f"{case_name} run {run}: {record['veracity']} — {record['reason']}")
                (out_dir / "REPORT.md").write_text(render_report(load_records(out_dir), _header(args.solver)))
    finally:
        if previous is None:
            os.environ.pop("EXAMPLE_LOOKUP_DISABLED", None)
        else:
            os.environ["EXAMPLE_LOOKUP_DISABLED"] = previous
    return 0


if __name__ == "__main__":
    sys.exit(main())
