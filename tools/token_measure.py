#!/usr/bin/env python3
"""Each prompt of the ``raw:`` and ``context:`` configurations measured with the model's own
tokenizer — docs/live-validation-001.md, step 4 (fix plan H80).

The prompts are built by the replay's own solvers (``solve_raw``, ``solve_context``, in
``isolated_stores``), so they are the text block B sends; each solver calls the model once per
run. The measuring client sends each prompt to Ollama with ``num_predict: 1`` and a ``num_ctx``
above any prompt, and reads ``prompt_eval_count``: the prompt's size in that model's tokens.
Beside it, what the product's client decides from its character estimate:

- ``did_not_fit``: the client refuses the prompt (estimate over ``OLLAMA_NUM_CTX_MAX``) — the
  run is a FAIL, the ceiling is not moved;
- ``would_truncate``: the client sends it, but the measured tokens and the answer exceed the
  ``num_ctx`` it would send — the run would be truncated, which the replay records invalid;
- ``fits``: neither;
- ``invalid``: no count, or a count that reached the measuring ``num_ctx`` (Ollama truncated the
  measurement itself) — never read as ``fits``.

With ``--store faiss`` the table also holds C2's prompts: ``context:`` with the vector search the
product builds for the investigation, as the replay's ``--store faiss`` builds them, one per case
and no cut runs (docs/live-validation-001.md, amendment 2026-10-30; CPI-26). The stub rows are the
same with or without it.

Usage::

    OLLAMA_URL=http://localhost:11434 python tools/token_measure.py --model mistral --store faiss
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import date
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from llm.ollama_client import _CHARS_PER_TOKEN, OllamaClient, PromptTooLongError  # noqa: E402
from tools import veracity_replay as vr  # noqa: E402

MEASURE_CTX = 32768                      # above any prompt of the 19 cases (h016's C3: ~8 400)
SOLVERS = ("raw", "context")
# the answer the solvers parse; one call per run, so it changes no measured prompt
ANSWER = "ROOT CAUSE: measurement run, no answer.\nCONFIDENCE: LOW"


class MeasuringClient:
    """Answers every prompt with ``ANSWER`` after measuring it; refuses exactly what the
    product's client refuses, so the solvers take the path they take live."""

    def __init__(self, model: str, url: str | None = None) -> None:
        self.sizer = OllamaClient(url=url, model=model)
        self.model = model
        self.calls: list[dict] = []
        self.last_call: dict = {}

    def is_available(self) -> bool:
        return True

    def model_is_pulled(self) -> bool:
        return True

    def model_digest(self) -> str:
        return self.sizer.model_digest()

    def generate(self, prompt: str, system: str = "", temperature: float = 0.1, **_) -> str:
        chars = len(prompt) + len(system or "")
        try:
            estimated, num_ctx = self.sizer.context_for(chars)
        except PromptTooLongError:
            estimated, num_ctx = math.ceil(chars / _CHARS_PER_TOKEN), None
        payload = {"model": self.model, "prompt": prompt, "stream": False,
                   "options": {"num_predict": 1, "num_ctx": MEASURE_CTX, "temperature": 0}}
        if system:
            payload["system"] = system
        resp = requests.post(f"{self.sizer.url}/api/generate", json=payload, timeout=self.sizer.timeout)
        resp.raise_for_status()
        tokens = resp.json().get("prompt_eval_count")
        self.calls.append({"chars": chars, "estimated": estimated, "num_ctx": num_ctx,
                           "tokens": tokens, "verdict": verdict(tokens, num_ctx, self.sizer.num_predict)})
        if num_ctx is None:
            raise PromptTooLongError(f"prompt needs ~{estimated} tokens: refused rather than truncated")
        self.last_call = {"num_ctx": num_ctx, "estimated_prompt_tokens": estimated}
        return ANSWER


def verdict(tokens: int | None, num_ctx: int | None, num_predict: int) -> str:
    if not isinstance(tokens, int) or tokens + 1 >= MEASURE_CTX:
        return "invalid"
    if num_ctx is None:
        return "did_not_fit"
    return "would_truncate" if tokens + num_predict > num_ctx else "fits"


def _measure_one(client: MeasuringClient, case: str, solver: str, cut: str | None, store: str) -> dict:
    before = len(client.calls)
    row = {"case": case, "solver": solver, "cut": cut, "store": store}
    try:
        with vr.isolated_stores():
            if solver == "raw":
                vr.solve_raw(case, client, 1, cut)
            elif store == "faiss":
                vr.solve_context(case, client, 1, cut, store="faiss", examples="off")
            else:
                vr.solve_context(case, client, 1, cut)
    except Exception as exc:  # noqa: BLE001 — one case must not stop the table
        row["error"] = f"{type(exc).__name__}: {exc}"[:300]
    calls = client.calls[before:]
    row["calls"] = len(calls)
    row.update(calls[0] if len(calls) == 1 else {"verdict": "invalid"})
    return row


def measure(client: MeasuringClient, cases: list[str], store: str = "stub") -> list[dict]:
    rows = []
    for case in cases:
        for cut in (None, *vr.cuttable(case)):
            for solver in SOLVERS:
                rows.append(_measure_one(client, case, solver, cut, "stub"))
        if store == "faiss":                       # C2: context only, no cut runs
            rows.append(_measure_one(client, case, "context", None, "faiss"))
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--model", required=True)
    ap.add_argument("--cases", default="", help="comma-separated keys, e.g. h001,h016 (default: all)")
    ap.add_argument("--store", choices=("stub", "faiss"), default="stub",
                    help="faiss: also measure C2's prompts (context: with the vector search)")
    ap.add_argument("--out", default=None, help="output file (default: a dated records directory)")
    args = ap.parse_args(argv)
    wanted = {k.strip() for k in args.cases.split(",") if k.strip()}
    cases = [c for c in vr.native_cases() if not wanted or vr._key(c) in wanted]
    client = MeasuringClient(args.model)
    rows = measure(client, cases, args.store)
    summary = {v: sum(r["verdict"] == v for r in rows)
               for v in ("fits", "would_truncate", "did_not_fit", "invalid")}
    out = Path(args.out) if args.out else (
        vr.REPLAY_BASELINES / f"tokens-{re.sub(r'[:/]', '_', args.model)}-{date.today()}" / "tokens.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "model": args.model, "model_digest": client.model_digest(), "commit": vr.commit_id(),
        "store": args.store,
        "measure_num_ctx": MEASURE_CTX, "num_ctx_max": client.sizer.num_ctx_max,
        "num_predict": client.sizer.num_predict, "summary": summary, "rows": rows}, indent=2) + "\n")
    print(json.dumps(summary), "→", out)
    return 1 if summary["invalid"] else 0


if __name__ == "__main__":
    sys.exit(main())
