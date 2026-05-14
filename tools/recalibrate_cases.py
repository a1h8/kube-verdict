"""
Recalibrate _debug_score_breakdown and confidence_score_min for all cases.

Reads every cases/0*/ directory, runs the full offline pipeline
(graph → FAISS → ContextBuilder), extracts the real component values
from pre_llm_confidence.reasons, and writes back to expect.json.

Usage:
    python tools/recalibrate_cases.py          # dry run (print only)
    python tools/recalibrate_cases.py --apply  # write to disk
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from tests.cases.graph_factory import build_graph, load_case  # noqa: E402
from rca.context_builder import ContextBuilder  # noqa: E402
from vectorstore.embedder import Embedder  # noqa: E402
from vectorstore.store import FAISSStore  # noqa: E402

CASES_ROOT = ROOT / "cases"
_REASON_RE = re.compile(r"→\s*([\d.]+)$")


def _parse_reasons(reasons: tuple[str, ...]) -> dict[str, float]:
    """Extract component values from ContextConfidence.reasons strings."""
    keys = ["bfs_c", "jac_c", "tfidf_c", "anchor_c", "signal_c", "drift_c", "policy_c"]
    result: dict[str, float] = {}
    for i, reason in enumerate(reasons):
        m = _REASON_RE.search(reason)
        if m and i < len(keys):
            # policy is additive so use its prefix marker
            if "Policy" in reason:
                result["policy_c"] = float(m.group(1))
            else:
                result[keys[i]] = float(m.group(1))
    return result


def recalibrate(case_dir: Path, apply: bool) -> tuple[str, list[str]]:
    """Run the offline pipeline for one case and return (case_name, changes)."""
    data   = load_case(case_dir)
    graph  = build_graph(data["input"])
    store  = FAISSStore(embedder=Embedder())
    store.index_graph(graph)
    ctx    = ContextBuilder(graph, store).build(data["input"]["query"])
    conf   = ctx.pre_llm_confidence

    expect_path = case_dir / "expect.json"
    expect      = json.loads(expect_path.read_text())

    # Parse real component values
    components = _parse_reasons(conf.reasons)
    new_total  = round(conf.score, 2)
    new_score_min = max(round(conf.score - 0.12, 2), 0.25)
    new_label     = conf.label

    # Build new _debug_score_breakdown
    new_breakdown = {
        "bfs_c":    components.get("bfs_c",    0.0),
        "jac_c":    components.get("jac_c",    0.0),
        "tfidf_c":  components.get("tfidf_c",  0.0),
        "anchor_c": components.get("anchor_c", 0.0),
        "signal_c": components.get("signal_c", 0.0),
        "drift_c":  components.get("drift_c",  0.0),
        "policy_c": components.get("policy_c", 0.0),
        "total":    new_total,
    }

    changes: list[str] = []
    old_breakdown = expect.get("_debug_score_breakdown", {})
    if old_breakdown.get("total") != new_total or "drift_c" not in old_breakdown:
        changes.append(
            f"_debug_score_breakdown: total {old_breakdown.get('total', '?')} → {new_total}"
            f"  (drift_c={new_breakdown['drift_c']:.2f})"
        )

    old_min = expect.get("confidence_score_min", 0)
    if abs(new_score_min - old_min) >= 0.02:
        changes.append(f"confidence_score_min: {old_min} → {new_score_min}")

    old_label = expect.get("confidence", "")
    if new_label != old_label:
        changes.append(f"confidence: {old_label!r} → {new_label!r}")

    if apply and changes:
        new_expect = dict(expect)
        new_expect["_debug_score_breakdown"] = new_breakdown
        new_expect["confidence_score_min"]   = new_score_min
        new_expect["confidence"]             = new_label
        expect_path.write_text(
            json.dumps(new_expect, indent=2, ensure_ascii=False) + "\n"
        )

    return case_dir.name, changes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Write changes to disk")
    args = parser.parse_args()

    case_dirs = sorted(CASES_ROOT.glob("0*/"))
    if not case_dirs:
        print(f"No cases found under {CASES_ROOT}")
        sys.exit(1)

    total_changes = 0
    for case_dir in case_dirs:
        print(f"\n{case_dir.name}", end="  ", flush=True)
        try:
            name, changes = recalibrate(case_dir, apply=args.apply)
            if changes:
                total_changes += len(changes)
                for c in changes:
                    print(f"\n  ✎ {c}", end="")
            else:
                print("✓ already up to date", end="")
        except Exception as exc:
            print(f"\n  ✗ ERROR: {exc}", end="")

    action = "Applied" if args.apply else "Dry run —"
    print(f"\n\n{action} {total_changes} change(s) across {len(case_dirs)} cases.")
    if not args.apply and total_changes:
        print("Run with --apply to write changes to disk.")


if __name__ == "__main__":
    main()
