"""R1, the investigation's vector search alone — docs/protocol-001-retrieval.md, amendment
2026-10-27. The arithmetic on toy texts; the real cases once, with no store written."""
from __future__ import annotations

import json

import pytest

from tools import retrieval_alone as r1

EXPECT = {"root_cause_concepts": ["memory", "limit"]}
CAUSE = "container memory limit 512Mi"


def test_chance_is_the_exact_share_of_subsets_that_pass():
    # one text of four carries the cause: 3 of the 4 triples hold it
    assert r1.chance([CAUSE, "pod a", "pod b", "event c"], EXPECT) == 0.75
    # concepts split over two texts: a triple passes only with both — 2 of 4
    assert r1.chance(["memory pressure", "a limit", "pod b", "event c"], EXPECT) == 0.5


def test_a_store_of_k_or_fewer_is_the_whole_store():
    assert r1.chance([CAUSE, "pod a"], EXPECT) == 1.0
    assert r1.chance(["pod a", "pod b", "pod c"], EXPECT) == 0.0


def test_m1_reads_the_top_three_in_rank_order_without_examples():
    hits = [{"uid": "example:inc-1", "text": CAUSE}] + [{"uid": f"p{i}", "text": f"pod {i}"} for i in range(3)] \
        + [{"uid": "x", "text": CAUSE}]
    top = r1.top_k_texts(hits)
    assert top == ["pod 0", "pod 1", "pod 2"]                    # the cause is fourth: not read
    assert not r1.passes("\n".join(top), EXPECT)
    assert r1.top_k_texts([{"uid": "x", "text": CAUSE}, *hits])[0] == CAUSE    # control


def _row(case, m2, negative=False, m1=False, chance=0.0):
    return {"case": case, "negative": negative, "m1_pass": m1, "chance": chance, "m2": m2,
            "vectors": 5, "c1_pass": False, "c2_pass": m2}


@pytest.mark.parametrize("n, decision", [(0, "leaves"), (1, "inconclusive"), (2, "inconclusive"), (3, "stays")])
def test_the_rule_at_each_threshold(n, decision):
    assert r1.decide(n) == decision
    assert r1.summarize([_row(f"h{i}", True) for i in range(n)] + [_row("hz", False)])["decision"] == decision


def test_negative_cases_are_never_counted():
    s = r1.summarize([_row("h022", True, negative=True, m1=True, chance=1.0), _row("h001", False)])
    assert s["cases"] == 1 and s["m2"] == 0 and s["m1_passes"] == 0 and s["m1_chance_expected"] == 0


def test_m2_counts_only_c1_fail_c2_pass(monkeypatch):
    for c1, c2, counted in [(False, True, True), (True, True, False), (True, False, False), (False, False, False)]:
        prompts = {"stub": "x memory limit" if c1 else "x", "faiss": "x memory limit" if c2 else "x"}
        monkeypatch.setattr(r1.vr, "solve_context",
                            lambda case, client, run, store="stub": {"prompts": [prompts[store]], "refused": ""})
        assert r1.m2("h003_oomkilled", EXPECT)["m2"] is counted


def test_the_real_cases_run_and_write_no_store(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "out"
    assert r1.main(["--cases", "h003,h022", "--out", str(out)]) == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == ["out"]
    data = json.loads((out / "r1.json").read_text())
    rows = {r["case"]: r for r in data["rows"]}
    assert rows["h022_no_incident"]["negative"] and data["summary"]["cases"] == 1
    h003 = rows["h003_oomkilled"]
    assert h003["vectors"] >= 4 and h003["index"]["examples"] == 0 and 0 <= h003["chance"] <= 1
    assert "Rule:" in (out / "REPORT.md").read_text()


def test_the_store_is_built_inside_isolated_stores(monkeypatch):
    # it would read ./data/examples and ./data/docs where it runs: checked where it is built
    import os

    import workflow.nodes as nodes
    real, seen = nodes.build_investigation_store, []

    def spy(graph, *a, **kw):
        seen.append("kv-replay-" in os.getcwd() and "kv-replay-" in os.environ["KUBEVERDICT_EXAMPLE_DIR"])
        return real(graph, *a, **kw)
    monkeypatch.setattr(nodes, "build_investigation_store", spy)
    r1.m1("h003_oomkilled", EXPECT)
    assert seen == [True]
