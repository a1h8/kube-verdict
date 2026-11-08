"""Does S2's cause object reach the analyze prompt? Offline, no model (docs/s2-context-reach.md).

For each S2 case with a cause, the prompt the context arms receive is built by the replay's own path
(``context_prompt_state`` + ``RCAAnalyzer``) with a client that answers nothing, in three configurations:
B0 (discovery empty, as S2's first run), B1 (discovery served, store stub), B2 (discovery served, the
investigation's hybrid search). Writes tests/s2/context-reach/<config>/<case>.json and prints the table.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUT = ROOT / "tests/s2/context-reach"
DISCOVERY_ONLY = {"a1_hpa_custom_metrics_api_points_away", "a2_hpa_scale_up_policy_slowed",
                  "l1_limitrange_default_injected", "l2_limitrange_max_lowered", "q2_quota_filled_by_stuck_jobs"}


class _Silent:
    """A client that answers nothing: only the prompt matters here."""
    model = "none"
    last_call = None

    def is_available(self) -> bool:
        return True

    def model_is_pulled(self) -> bool:
        return True

    def generate(self, prompt, system=None, **kwargs) -> str:
        return ""


def present(prompt: str, cause_object: str) -> bool:
    """One line holds both the kind and the name (case-insensitive); a Namespace also as ``namespace <name>``."""
    kind, *rest = cause_object.split("/")
    name = rest[-1].lower()
    for line in prompt.lower().splitlines():
        if name in line and (kind.lower() in line or (kind == "Namespace" and f"namespace {name}" in line)):
            return True
    return False


def in_graph(graph, cause_object: str) -> bool:
    kind, *rest = cause_object.split("/")
    ns = rest[0] if len(rest) == 2 else None
    return any(str(getattr(e.kind, "value", e.kind)) == kind and e.name == rest[-1]
               and (ns is None or e.namespace == ns) for e in graph.entities())


@contextlib.contextmanager
def empty_discovery(on: bool):
    import tests.s2.live_graph as lg
    real = lg.discovery_for_case
    if on:
        lg.discovery_for_case = lambda docs: real({})
    try:
        yield
    finally:
        lg.discovery_for_case = real


def prompt_for(case: str, store: str):
    from rca.analyzer import RCAAnalyzer
    from tests.integration.workflow_harness import RecordingLLM, StubStore
    from tools import veracity_replay as vr
    graph, expect, _ = vr.context_prompt_state(case)
    if store == "faiss":
        from workflow.nodes import build_investigation_store
        retrieval, _ = build_investigation_store(graph)
    else:
        retrieval = StubStore()
    rec = RecordingLLM(_Silent())
    try:
        RCAAnalyzer(graph=graph, store=retrieval, llm=rec).analyze(expect["query"])
    except Exception as exc:  # noqa: BLE001 — an empty answer may not parse; the prompt is already kept
        if not rec.analyze_prompts:
            raise RuntimeError(f"{case}: no prompt built ({exc})") from exc
    return graph, expect, rec.analyze_prompts[-1]


def main() -> int:
    from tools import veracity_replay as vr
    vr.use_set("s2")
    cases = [c for c in json.loads((ROOT / "tests/s2/frozen.json").read_text()).get("cases", {})] \
        if isinstance(json.loads((ROOT / "tests/s2/frozen.json").read_text()).get("cases"), dict) else None
    cases = cases or sorted(p.name for p in (ROOT / "tests/s2/cases").iterdir() if p.is_dir())
    commit = vr.commit_id()
    configs = {"B0": (True, "stub"), "B1": (False, "stub"), "B2": (False, "faiss")}
    table: dict[str, dict[str, tuple[bool, bool]]] = {}
    for name, (empty, store) in configs.items():
        with vr.isolated_stores(), empty_discovery(empty):
            for case in cases:
                expect = json.loads((ROOT / "tests/s2/cases" / case / "expect.json").read_text())
                if expect.get("expected_outcome") != "cause":
                    continue
                graph, expect, prompt = prompt_for(case, store)
                g, p = in_graph(graph, expect["cause_object"]), present(prompt, expect["cause_object"])
                table.setdefault(case, {})[name] = (g, p)
                out = OUT / name
                out.mkdir(parents=True, exist_ok=True)
                (out / f"{case}.json").write_text(json.dumps({
                    "config": name, "case": case, "commit": commit, "cause_object": expect["cause_object"],
                    "in_graph": g, "in_prompt": p, "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                    "prompt_chars": len(prompt), "prompt": prompt}, indent=1) + "\n")
    print(f"commit {commit}")
    print("| case | discovery-only | B0 graph/prompt | B1 graph/prompt | B2 graph/prompt |")
    print("|---|---|---|---|---|")
    yn = lambda b: "yes" if b else "no"  # noqa: E731
    for case, row in table.items():
        cells = " | ".join(f"{yn(row[c][0])}/{yn(row[c][1])}" for c in configs)
        print(f"| {case} | {'yes' if case in DISCOVERY_ONLY else ''} | {cells} |")
    for c in configs:
        print(f"{c}: cause in prompt {sum(r[c][1] for r in table.values())}/{len(table)}, "
              f"in graph {sum(r[c][0] for r in table.values())}/{len(table)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
