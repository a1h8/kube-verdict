"""Context window sizing — docs/case-audit-001.md F8.

Ollama's default 2048-token window cut every case prompt (logs: 6 212 tokens → 1 027).
The client now sizes num_ctx per call, refuses a prompt that cannot fit, and records what
Ollama actually read.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from llm.ollama_client import OllamaClient, PromptTooLongError

# (prompt + system characters, tokens Ollama read) — mistral, real case prompts, 2026-10-17
CALIBRATION = [(7233, 2343), (14011, 4569)]


def _client(**kw) -> OllamaClient:
    return OllamaClient(url="http://ollama.invalid", model="mistral",
                        num_ctx=kw.pop("num_ctx", 2048), num_predict=kw.pop("num_predict", 512), **kw)


@pytest.mark.parametrize("chars,read", CALIBRATION)
def test_window_covers_the_measured_prompts(chars, read):
    client = _client(num_ctx_max=16384)
    estimated, num_ctx = client.context_for(chars)
    assert estimated >= read                      # never sized below what Ollama counts
    assert num_ctx >= read + client.num_predict   # room for the whole answer too


def test_the_old_fixed_window_did_not_fit_even_the_smallest_case():
    chars, read = CALIBRATION[0]                  # h001, the simplest case
    assert read + 512 > 2048


def test_short_prompt_keeps_the_configured_floor():
    assert _client(num_ctx=2048).context_for(1000)[1] == 2048


def test_a_prompt_that_cannot_fit_is_refused():
    with pytest.raises(PromptTooLongError):
        _client(num_ctx_max=4096).context_for(20000)


def test_generate_sends_the_sized_window_and_records_what_was_read():
    client = _client(num_ctx_max=16384)
    response = MagicMock(status_code=200)
    response.json.return_value = {"response": " ok ", "prompt_eval_count": 2343, "eval_count": 7,
                                  "prompt_eval_duration": 103_400_000_000, "eval_duration": 1_000_000_000}
    with patch("llm.ollama_client.requests.post", return_value=response) as post:
        assert client.generate("x" * 7000, system="y" * 233) == "ok"
    sent = post.call_args.kwargs["json"]["options"]["num_ctx"]
    assert sent >= 2343 + 512
    assert client.last_call["num_ctx"] == sent
    assert client.last_call["prompt_tokens_read"] == 2343
    assert client.last_call["estimated_prompt_tokens"] >= 2343


def test_every_native_case_prompt_fits_under_the_default_ceiling():
    """The replay must never refuse a case: each analyze prompt fits OLLAMA_NUM_CTX_MAX."""
    import json
    from pathlib import Path

    import config as cfg
    from rca.analyzer import _SYSTEM_PROMPT
    from tools.reasoning_path import _RecordOnly
    from tests.integration.workflow_harness import run_case

    cases = Path(__file__).resolve().parents[1] / "integration" / "cases"
    client = _client(num_ctx_max=cfg.OLLAMA_NUM_CTX_MAX)
    for d in sorted(cases.glob("h0*/")):
        if not json.loads((d / "expect.json").read_text()).get("query"):
            continue
        for prompt in run_case(d.name.split("_")[0], llm=_RecordOnly()).llm.analyze_prompts:
            client.context_for(len(prompt) + len(_SYSTEM_PROMPT))  # raises if it does not fit
