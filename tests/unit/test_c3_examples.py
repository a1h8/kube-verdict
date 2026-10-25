"""Protocol 001's C3: the model on the pipeline's context with the product's vector search and the
incident corpus — docs/protocol-001-retrieval.md. No Ollama: a scripted model."""
from __future__ import annotations

import json
import os

import pytest

from tests.unit.test_raw_context_solvers import FakeLLM
from tools import incident_corpus as ic
from tools import veracity_replay as vr


def _c3(case: str, examples: str = "on") -> dict:
    with vr.isolated_stores():
        return vr.solve_context(case, FakeLLM(), 1, store="faiss", examples=examples)


def test_the_corpus_is_in_the_c3_store():
    rec = _c3("h003_oomkilled")
    assert rec["index"]["examples"] == len(ic.load_records()) == 11
    assert rec["index"]["docs"] == 0


def test_without_examples_the_store_has_none():                                     # control: C2
    assert _c3("h003_oomkilled", examples="off")["index"]["examples"] == 0


def _similar(prompt: str) -> str:
    section = prompt[prompt.find("SIMILAR PAST INCIDENTS"):]
    return section[:section.find("\n###")]


def test_the_closest_resolved_incidents_head_the_prompt():
    # the product lists five similar incidents, cause and fix included (CPI-19); for h003 the
    # first is the corpus's OOM record
    section = _similar(_c3("h003_oomkilled")["prompts"][0])
    assert section.startswith("SIMILAR PAST INCIDENTS — proven remediations (5)")
    first = section.split("RESOLVED INCIDENT: ")[1]
    assert first.startswith("Telemetry sync agents restart in a loop") and "Root cause: The memory limit" in first


def test_a_negative_case_gets_similar_incidents_too():
    # no threshold on this path: h022, where nothing is broken, is shown five past causes
    assert _similar(_c3("h022_no_incident")["prompts"][0]).count("RESOLVED INCIDENT: ") == 5


def test_the_corpus_is_never_written_to_a_real_store(tmp_path, monkeypatch):
    monkeypatch.setenv("KUBEVERDICT_EXAMPLE_DIR", str(tmp_path / "data/examples"))
    with pytest.raises(RuntimeError, match="outside isolated_stores"):
        vr.load_corpus_examples()
    assert not (tmp_path / "data/examples").exists()


def test_c3_names_its_configuration_and_records_the_corpus_hash(tmp_path, monkeypatch):
    assert vr.configuration("context", "faiss", "on")["config"] == "C3"
    assert vr.run_dir_name("context", "mistral", "v6", "faiss", "on") == \
        "context-mistral-prompt_v6-store_faiss-examples_on"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("llm.ollama_client.OllamaClient", lambda **kw: FakeLLM())
    out = tmp_path / "out"
    assert vr.main(["--solver", "context:fake", "--store", "faiss", "--examples", "on", "--cases", "h003",
                    "--child-run", "1", "--out", str(out), "--out-free"]) == 0
    record = json.loads((out / "context/h003_oomkilled/run_1.json").read_text())
    assert record["config"] == "C3" and record["examples"] == "on" and record["index"]["examples"] == 11
    assert record["corpus_hash"] == ic.corpus_hash() and record["invalid"] == []


@pytest.mark.parametrize("argv", [
    ["--solver", "context:fake", "--examples", "on"],                       # without the vector search
    ["--solver", "structured", "--examples", "on"],
    ["--solver", "raw:fake", "--store", "faiss", "--examples", "on"],
])
def test_the_corpus_reaches_only_the_context_with_its_vector_search(argv, tmp_path):
    with pytest.raises(SystemExit):
        vr.main([*argv, "--out", str(tmp_path / "x"), "--out-free"])


def test_the_child_run_carries_the_examples_flag(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(vr.subprocess, "run", lambda cmd, **kw: seen.append(cmd) or type("D", (), {"returncode": 0})())
    vr._run_in_child("context:fake", "h003_oomkilled", 1, tmp_path, None, "faiss", "on")
    assert seen and seen[0][-4:] == ["--store", "faiss", "--examples", "on"]
    assert os.environ.get("KUBEVERDICT_EXAMPLE_DIR", "").find("kv-replay-") < 0   # nothing leaked
