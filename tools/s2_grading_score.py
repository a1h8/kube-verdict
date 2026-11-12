#!/usr/bin/env python3
"""S2's grading, combined (docs/s2/grading.md): two graders' verdicts, a third reading for the disputes.

    python tools/s2_grading_score.py --key KEY.json --grader A.md --grader B.md [--third C.md]

Each grader file holds the table a grader returned (| item | verdict | reason |). Prints, per arm and per family,
the cases passed (2 of 3 runs YES; D: its one run), PARTIAL, NO and disputed, and the two graders' agreement.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "tests/s2/cases"
VERDICTS = ("YES", "PARTIAL", "NO")


def read_verdicts(text: str) -> dict[int, str]:
    """{item: verdict} from a markdown table; rows that are not items are skipped."""
    out = {}
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 2 and re.fullmatch(r"\d+", cells[0]) and cells[1].upper() in VERDICTS:
            out[int(cells[0])] = cells[1].upper()
    return out


def kappa(a: list[str], b: list[str]) -> float | None:
    n = len(a)
    if n == 0:
        return None
    po = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[v] * cb[v] for v in VERDICTS) / (n * n)
    return None if pe == 1 else (po - pe) / (1 - pe)


def combine(first: dict[int, str], second: dict[int, str], third: dict[int, str] | None) -> dict[int, str]:
    """A run's verdict: the two graders' when they agree, else the third reading's, else DISPUTED."""
    out = {}
    for item in first:
        if item not in second:
            raise ValueError(f"item {item} graded by one grader only")
        a, b = first[item], second[item]
        out[item] = a if a == b else (third or {}).get(item, "DISPUTED")
    if set(second) - set(first):
        raise ValueError(f"items graded by one grader only: {sorted(set(second) - set(first))}")
    return out


def score(key: list[dict], verdicts: dict[int, str]) -> dict:
    by_case = defaultdict(list)
    for row in key:
        by_case[(row["arm"], row["case"])].append(verdicts.get(row["item"], "MISSING"))
    table = defaultdict(lambda: Counter())
    for (arm, case), vs in by_case.items():
        family = json.loads((CASES / case / "expect.json").read_text())["family"]
        need = 1 if arm == "D" else 2
        outcome = ("pass" if vs.count("YES") >= need else "disputed" if "DISPUTED" in vs else
                   "partial" if "PARTIAL" in vs else "no")
        for scope in (("all", arm), (family, arm)):
            table[scope][outcome] += 1
            table[scope]["cases"] += 1
    return {f"{scope[1]} · {scope[0]}": dict(c) for scope, c in sorted(table.items())}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--key", type=Path, required=True)
    ap.add_argument("--grader", type=Path, action="append", required=True)
    ap.add_argument("--third", type=Path)
    args = ap.parse_args(argv)
    if len(args.grader) != 2:
        ap.error("exactly two --grader files")
    key = json.loads(args.key.read_text())["items"]
    a, b = (read_verdicts(p.read_text()) for p in args.grader)
    third = read_verdicts(args.third.read_text()) if args.third else None
    items = sorted(a)
    agreement = sum(a[i] == b.get(i) for i in items) / len(items) if items else 0
    print(f"agreement {agreement:.2f}, kappa {kappa([a[i] for i in items], [b.get(i, '') for i in items])}")
    for scope, counts in score(key, combine(a, b, third)).items():
        print(scope, counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
