"""Public claims say what the code does — docs/fix-plan-001.md, H8. Each phrase below was
false (a remote LLM provider, the embedding download and the GitHub API do send data; the
database is always SQLite; the live cluster is compared with what Helm applied, not diffed
with a render)."""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FILES = ["SKILL.md", ".codex-plugin/skills/kube-verdict/SKILL.md", ".claude-plugin/plugin.json",
         ".codex-plugin/plugin.json", ".claude-plugin/marketplace.json", "openapi_tools.json",
         "SECURITY.md", "docs/configuration.md", "README.md"]
FALSE = ["no data leaves", "no external calls", "No data is sent to external services",
         "runs air-gapped via local Ollama", "Runs air-gapped via", "Postgres URL when scaling",
         "diffs it against the live cluster"]


@pytest.mark.parametrize("phrase", FALSE)
def test_the_false_claim_is_gone(phrase):
    hits = [f for f in FILES if phrase.lower() in (ROOT / f).read_text().lower()]
    assert hits == []


def test_the_remote_provider_is_disclosed():                               # control
    text = (ROOT / "SECURITY.md").read_text()
    assert "remote provider" in text and "receives the prompt" in text
