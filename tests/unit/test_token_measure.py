"""The tokenizer measurement before the live run — docs/live-validation-001.md, step 4. No
Ollama: a scripted HTTP answer counts one token per three characters."""
from __future__ import annotations

import json

import pytest

from tools import token_measure as tm
from tools import veracity_replay as vr


class _Resp:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


@pytest.fixture
def ollama(monkeypatch):
    sent = []

    def post(url, json=None, timeout=None):
        sent.append(json)
        return _Resp({"prompt_eval_count": len(json["prompt"] + json.get("system", "")) // 3, "response": "x"})
    monkeypatch.setattr(tm.requests, "post", post)
    return sent


def _client(num_ctx_max=8192):
    c = tm.MeasuringClient("mistral", url="http://ollama.invalid")
    c.sizer.num_ctx_max = num_ctx_max
    return c


def test_every_case_and_cut_is_measured_once_per_solver(ollama):
    rows = tm.measure(_client(), vr.native_cases())
    expected = {(c, s, k) for c in vr.native_cases() for k in (None, *vr.cuttable(c)) for s in tm.SOLVERS}
    assert {(r["case"], r["solver"], r["cut"]) for r in rows} == expected and len(rows) == len(expected)
    assert all(r["calls"] == 1 and "error" not in r for r in rows)     # one call per run
    assert len(rows) == 2 * (19 + 8)


def test_the_request_measures_without_truncating(ollama):
    tm.measure(_client(), ["h001_crashloopbackoff"])
    raw, context = ollama
    assert raw["options"] == {"num_predict": 1, "num_ctx": tm.MEASURE_CTX, "temperature": 0}
    # each solver's system prompt is part of what the model reads
    from rca.analyzer import _SYSTEM_PROMPT
    assert raw["system"] == vr._RAW_SYSTEM_PROMPT and context["system"] == _SYSTEM_PROMPT


def test_a_short_prompt_fits(ollama):                                                # control
    assert {r["verdict"] for r in tm.measure(_client(), ["h001_crashloopbackoff"])} == {"fits"}


def test_a_prompt_over_the_ceiling_did_not_fit_and_is_still_measured(ollama):
    rows = tm.measure(_client(num_ctx_max=2048), ["h001_crashloopbackoff"])
    assert {r["verdict"] for r in rows} == {"did_not_fit"}
    assert all(r["tokens"] > 0 and r["num_ctx"] is None for r in rows)


def test_a_prompt_the_estimate_undersizes_would_truncate():
    assert tm.verdict(tokens=3000, num_ctx=3072, num_predict=512) == "would_truncate"
    assert tm.verdict(tokens=2000, num_ctx=3072, num_predict=512) == "fits"


@pytest.mark.parametrize("tokens", [None, tm.MEASURE_CTX - 1, tm.MEASURE_CTX])
def test_a_truncated_or_missing_measurement_is_invalid(tokens):
    assert tm.verdict(tokens, 4096, 512) == "invalid"
    assert tm.verdict(tokens, None, 512) == "invalid"


def test_nothing_is_written_to_a_real_store(ollama, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tm.MeasuringClient, "model_digest", lambda self: "sha256:abc")
    out = tmp_path / "out/tokens.json"
    assert tm.main(["--model", "mistral", "--cases", "h001", "--out", str(out)]) == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == ["out"]
    table = json.loads(out.read_text())
    assert table["model_digest"] == "sha256:abc" and table["summary"]["fits"] == 2
    assert table["measure_num_ctx"] == tm.MEASURE_CTX and len(table["rows"]) == 2


def test_each_solver_runs_inside_isolated_stores(monkeypatch):
    # the solvers write nothing on this path today: the isolation is checked where they run
    seen = []

    def spy(case, client, run, cut=None):
        import os
        seen.append(("kv-replay-" in os.getcwd(), "kv-replay-" in os.environ.get("KUBEVERDICT_EXAMPLE_DIR", "")))
    monkeypatch.setattr(vr, "solve_raw", spy)
    monkeypatch.setattr(vr, "solve_context", spy)
    tm.measure(_client(), ["h001_crashloopbackoff"])
    assert seen == [(True, True), (True, True)]


def test_the_measuring_context_is_above_any_run():                                   # CPI-27
    # whether it fits the pod's memory is not computable here (compute buffers): checked live
    import config as cfg
    assert tm.MEASURE_CTX >= 2 * cfg.OLLAMA_NUM_CTX_MAX


# ── C2's prompts (CPI-26) ───────────────────────────────────────────────────────

def _key(r):
    return {k: v for k, v in r.items() if k not in ("store",)}


def test_with_faiss_each_case_adds_one_c2_prompt_and_no_cut(ollama):
    rows = tm.measure(_client(), vr.native_cases(), store="faiss")
    c2 = [r for r in rows if r["store"] == "faiss"]
    assert {(r["case"], r["solver"], r["cut"]) for r in c2} == {(c, "context", None) for c in vr.native_cases()}
    assert len(c2) == 19 and all(r["calls"] == 1 and "error" not in r for r in c2)
    assert len(rows) == 2 * (19 + 8) + 19


def test_the_stub_rows_are_the_same_with_or_without_faiss(ollama):            # control
    without = tm.measure(_client(), ["h013_slo_error_budget_burn"])
    with_c2 = [r for r in tm.measure(_client(), ["h013_slo_error_budget_burn"], store="faiss")
               if r["store"] == "stub"]
    assert without and all("error" not in r for r in without)
    assert [_key(r) for r in with_c2] == [_key(r) for r in without]


def test_a_c2_prompt_is_the_one_the_vector_search_builds(ollama, monkeypatch):
    seen = []
    real = vr.solve_context

    def spy(case, client, run, cut=None, store="stub", examples="off"):
        seen.append((store, examples))
        return real(case, client, run, cut, store, examples)
    monkeypatch.setattr(vr, "solve_context", spy)
    tm.measure(_client(), ["h013_slo_error_budget_burn"], store="faiss")
    assert seen[-1] == ("faiss", "off") and all(s == ("stub", "off") for s in seen[:-1])
    stub_context, c2 = ollama[1]["prompt"], ollama[-1]["prompt"]
    assert c2 != stub_context                       # the search's hits are in the measured text


def test_the_table_names_its_store(ollama, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tm.MeasuringClient, "model_digest", lambda self: "sha256:abc")
    out = tmp_path / "out/tokens.json"
    assert tm.main(["--model", "mistral", "--cases", "h001", "--store", "faiss", "--out", str(out)]) == 0
    table = json.loads(out.read_text())
    assert table["store"] == "faiss" and len(table["rows"]) == 3 and table["summary"]["fits"] == 3
