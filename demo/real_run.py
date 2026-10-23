"""KubeVerdict — real, reproducible run (offline, no cluster, no LLM).

Runs the product's own workflow (`LLM_PROVIDER=none`) on the committed
`h012_gitops_render_vs_live` fixture and prints only what it computed: the drift found by
comparing what Git declares with what Helm applied (docs/three-states.md), the structured
diagnosis, its confidence and the policy verdict. Nothing below is written by this script
(docs/fix-plan-001.md, F10). Reproduce with:

    python demo/real_run.py

Validated in CI: tests/unit/test_structured_in_product.py (the workflow's report equals the
structured diagnosis on every case).
"""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
logging.disable(logging.INFO)  # keep the transcript clean

from tests.integration.workflow_harness import run_case  # noqa: E402

C = {"g": "\033[32m", "r": "\033[31m", "y": "\033[33m", "c": "\033[36m",
     "d": "\033[2m", "B": "\033[1m", "x": "\033[0m"}


def p(s: str = "", pause: float = 0.15) -> None:
    print(s)
    sys.stdout.flush()
    time.sleep(pause)


def main() -> None:
    p(f"{C['B']}{C['c']}KubeVerdict — real run (offline · no cluster · no LLM){C['x']}", 0.4)
    run = run_case("h012", llm="none")
    state, graph = run.state, run.graph
    p(f"{C['d']}case: h012_gitops_render_vs_live   query: {state['query']}{C['x']}", 0.5)
    p()

    from dedup.bfs import find_unhealthy
    p(f"{C['B']}[1] What is failing (seeds, from the collected objects){C['x']}", 0.3)
    for e in find_unhealthy(graph)[:4]:
        p(f"    {e.fqn}", 0.3)
    p()

    from rca.signals import extract_signals
    signals = extract_signals(graph)
    p(f"{C['B']}[2] Drift — Git declared vs Helm applied vs live{C['x']}", 0.3)
    for s in signals.of("drift.config"):
        p(f"    {signals.subject(s)}  {s.attr('field')}: declared {C['g']}{s.attr('declared')}{C['x']}"
          f" → {C['y']}{s.attr('observed')}{C['x']}  {C['d']}[{s.attr('provenance') or s.source}]{C['x']}", 0.4)
    p()

    diag = state["diagnosis"]
    p(f"{C['B']}[3] Structured diagnosis{C['x']}  {C['d']}({diag['outcome']}){C['x']}", 0.3)
    for i, d in enumerate(diag["diagnoses"][:3], 1):
        p(f"    D{i} [{C['c']}{d['rule']}{C['x']}] {d['score']:.2f} {d['label']} — {d['cause']}", 0.4)
    p()

    p(f"{C['B']}[4] Report and verdict{C['x']}", 0.3)
    p(f"    confidence: {C['y']}{state['confidence']}{C['x']}   ·   verdict: {C['B']}{state.get('verdict')}{C['x']}", 0.3)
    for reason in state.get("verdict_reasons") or []:
        p(f"    {C['d']}{reason}{C['x']}", 0.2)
    p()
    p(f"{C['d']}reproduce: python demo/real_run.py{C['x']}", 0.2)


if __name__ == "__main__":
    main()
