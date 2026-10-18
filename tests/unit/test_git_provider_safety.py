"""Git provider safety — docs/fix-plan-001.md, H42 (reads stay in the clone) and H31 (the token
never goes in a URL, an argv or an error message)."""
from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from ingestion.git_provider import LocalGitProvider

TOKEN = "ghp_NOT_A_REAL_TOKEN_123"


@pytest.fixture
def clone(tmp_path):
    repo = tmp_path / "clones" / "infra"
    (repo / ".git").mkdir(parents=True)
    (repo / "values.yaml").write_text("replicaCount: 3\n")
    (tmp_path / "outside.yaml").write_text("private: s3cr3t\n")
    (repo / "link.yaml").symlink_to(tmp_path / "outside.yaml")
    provider = LocalGitProvider("https://git.example/acme/infra.git", clone_dir=tmp_path / "clones")
    return provider


def test_a_file_in_the_repository_is_read(clone):
    assert clone.get_file("values.yaml") == "replicaCount: 3\n"


@pytest.mark.parametrize("path", ["../../outside.yaml", "link.yaml", "/etc/hostname"])
def test_a_path_outside_the_repository_is_refused(clone, path):
    assert clone.get_file(path) is None


def test_list_files_does_not_leave_the_repository(clone):
    assert clone.list_files("..", "*.yaml") == []
    assert clone.list_files(".", "*.yaml") == ["values.yaml"]          # the symlink is skipped


def test_the_token_is_not_in_argv_but_reaches_git(tmp_path):
    provider = LocalGitProvider("https://git.example/acme/infra.git", token=TOKEN,
                                clone_dir=tmp_path)
    with patch("ingestion.git_provider.subprocess.run") as run:
        provider._ensure_cloned()
    argv, env = run.call_args.args[0], run.call_args.kwargs["env"]
    assert all(TOKEN not in a for a in argv)
    assert "https://git.example/acme/infra.git" in argv
    import base64
    header = env[f"GIT_CONFIG_VALUE_{int(env['GIT_CONFIG_COUNT']) - 1}"]
    assert base64.b64decode(header.split()[-1]).decode() == f"oauth2:{TOKEN}"   # control


def test_a_failed_clone_does_not_log_the_token(tmp_path, caplog):
    provider = LocalGitProvider("https://git.invalid/acme/infra.git", token=TOKEN,
                                clone_dir=tmp_path)
    assert provider.get_file("values.yaml") is None       # real git, unreachable host
    assert "infra" in caplog.text and TOKEN not in caplog.text


def test_no_token_for_a_non_http_url(tmp_path):
    provider = LocalGitProvider("file:///srv/repo", token=TOKEN, clone_dir=tmp_path)
    assert all(TOKEN not in v for v in provider._git_env().values())
