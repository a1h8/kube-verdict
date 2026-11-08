#!/usr/bin/env python3
"""Protocol 002, amendment A3: the number of cases each primary comparison needs, from block B.

Reads block B's records at the case level (2 runs of 3, official grades), takes the share of discordant
pairs of each comparison, and finds the smallest number of cases for which the exact two-sided McNemar test
reaches a power of 0.8 against a 15-point difference. Never hand-written: re-run this tool.

    python tools/protocol002_volume.py
"""
from __future__ import annotations

import glob
import json
from math import comb
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / "tests/veracity/replay-baselines"
DELTA, POWER = 0.15, 0.8
ALPHAS = {"0.05/8": 0.05 / 8, "0.05": 0.05}


def case_passes(run_dir: str, solver: str, runs: int) -> dict[str, bool]:
    out: dict[str, list[bool]] = {}
    for f in glob.glob(str(BASE / run_dir / solver / "*" / "run_*.json")):
        r = json.loads(Path(f).read_text())
        out.setdefault(r["case"], []).append(r["veracity"] == "PASS" and not r.get("invalid"))
    return {c: (sum(v) >= 2 if runs == 3 else v[0]) for c, v in out.items()}


def exact_p(b: int, c: int) -> float:
    n = b + c
    if n == 0:
        return 1.0
    return min(1.0, 2 * sum(comb(n, i) for i in range(min(b, c) + 1)) / 2 ** n)


def power(n: int, psi: float, delta: float, alpha: float) -> float:
    """P(reject) when a share psi of the cases is discordant and the difference is delta."""
    q = (psi + delta) / (2 * psi)
    total = 0.0
    for d in range(n + 1):
        pd = comb(n, d) * psi ** d * (1 - psi) ** (n - d)
        if pd < 1e-12:
            continue
        total += pd * sum(comb(d, b) * q ** b * (1 - q) ** (d - b) for b in range(d + 1) if exact_p(b, d - b) < alpha)
    return total


def cases_needed(psi: float, alpha: float) -> int:
    n = 10
    while power(n, psi, DELTA, alpha) < POWER:
        n += 5 if n < 100 else 10
    return n


def main() -> None:
    d = case_passes("structured-store_stub-examples_off", "structured", 1)
    arms = {"L0": case_passes("context-mistral-prompt_v6-store_stub-examples_off", "context", 3),
            "S0": case_passes("context-qwen2.5_3b-instruct-prompt_v6-store_stub-examples_off", "context", 3),
            "C2-L": case_passes("context-mistral-prompt_v6-store_faiss-examples_off", "context", 3),
            "C2-S": case_passes("context-qwen2.5_3b-instruct-prompt_v6-store_faiss-examples_off", "context", 3)}
    cases = sorted(d)
    rows = [("D vs L0", d, arms["L0"]), ("D vs S0", d, arms["S0"]),
            ("L0 vs C2 (mistral)", arms["L0"], arms["C2-L"]), ("S0 vs C2 (qwen)", arms["S0"], arms["C2-S"])]
    print("| Comparison | Discordant | psi | " + " | ".join(f"cases, alpha {a}" for a in ALPHAS) + " |")
    print("|---|---|---|" + "---|" * len(ALPHAS))
    for name, a, b in rows:
        disc = sum(a[c] != b[c] for c in cases)
        psi = disc / len(cases)
        print(f"| {name} | {disc} of {len(cases)} | {psi:.2f} | "
              + " | ".join(str(cases_needed(psi, al)) for al in ALPHAS.values()) + " |")


if __name__ == "__main__":
    main()
