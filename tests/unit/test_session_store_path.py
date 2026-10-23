"""init_store opens the path it is given and leaves the environment alone — fix plan H63."""
from __future__ import annotations

import os

import api.session_store as ss


def test_init_store_does_not_rewrite_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("KUBEWHISPERER_DB", str(tmp_path / "other.db"))
    try:
        ss.init_store(str(tmp_path / "sessions.db"))
        assert os.environ["KUBEWHISPERER_DB"] == str(tmp_path / "other.db")
        assert (tmp_path / "sessions.db").exists()                  # control: the given path is used
        assert not (tmp_path / "other.db").exists()
    finally:
        ss.init_store(":memory:")
