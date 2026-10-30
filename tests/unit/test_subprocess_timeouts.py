"""Every helm / git call is bounded; a hang is a recorded failure — docs/fix-plan-001.md H56."""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

from ingestion.git_provider import LocalGitProvider
from ingestion.helm_collector import HelmCollector


def _hang(*args, **kwargs):
    if "timeout" not in kwargs:                  # without a timeout the call would hang forever
        raise AssertionError("subprocess.run called without a timeout")
    raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])


def test_a_hung_helm_is_a_recorded_failure():
    c = HelmCollector()
    with patch("subprocess.run", side_effect=_hang):
        assert c._run_json(["helm", "list"], "helm list") == {}
    assert any("timed out" in f for f in c.failures)


def test_a_helm_call_that_answers_records_no_failure():          # control
    c = HelmCollector()
    with patch("subprocess.run", return_value=MagicMock(stdout='[{"name": "api"}]', returncode=0)) as run:
        assert c._run_json(["helm", "list"], "helm list") == [{"name": "api"}]
    assert "timeout" in run.call_args.kwargs and not getattr(c, "failures", [])


def test_a_hung_clone_is_a_recorded_failure(tmp_path):
    provider = LocalGitProvider("https://git.example/acme/infra.git", clone_dir=tmp_path)
    with patch("ingestion.git_provider.subprocess.run", side_effect=_hang):
        assert provider.local_path() is None
    assert "timed out" in provider.last_error


def test_a_hung_pull_keeps_the_checkout_and_says_it_may_be_stale(tmp_path):
    provider = LocalGitProvider("https://git.example/acme/infra.git", clone_dir=tmp_path)
    (provider._repo_dir() / ".git").mkdir(parents=True)
    with patch("ingestion.git_provider.subprocess.run", side_effect=_hang):
        assert provider.local_path() == provider._repo_dir()
    assert "stale" in provider.last_error and "timed out" in provider.last_error
