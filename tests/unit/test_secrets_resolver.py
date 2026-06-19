"""Secret resolution — env → file-mounted secret → Vault (no plaintext config)."""
from __future__ import annotations

import json
from unittest.mock import patch

from api import secrets


def test_env_takes_precedence(monkeypatch, tmp_path):
    monkeypatch.setenv("MY_SECRET", "from-env")
    monkeypatch.setattr(secrets, "SECRETS_DIR", str(tmp_path))
    (tmp_path / "MY_SECRET").write_text("from-file")
    assert secrets.resolve_secret("MY_SECRET") == "from-env"


def test_file_mounted_secret(monkeypatch, tmp_path):
    monkeypatch.delenv("MY_SECRET", raising=False)
    monkeypatch.setattr(secrets, "SECRETS_DIR", str(tmp_path))
    (tmp_path / "MY_SECRET").write_text("from-file\n")
    assert secrets.resolve_secret("MY_SECRET") == "from-file"


def test_default_when_absent(monkeypatch, tmp_path):
    monkeypatch.delenv("MY_SECRET", raising=False)
    monkeypatch.setattr(secrets, "SECRETS_DIR", str(tmp_path))
    for var in ("VAULT_ADDR", "VAULT_TOKEN", "VAULT_KV_PATH"):
        monkeypatch.delenv(var, raising=False)
    assert secrets.resolve_secret("MY_SECRET", default="fallback") == "fallback"


def test_vault_kv_v2(monkeypatch, tmp_path):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setattr(secrets, "SECRETS_DIR", str(tmp_path))
    monkeypatch.setenv("VAULT_ADDR", "https://vault.local")
    monkeypatch.setenv("VAULT_TOKEN", "t")
    monkeypatch.setenv("VAULT_KV_PATH", "secret/data/kubeverdict")

    class FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps({"data": {"data": {"GROQ_API_KEY": "vault-val"}}}).encode()

    with patch("urllib.request.urlopen", return_value=FakeResp()):
        # urlopen returns FakeResp; json.load reads .read()
        with patch("json.load", side_effect=lambda r: json.loads(r.read())):
            assert secrets.resolve_secret("GROQ_API_KEY") == "vault-val"
