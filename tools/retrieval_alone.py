#!/usr/bin/env python3
"""R1 — the investigation's vector search alone, offline, no model: docs/protocol-001-retrieval.md,
amendment 2026-10-27 (CPI-25).

For each case, the store the product builds from the case's graph (``build_investigation_store``,
examples off, in ``isolated_stores``):

- **M1** — the top 3 non-example hits of ``hybrid_search(query)``, as the context builder splits
  them, joined and graded with the case's grader; beside it, **chance**: the share of all 3-hit
  subsets of the store's non-example vectors that pass the same grader (exact, no seed);
- **M2** — the C1 and C2 prompts, built by the replay's own solver with a scripted model, each
  graded: a case counts when C1 fails and C2 passes.

The rule (fixed before any number): M2 ≥ 3 cases → the vector search stays in the default path;
1–2 → inconclusive; 0 → it leaves. M1 is reported with its chance line and decides nothing.

Usage::

    python tools/retrieval_alone.py
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import veracity_replay as vr  # noqa: E402

K = 3
NEGATIVES = ("h022", "h026")              # no cause: reported, not counted
ANSWER = "ROOT CAUSE: scripted, no answer.\nCONFIDENCE: LOW"


class ScriptedClient:
    """Answers ``ANSWER``; sizes and refuses prompts as the product's client does."""

    def __init__(self) -> None:
        from llm.ollama_client import OllamaClient
        self.model = "scripted"
        self._sizer = OllamaClient()
        self.last_call: dict = {}

    def is_available(self) -> bool:
        return True

    def model_is_pulled(self) -> bool:
        return True

    def generate(self, prompt: str, system: str = "", **_) -> str:
        estimated, num_ctx = self._sizer.context_for(len(prompt) + len(system or ""))
        self.last_call = {"num_ctx": num_ctx, "estimated_prompt_tokens": estimated}
        return ANSWER


def passes(text: str, expect: dict) -> bool:
    from tools.veracity import grade
    return grade(text, expect)["veracity"] == "PASS"


def chance(texts: list[str], expect: dict, k: int = K) -> float:
    """The exact share of k-subsets of ``texts`` whose joined text passes; with k or fewer
    texts, the whole set (1.0 or 0.0)."""
    if len(texts) <= k:
        return 1.0 if passes("\n".join(texts), expect) else 0.0
    subsets = list(itertools.combinations(texts, k))
    return sum(passes("\n".join(s), expect) for s in subsets) / len(subsets)


def top_k_texts(hits: list[dict], k: int = K) -> list[str]:
    from knowledge.example_store import EXAMPLE_UID_PREFIX
    return [h["text"] for h in hits if not h["uid"].startswith(EXAMPLE_UID_PREFIX)][:k]


def m1(case: str, expect: dict) -> dict:
    import config as cfg
    from knowledge.example_store import EXAMPLE_UID_PREFIX
    from workflow.nodes import build_investigation_store
    with vr.isolated_stores():
        graph, _, _ = vr.context_prompt_state(case)
        store, index = build_investigation_store(graph)
        hits = store.hybrid_search(expect.get("query", ""), top_k=cfg.TFIDF_TOP_K * 3)
        texts = [m["text"] for m in store._metadata if not m["uid"].startswith(EXAMPLE_UID_PREFIX)]
    top = top_k_texts(hits)
    return {"vectors": len(texts), "index": index, "m1_pass": passes("\n".join(top), expect),
            "chance": round(chance(texts, expect), 4)}


def m2(case: str, expect: dict) -> dict:
    out = {}
    for config, store in (("c1", "stub"), ("c2", "faiss")):
        with vr.isolated_stores():
            rec = vr.solve_context(case, ScriptedClient(), 1, store=store)
        prompt = (rec.get("prompts") or [""])[0]
        out[f"{config}_pass"] = bool(prompt) and passes(prompt, expect)
        out[f"{config}_refused"] = bool(rec.get("refused"))
    out["m2"] = not out["c1_pass"] and out["c2_pass"]
    return out


def decide(m2_cases: int) -> str:
    return "stays" if m2_cases >= 3 else "inconclusive" if m2_cases >= 1 else "leaves"


def summarize(rows: list[dict]) -> dict:
    counted = [r for r in rows if not r["negative"]]
    m1_passes = sum(r["m1_pass"] for r in counted)
    expected = round(sum(r["chance"] for r in counted), 2)
    m2_cases = [r["case"] for r in counted if r["m2"]]
    return {"cases": len(counted), "m1_passes": m1_passes, "m1_chance_expected": expected,
            "m1_minus_chance": round(m1_passes - expected, 2), "m2_cases": m2_cases,
            "m2": len(m2_cases), "decision": decide(len(m2_cases))}


def render(summary: dict, rows: list[dict], commit: str) -> str:
    lines = [f"# R1 — the investigation's vector search alone (commit `{commit}`)", "",
             "docs/protocol-001-retrieval.md, amendment 2026-10-27. No model; deterministic.", "",
             f"- M1: **{summary['m1_passes']}/{summary['cases']}** pass at top {K}; expected by chance "
             f"**{summary['m1_chance_expected']}**; difference **{summary['m1_minus_chance']}** (reported)",
             f"- M2: **{summary['m2']}** case(s) where C2's prompt passes and C1's does not "
             f"({', '.join(summary['m2_cases']) or 'none'})",
             f"- Rule: **{summary['decision']}**", "",
             "| Case | Vectors | M1 top 3 | Chance | C1 | C2 | M2 |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        mark = lambda b: "PASS" if b else "FAIL"  # noqa: E731
        lines.append(f"| {r['case']}{' (negative, not counted)' if r['negative'] else ''} | {r['vectors']} | "
                     f"{mark(r['m1_pass'])} | {r['chance']} | {mark(r['c1_pass'])} | {mark(r['c2_pass'])} | "
                     f"{'yes' if r['m2'] else '—'} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--cases", default="", help="comma-separated keys (default: all)")
    ap.add_argument("--out", default=None, help="records directory (default: a dated one)")
    args = ap.parse_args(argv)
    from tests.integration.cases.case_loader import load_case
    wanted = {k.strip() for k in args.cases.split(",") if k.strip()}
    rows = []
    for case in (c for c in vr.native_cases() if not wanted or vr._key(c) in wanted):
        expect = vr.grading_expect(load_case(vr.CASES_ROOT / case)["expect"])
        expect = {k: v for k, v in expect.items() if k != "expected_verdict"}
        negative = vr._key(case) in NEGATIVES
        row = {"case": case, "negative": negative}
        if negative:     # no cause to grade: the store is still described
            row.update({"vectors": 0, "m1_pass": False, "chance": 0.0, "c1_pass": False,
                        "c2_pass": False, "m2": False})
        else:
            row.update(m1(case, expect))
            row.update(m2(case, expect))
        rows.append(row)
    commit = vr.commit_id()
    summary = summarize(rows)
    out = Path(args.out) if args.out else (
        vr.REPLAY_BASELINES / f"retrieval_investigation-store_faiss-examples_off-{date.today()}")
    out.mkdir(parents=True, exist_ok=True)
    (out / "r1.json").write_text(json.dumps({"commit": commit, "k": K, "summary": summary,
                                             "rows": rows}, indent=2) + "\n")
    (out / "REPORT.md").write_text(render(summary, rows, commit))
    print(json.dumps(summary), "→", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
