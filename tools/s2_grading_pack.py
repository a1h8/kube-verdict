#!/usr/bin/env python3
"""S2's blind grading pack and its key (docs/s2/grading.md, protocol 002 amendment A1).

    python tools/s2_grading_pack.py --records DIR [DIR ...] --out PACK.md --key KEY.json [--seed N]

Reads the records of S2's runs (one directory per arm), mixes every answer in an order drawn from the seed,
and writes the pack a grader receives (the instructions of docs/s2/grading.md, verbatim, then the items) and,
apart, the key that says which arm, case and run wrote each item.
"""
from __future__ import annotations

import argparse
import json
import random
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "tests/s2/cases"
GRADING = ROOT / "docs/s2/grading.md"


def instructions() -> str:
    """The instructions, verbatim from docs/s2/grading.md (the quoted block)."""
    text = GRADING.read_text()
    block = text.split("## The instructions, given verbatim with every pack", 1)[1].split("\n## ", 1)[0]
    lines = [ln[2:] if ln.startswith("> ") else ("" if ln.strip() == ">" else None) for ln in block.splitlines()]
    return "\n".join(ln for ln in lines if ln is not None).strip()


def truth(expect: dict) -> tuple[str, str, str]:
    """(ground truth, passes if, partial if only) for a case."""
    if expect.get("expected_outcome") == "no_incident":
        return "No incident.", "the answer says there is no incident and names no fault", "never"
    cause = expect["cause_object"]
    origin = f" (origin: {expect['origin_object']})" if expect.get("origin_object") else ""
    partial = "; ".join(expect.get("partial_if_only") or []) or "the right object with a wrong mechanism"
    return (f"{cause}{origin}: {expect['mechanism']}", expect.get("pass_if") or f"names {cause} and the mechanism",
            partial)


def arm_of(record: dict) -> str:
    if record["solver"] == "structured":
        return "D"
    return {"mistral": "L0", "qwen2.5:3b-instruct": "S0"}.get(record.get("model", ""), f"context:{record.get('model')}")


def items(record_dirs: list[Path]) -> list[dict]:
    out = []
    for d in record_dirs:
        for path in sorted(d.rglob("run_*.json")):
            r = json.loads(path.read_text())
            if r.get("set") != "S2":
                raise ValueError(f"{path}: not an S2 record")
            out.append({"case": r["case"], "arm": arm_of(r), "run": r["run"], "answer": r.get("root_cause") or "",
                        "record": str(path.relative_to(ROOT) if path.is_relative_to(ROOT) else path)})
    return out


def build(record_dirs: list[Path], seed: int) -> tuple[str, list[dict]]:
    rows = items(record_dirs)
    random.Random(seed).shuffle(rows)
    lines = [instructions(), "", f"There are {len(rows)} items.", "", "---", ""]
    key = []
    for n, row in enumerate(rows, 1):
        expect = json.loads((CASES / row["case"] / "expect.json").read_text())
        gt, passes, partial = truth(expect)
        answer = re.sub(r"\s+", " ", row["answer"]).strip() or "(empty)"
        lines += [f"Item {n}", f"Question: {expect['query']}", f"Ground truth: {gt}", f"Passes if: {passes}",
                  f"Partial if only: {partial}", f"Answer: {answer}", ""]
        key.append({"item": n, **{k: row[k] for k in ("case", "arm", "run", "record")}})
    return "\n".join(lines), key


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--records", nargs="+", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--key", type=Path, required=True)
    ap.add_argument("--seed", type=int, default=20261108)
    args = ap.parse_args(argv)
    pack, key = build(args.records, args.seed)
    args.out.write_text(pack + "\n")
    args.key.write_text(json.dumps({"seed": args.seed, "items": key}, indent=1) + "\n")
    print(f"{len(key)} items → {args.out}; key → {args.key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
