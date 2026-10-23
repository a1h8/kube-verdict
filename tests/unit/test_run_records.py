"""What every replay run records, and where — docs/veracity-replay.md § Amendment 2026-10-23,
docs/fix-plan-001.md H79. No Ollama: the model is a fake that reports its own token counts."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

import config as cfg
from tools import veracity_replay as vr

OOM_ANSWER = """HIGH
### 3. Root cause
The memory limit drifted from the declared 512Mi to 128Mi, so the container is OOMKilled.
### 6. Confidence
HIGH
"""


class TokenLLM:
    """Answers OOM_ANSWER and reports ``read`` tokens in a ``num_ctx`` window."""

    def __init__(self, read: int, num_ctx: int = 4096, num_predict: int = 512) -> None:
        self.model, self.num_ctx_max = "fake", 16384
        self.read, self.num_ctx, self.num_predict = read, num_ctx, num_predict
        self.last_call: dict = {}

    def is_available(self) -> bool:
        return True

    def model_is_pulled(self) -> bool:
        return True

    def model_digest(self) -> str:
        return "sha256:fake"

    def generate(self, prompt: str, system: str | None = None, **kwargs) -> str:
        self.last_call = {"num_ctx": self.num_ctx, "prompt_tokens_read": self.read,
                          "num_predict": self.num_predict, "temperature": 0.1}
        return OOM_ANSWER


def _main_raw(tmp_path, monkeypatch, llm) -> dict:
    work = tmp_path / "cwd"
    work.mkdir(exist_ok=True)
    monkeypatch.chdir(work)
    monkeypatch.setattr("llm.ollama_client.OllamaClient", lambda **kw: llm)
    out = tmp_path / "out"
    assert vr.main(["--solver", "raw:fake", "--cases", "h003", "--child-run", "1",
                    "--out", str(out), "--out-free"]) == 0
    return json.loads((out / "raw/h003_oomkilled/run_1.json").read_text())


def _main_structured(tmp_path, monkeypatch, case="h003") -> tuple[dict, Path]:
    work = tmp_path / "cwd"
    work.mkdir(exist_ok=True)
    monkeypatch.chdir(work)
    out = tmp_path / "out"
    assert vr.main(["--solver", "structured", "--cases", case, "--out", str(out), "--out-free"]) == 0
    name = next(c for c in vr.native_cases() if c.startswith(case))
    return json.loads((out / "structured" / name / "run_1.json").read_text()), out


# ── provenance ──────────────────────────────────────────────────────────────────

def test_the_grading_hashes_are_those_of_the_files():
    hashes = vr.grading_hashes()
    assert set(hashes) == set(vr.GRADING_FILES)
    for rel, h in hashes.items():
        assert h == hashlib.sha256((vr.ROOT / rel).read_bytes()).hexdigest()
    assert len(set(hashes.values())) == 2                                          # not a constant


def test_the_case_hash_moves_with_any_file_of_the_case(tmp_path, monkeypatch):
    shutil.copytree(vr.CASES_ROOT / "h003_oomkilled", tmp_path / "h003_oomkilled")
    monkeypatch.setattr(vr, "CASES_ROOT", tmp_path)
    before = vr.case_hash("h003_oomkilled")
    assert vr.case_hash("h003_oomkilled") == before                                # stable
    expect = tmp_path / "h003_oomkilled/expect.json"
    expect.write_text(expect.read_text() + " ")
    assert vr.case_hash("h003_oomkilled") != before


def test_the_commit_is_head():
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=vr.ROOT, capture_output=True,
                          text=True, check=True).stdout.strip()
    assert vr.commit_id() in (head, head + "-dirty")


def test_a_structured_record_says_where_it_comes_from(tmp_path, monkeypatch):
    record, _ = _main_structured(tmp_path, monkeypatch)
    assert record["commit"] == vr.commit_id()
    assert record["grading_hashes"] == vr.grading_hashes()
    assert record["case_hash"] == vr.case_hash("h003_oomkilled")
    assert record["config"] == "C0" and record["store"] == "stub" and record["examples"] == "off"
    assert record["model_digest"] == "" and record["cut"] is None and record["invalid"] == []


def test_a_model_record_carries_the_model_digest(tmp_path, monkeypatch):
    record = _main_raw(tmp_path, monkeypatch, TokenLLM(read=1000))
    assert record["model_digest"] == "sha256:fake" and record["config"] == "raw"


@pytest.mark.parametrize("solver, store, examples, config", [
    ("structured", "stub", "off", "C0"), ("context", "stub", "off", "C1"),
    ("context", "faiss", "off", "C2"), ("context", "faiss", "on", "C3"),
    ("raw", "stub", "off", "raw"), ("llm", "stub", "off", "llm"),
])
def test_configuration(solver, store, examples, config):
    assert vr.configuration(solver, store, examples)["config"] == config


# ── where ───────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("solver, model, prompt, name", [
    ("llm", "mistral", "v6", "llm-mistral-prompt_v6-store_stub-examples_off"),
    ("context", "mistral", "v6", "context-mistral-prompt_v6-store_stub-examples_off"),
    ("raw", "qwen2.5:3b-instruct", "raw-v1",
     "raw-qwen2.5_3b-instruct-prompt_raw-v1-store_stub-examples_off"),
    ("structured", "", None, "structured-store_stub-examples_off"),
])
def test_the_run_directory_names_the_solver(solver, model, prompt, name):
    assert vr.run_dir_name(solver, model, prompt) == name


def test_llm_and_context_on_one_model_never_share_a_directory():
    assert vr.run_dir_name("llm", "mistral", "v6") != vr.run_dir_name("context", "mistral", "v6")


def test_an_out_directory_must_carry_the_run_name(tmp_path):
    name = "structured-store_stub-examples_off"
    assert vr.out_dir_error(tmp_path / name, name) == ""
    assert vr.out_dir_error(tmp_path / f"{name}-2026-10-24", name) == ""
    assert "must be named" in vr.out_dir_error(tmp_path / "anything", name)
    with pytest.raises(SystemExit):
        vr.main(["--solver", "structured", "--cases", "h003", "--out", str(tmp_path / "anything")])
    assert not (tmp_path / "anything").exists()


# ── invalid runs ────────────────────────────────────────────────────────────────

def test_truncated_calls():
    assert vr.truncated_calls([{"num_ctx": 4096, "prompt_tokens_read": 3584, "num_predict": 512},
                               {"num_ctx": 4096, "prompt_tokens_read": 3000, "num_predict": 512},
                               {"num_ctx": 4096, "prompt_tokens_read": None}]) == [0]


def test_a_truncated_run_is_invalid_and_never_a_pass(tmp_path, monkeypatch):
    record = _main_raw(tmp_path, monkeypatch, TokenLLM(read=3700))
    assert record["veracity"] == "PASS"                                 # graded as it answered
    assert record["invalid"] and record["invalid"][0].startswith("truncated")
    report = (tmp_path / "out/REPORT.md").read_text()
    row = next(line for line in report.splitlines() if line.startswith("| h003_oomkilled "))
    cells = [c.strip() for c in row.split("|")[2:-1]]
    assert cells[0] == "0/1" and cells[-1] == "1"


def test_a_run_with_room_left_is_valid(tmp_path, monkeypatch):                     # control
    assert _main_raw(tmp_path, monkeypatch, TokenLLM(read=3000))["invalid"] == []


def test_a_run_that_writes_a_real_store_is_invalid(tmp_path, monkeypatch):
    real = tmp_path / "cwd"
    real.mkdir()
    leak = real / "data/examples/leak.json"

    def leaking(case_name):
        leak.parent.mkdir(parents=True)
        leak.write_text("{}")                     # an absolute path: the isolation cannot catch it
        return {"case": case_name, "run": 1, "veracity": "PASS", "reason": "", "tier": "A",
                "solver": "structured", "model": ""}

    monkeypatch.setattr(vr, "solve_structured", leaking)
    record, _ = _main_structured(tmp_path, monkeypatch)
    assert record["invalid"] and record["invalid"][0].startswith("store_touched")
    assert str(leak) in record["invalid"][0]


def test_every_solver_runs_isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("KUBEWHISPERER_DB", str(tmp_path / "real.db"))
    seen = {}

    def spy(case_name):
        seen.update(cwd=Path.cwd(), db=os.environ["KUBEWHISPERER_DB"],
                    examples=os.environ["KUBEVERDICT_EXAMPLE_DIR"], index=cfg.VECTOR_STORE_PATH)
        return {"case": case_name, "run": 1, "veracity": "PASS", "reason": "", "tier": "A",
                "solver": "structured", "model": ""}

    monkeypatch.setattr(vr, "solve_structured", spy)
    index_before = cfg.VECTOR_STORE_PATH
    _main_structured(tmp_path, monkeypatch)
    tmp = seen["cwd"]
    assert tmp.name.startswith("kv-replay-") and not tmp.exists()
    for key in ("db", "examples", "index"):
        assert Path(seen[key]).is_relative_to(tmp), key
    assert os.environ["KUBEWHISPERER_DB"] == str(tmp_path / "real.db")             # restored
    assert cfg.VECTOR_STORE_PATH == index_before


def test_the_store_check_sees_a_write_and_nothing_else(tmp_path):
    (tmp_path / "data").mkdir()
    db = tmp_path / "kubeverdict.db"
    db.write_text("x")
    watched = vr.watched_stores(tmp_path)
    before = vr.fingerprint(watched)
    assert vr.store_changes(before, vr.fingerprint(watched)) == []                 # control
    db.read_text()                                                     # a read is not a write
    assert vr.store_changes(before, vr.fingerprint(watched)) == []
    (tmp_path / "data/new.json").write_text("{}")
    changes = vr.store_changes(before, vr.fingerprint(watched))
    assert str(tmp_path / "data/new.json") in changes
