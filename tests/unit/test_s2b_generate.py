"""tools/s2b_generate.py without any network: the dry run, and the key's handling (docs/s2b-generation.md § The key)."""
from __future__ import annotations

import json
import urllib.request

import pytest

from tools import s2b_generate as g

FAKE = "xai-FAKE-KEY-0123456789"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("network called")
    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    monkeypatch.delenv("XAI_API_KEY", raising=False)


def _key_file(home, mode=0o600):
    p = home / ".config/xai/api_key"
    p.parent.mkdir(parents=True)
    p.write_text(FAKE + "\n")
    p.chmod(mode)
    return p


def test_the_dry_run_sends_nothing_and_never_prints_the_key(tmp_path, capsys):
    _key_file(tmp_path)
    assert g.main(["--batch", "pilot-1", "--dry-run"], home=tmp_path) == 0
    out = capsys.readouterr().out
    assert FAKE not in out
    shown = json.loads(out)
    assert shown["key"] == "found (file ~/.config/xai/api_key)"
    assert shown["temperature"] == g.TEMPERATURE and shown["max_tokens"] == 32000
    assert shown["user_message"].count("- one scenario") == 11


def test_the_env_key_comes_first(tmp_path, monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", FAKE)
    assert g.read_key(tmp_path) == (FAKE, "env XAI_API_KEY")


def test_a_key_file_readable_by_others_is_refused(tmp_path):
    _key_file(tmp_path, 0o644)
    with pytest.raises(g.KeyError_, match="0o644"):
        g.read_key(tmp_path)


def test_without_a_key_a_real_run_stops_before_any_call(tmp_path, capsys):
    assert g.main(["--batch", "pilot-1", "--model", "m"], home=tmp_path) == 2
    assert "no key" in capsys.readouterr().err
