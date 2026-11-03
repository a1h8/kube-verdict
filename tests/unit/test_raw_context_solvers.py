"""The raw and context configurations of the comparison — docs/veracity-replay.md
§ Amendment 2026-10-22. No Ollama: a fake model that refuses like the real client."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from llm.ollama_client import OllamaClient
from tools import veracity_replay as vr
from tools.raw_input import RAW_DIRS, raw_block, raw_files, strip_comments

CASES = vr.CASES_ROOT

OOM_ANSWER = """HIGH
### 1. Summary
api is OOMKilled.
### 2. Affected resources
- Deployment/production/api — OOMKilled
### 3. Root cause
The memory limit drifted from the declared 512Mi to 128Mi, so the container is OOMKilled.
### 4. Causal chain
1. limit lowered 2. OOMKilled
### 5. Remediation
helm upgrade api ./chart
### 6. Confidence
HIGH
"""
WRONG_ANSWER = OOM_ANSWER.replace(
    "The memory limit drifted from the declared 512Mi to 128Mi, so the container is OOMKilled.",
    "A NetworkPolicy blocks the egress to the database.")


class FakeLLM:
    """Answers ``answer``; refuses an oversized prompt exactly as OllamaClient does."""

    def __init__(self, answer: str = OOM_ANSWER, num_ctx_max: int = 16384) -> None:
        self.model = "fake"
        self.num_ctx_max = num_ctx_max
        self.answer = answer
        self.prompts: list[str] = []
        self.last_call: dict = {}
        self._sizer = OllamaClient(num_ctx_max=num_ctx_max)

    def is_available(self) -> bool:
        return True

    def model_is_pulled(self) -> bool:
        return True

    def generate(self, prompt: str, system: str | None = None, **kwargs) -> str:
        estimated, num_ctx = self._sizer.context_for(len(prompt))   # PromptTooLongError
        self.prompts.append(prompt)
        self.last_call = {"num_ctx": num_ctx, "estimated_prompt_tokens": estimated}
        return self.answer


# ── the raw input ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("case_dir", sorted(CASES.glob("h0*")), ids=lambda p: p.name)
def test_only_fixture_files_are_raw_input(case_dir):
    names = [p.relative_to(case_dir).as_posix() for p in raw_files(case_dir)]
    assert names
    assert all(n.split("/", 1)[0] in RAW_DIRS for n in names)
    text, _ = raw_block(case_dir)
    assert case_dir.name not in text


def test_what_is_never_raw_input_exists():                                       # control
    h012 = CASES / "h012_gitops_render_vs_live"
    assert (h012 / "README.md").exists() and (h012 / "rendered").is_dir()
    names = [p.name for p in raw_files(h012)]
    assert "README.md" not in names and "expect.json" not in names
    assert not any("rendered" in p.parts for p in raw_files(h012))


@pytest.mark.parametrize("case, comment", [
    ("h004_missing_configmap", "this ConfigMap does NOT exist"),
    ("h004_missing_configmap", "must be created"),
    ("h012_gitops_render_vs_live", "A prior `helm upgrade --set"),
    ("h022_no_incident", "no drift"),
])
def test_comments_naming_the_cause_are_dropped(case, comment):
    case_dir = CASES / case
    original = "".join(p.read_text() for p in raw_files(case_dir))
    assert comment in original                                                   # control
    text, _ = raw_block(case_dir)
    assert comment not in text


def test_a_template_keeps_its_data_and_loses_its_comments():
    template = ('# a comment naming the cause\n'
                '{{/* another one */}}\n'
                'image: "{{ .Values.image }}"   # trailing comment\n'
                'note: "a # inside quotes stays"\n')
    out = strip_comments(template)
    assert "comment" not in out and "another one" not in out
    assert 'image: "{{ .Values.image }}"' in out
    assert 'note: "a # inside quotes stays"' in out


def test_plain_yaml_keeps_its_data():
    out = strip_comments("a: 1   # why\nb:\n  - x  # because\n")
    assert out == "a: 1\nb:\n- x\n"


# ── the raw solver ──────────────────────────────────────────────────────────────

def test_raw_grades_the_models_own_cause():
    llm = FakeLLM()
    record = vr.solve_raw("h003_oomkilled", llm, 1)
    assert record["veracity"] == "PASS"
    assert record["solver"] == "raw" and record["prompt"] == vr.RAW_PROMPT_VERSION
    assert record["prompts"] == llm.prompts and not record["did_not_fit"]
    assert all(f.split("/", 1)[0] in RAW_DIRS for f in record["files"])
    assert vr.solve_raw("h003_oomkilled", FakeLLM(WRONG_ANSWER), 1)["veracity"] == "FAIL"  # control


def test_an_empty_answer_is_a_fail():
    assert vr.solve_raw("h003_oomkilled", FakeLLM(answer=""), 1)["veracity"] == "FAIL"
    assert vr.solve_raw("h003_oomkilled", FakeLLM(), 1)["veracity"] == "PASS"            # control


def test_a_prompt_that_does_not_fit_is_a_graded_fail():
    record = vr.solve_raw("h016_multi_signal", FakeLLM(num_ctx_max=4096), 1)
    assert record["veracity"] == "FAIL" and record["did_not_fit"]
    assert record["reason"].startswith("did_not_fit")
    assert record["prompt_chars"] > 0
    assert not vr.solve_raw("h016_multi_signal", FakeLLM(), 1)["did_not_fit"]       # control


def test_every_case_fits_the_raw_ceiling():
    llm = FakeLLM(num_ctx_max=vr.RAW_NUM_CTX_MAX)
    for case_dir in sorted(CASES.glob("h0*")):
        prompt, _ = vr.raw_prompt(case_dir, "q")
        llm._sizer.context_for(len(prompt))


# ── the context solver ──────────────────────────────────────────────────────────

def test_context_has_neither_the_hypothesis_nor_the_diagnosis():
    llm = FakeLLM()
    record = vr.solve_context("h003_oomkilled", llm, 1)
    prompt = record["prompts"][0]
    assert prompt and "HYPOTHESIS TO INVESTIGATE" not in prompt
    assert record["solver"] == "context" and record["veracity"] == "PASS"
    # control: the product path puts the structured diagnosis in front of the model
    from tests.integration.workflow_harness import run_case
    product = run_case("h003", llm=FakeLLM())
    assert any("HYPOTHESIS TO INVESTIGATE" in p for p in product.llm.analyze_prompts)


def _evidence(prompt: str) -> str:
    """The prompt's evidence block: after the timestamp line, before the incident query."""
    body = prompt.split("Analysis timestamp", 1)[1].split("\n", 1)[1]
    return body.split("## Incident query", 1)[0]


@pytest.mark.parametrize("key, case", [("h003", "h003_oomkilled"), ("h016", "h016_multi_signal")])
def test_context_is_the_products_evidence_without_the_diagnosis(key, case):
    from tests.integration.workflow_harness import run_case
    context = vr.solve_context(case, FakeLLM(), 1)["prompts"][0]
    product = run_case(key, llm=FakeLLM()).llm.analyze_prompts[0]
    assert _evidence(context) == _evidence(product)
    assert "drift" in _evidence(context)                                         # not empty


# ── no real store ───────────────────────────────────────────────────────────────

def test_isolated_stores_point_inside_a_temporary_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    before_env = os.environ.get("KUBEVERDICT_EXAMPLE_DIR")
    with vr.isolated_stores() as tmp:
        from knowledge.doc_store import DocStore
        from knowledge.example_store import default_example_dir
        assert Path.cwd() == tmp
        assert default_example_dir().is_relative_to(tmp)
        DocStore()                                    # creates ./data/docs: inside tmp
    assert Path.cwd() == tmp_path
    assert os.environ.get("KUBEVERDICT_EXAMPLE_DIR") == before_env
    assert not (tmp_path / "data").exists()


def test_a_raw_run_through_main_writes_its_record_and_nothing_else(tmp_path, monkeypatch):
    work = tmp_path / "cwd"
    work.mkdir()
    monkeypatch.chdir(work)
    monkeypatch.setattr("llm.ollama_client.OllamaClient",
                        lambda model=None, timeout=None, num_ctx_max=None: FakeLLM())
    out = tmp_path / "out"
    assert vr.main(["--solver", "raw:fake", "--cases", "h003", "--child-run", "1",
                    "--out", str(out), "--out-free"]) == 0
    record = json.loads((out / "raw/h003_oomkilled/run_1.json").read_text())
    assert record["veracity"] == "PASS"
    assert list(work.iterdir()) == []                 # no ./data written where it ran
    assert Path.cwd() == work


def test_raw_and_context_need_a_model():
    with pytest.raises(SystemExit):
        vr.main(["--solver", "raw", "--out", "/nonexistent"])
