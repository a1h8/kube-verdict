"""Protocol 001's C2: the model on the pipeline's context with the product's vector search —
docs/protocol-001-retrieval.md, amendments of 2026-10-23. No Ollama: a scripted model."""
from __future__ import annotations

import json

import pytest

from tests.unit.test_raw_context_solvers import FakeLLM
from tools import veracity_replay as vr


def _body(prompt: str) -> list[str]:
    return [line for line in prompt.splitlines() if not line.startswith("Analysis timestamp")]


def _c1_c2(case: str) -> tuple[dict, dict]:
    with vr.isolated_stores():
        c1 = vr.solve_context(case, FakeLLM(), 1)
    with vr.isolated_stores():
        c2 = vr.solve_context(case, FakeLLM(), 1, store="faiss")
    return c1, c2


def test_the_c2_store_is_the_case_graph_alone():
    c1, c2 = _c1_c2("h005_rbac_forbidden")
    assert c2["index"]["vectors"] > 0
    assert {k: c2["index"][k] for k in ("examples", "docs", "chart_anchors")} == \
        {"examples": 0, "docs": 0, "chart_anchors": 0}
    assert c1["index"] is None                                                    # control: stub


def test_c2_adds_related_context_already_in_the_prompt():
    c1, c2 = _c1_c2("h013_slo_error_budget_burn")
    one, two = _body(c1["prompts"][0]), _body(c2["prompts"][0])
    added = [line for line in two if line not in set(one)]
    assert any("Related context" in line for line in added)
    # both prompts carry a chunk count and a quality score; C2 changes their values
    header = ("Total context chunks", "Context quality score")
    score = lambda lines: next(x for x in lines if x.startswith("Context quality score"))  # noqa: E731
    assert score(one).split("(")[0] != score(two).split("(")[0]                 # MEDIUM → HIGH
    assert {x for x in one if not x.startswith(header)} <= set(two)             # nothing removed


def test_for_some_cases_c2_is_the_c1_prompt():                                   # the noise cases
    c1, c2 = _c1_c2("h003_oomkilled")
    assert _body(c1["prompts"][0]) == _body(c2["prompts"][0])


def test_the_product_builds_the_same_store(monkeypatch):
    from workflow import nodes
    seen = []

    def spy(graph, chart_dir=None, namespace="default"):
        seen.append(graph)
        return "built", {}

    monkeypatch.setattr(nodes, "build_investigation_store", spy)
    graph = object()
    config = {"configurable": {"graph": graph}}
    nodes.index_node({}, config)
    assert seen == [graph] and config["configurable"]["store"] == "built"


def test_c2_names_its_configuration_and_directory(tmp_path, monkeypatch):
    assert vr.configuration("context", "faiss")["config"] == "C2"
    assert vr.run_dir_name("context", "mistral", "v6", "faiss") == \
        "context-mistral-prompt_v6-store_faiss-examples_off"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("llm.ollama_client.OllamaClient", lambda **kw: FakeLLM())
    out = tmp_path / "out"
    assert vr.main(["--solver", "context:fake", "--store", "faiss", "--cases", "h003",
                    "--child-run", "1", "--out", str(out), "--out-free"]) == 0
    record = json.loads((out / "context/h003_oomkilled/run_1.json").read_text())
    assert record["config"] == "C2" and record["store"] == "faiss" and record["index"]["vectors"] > 0


@pytest.mark.parametrize("solver", ["structured", "raw:fake", "llm:fake"])
def test_the_vector_search_is_measured_on_the_context_only(solver, tmp_path):
    with pytest.raises(SystemExit):
        vr.main(["--solver", solver, "--store", "faiss", "--out", str(tmp_path / "o"), "--out-free"])


def test_the_child_receives_the_store(tmp_path, monkeypatch):
    import subprocess as sp
    calls = []
    monkeypatch.setattr(vr.subprocess, "run", lambda cmd, **k: calls.append(cmd) or sp.CompletedProcess(cmd, 0))
    vr._run_in_child("context:m", "h003_oomkilled", 1, tmp_path, None, "faiss")
    vr._run_in_child("context:m", "h003_oomkilled", 1, tmp_path)                   # control
    assert calls[0][-2:] == ["--store", "faiss"] and "--store" not in calls[1]
