"""
Security tests for HelmCollector's command barrier (CodeQL alert #5,
py/command-line-injection).

The real defence is structural: helm is always run with a list argv and
shell=False, and every dynamic token is allowlist-validated inline with the
`subprocess.run` call in `_exec`. These tests pin that no untrusted token can
reach the exec.
"""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from ingestion.helm_collector import HelmCollector


def _run_ok(stdout: str = "") -> MagicMock:
    r = MagicMock()
    r.stdout = stdout
    r.stderr = ""
    r.returncode = 0
    return r


# ─────────────────────────────────────────────────────────────────────────────
# _exec — inline allowlist barrier
# ─────────────────────────────────────────────────────────────────────────────

class TestExecBarrier:
    def test_allows_valid_argv(self):
        c = HelmCollector()
        with patch("subprocess.run", return_value=_run_ok("ok")) as m:
            assert c._exec(["helm", "get", "notes", "api", "--namespace", "prod"]) == "ok"
        assert m.called

    def test_rejects_shell_metacharacters(self):
        c = HelmCollector()
        with patch("subprocess.run") as m:
            with pytest.raises(ValueError, match="unsafe helm argument"):
                c._exec(["helm", "get", "notes", "api; rm -rf /", "--namespace", "prod"])
        m.assert_not_called()

    def test_rejects_value_with_spaces(self):
        c = HelmCollector()
        with patch("subprocess.run") as m:
            with pytest.raises(ValueError, match="unsafe helm argument"):
                c._exec(["helm", "list", "--namespace", "prod ns"])
        m.assert_not_called()

    def test_rejects_unknown_flag(self):
        c = HelmCollector()
        with patch("subprocess.run") as m:
            with pytest.raises(ValueError, match="unsafe helm argument"):
                c._exec(["helm", "list", "--kube-as-user"])
        m.assert_not_called()

    def test_rejects_injected_leading_dash_value(self):
        c = HelmCollector()
        with patch("subprocess.run") as m:
            with pytest.raises(ValueError, match="unsafe helm argument"):
                c._exec(["helm", "get", "values", "-rf"])
        m.assert_not_called()

    def test_allows_validated_kubeconfig_path(self, tmp_path):
        kube = tmp_path / "config"
        kube.write_text("apiVersion: v1")
        c = HelmCollector(kubeconfig=str(kube))
        resolved = str(kube.resolve())
        with patch("subprocess.run", return_value=_run_ok("[]")) as m:
            c._exec(["helm", "list", "--output", "json", "--kubeconfig", resolved])
        assert m.called  # path contains "/" but is allowlisted in __init__


# ─────────────────────────────────────────────────────────────────────────────
# Callers fail closed on a blocked argument
# ─────────────────────────────────────────────────────────────────────────────

class TestCallersFailClosed:
    def test_run_json_returns_empty_on_unsafe_argument(self):
        c = HelmCollector()
        with patch("subprocess.run") as m:
            assert c._run_json(["helm", "list", "; evil"], "helm list") == {}
        m.assert_not_called()

    def test_get_notes_returns_empty_on_unsafe_release(self):
        c = HelmCollector()
        with patch("subprocess.run") as m:
            assert c._get_notes("bad name", "prod") == ""
        m.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# Constructor validation (inline regex guard)
# ─────────────────────────────────────────────────────────────────────────────

class TestConstructorGuard:
    def test_kubeconfig_not_found_raises(self):
        with pytest.raises(ValueError, match="kubeconfig not found"):
            HelmCollector(kubeconfig="/nonexistent/path/config")

    def test_unsafe_kube_context_raises(self, tmp_path):
        kube = tmp_path / "config"
        kube.write_text("apiVersion: v1")
        with pytest.raises(ValueError, match="unsafe kube_context"):
            HelmCollector(kubeconfig=str(kube), kube_context="../../../etc/passwd")

    def test_kube_context_with_shell_chars_raises(self, tmp_path):
        kube = tmp_path / "config"
        kube.write_text("apiVersion: v1")
        with pytest.raises(ValueError, match="unsafe kube_context"):
            HelmCollector(kubeconfig=str(kube), kube_context="ctx; rm -rf /")

    def test_valid_kube_context_recorded_as_safe(self, tmp_path):
        kube = tmp_path / "config"
        kube.write_text("apiVersion: v1")
        c = HelmCollector(kubeconfig=str(kube), kube_context="k3s-prod")
        assert "k3s-prod" in c._env_flags
        assert "k3s-prod" in c._safe_argv_values
