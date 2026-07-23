"""PR/MR-first phase 3 — the invokable propose_patch use-case (offline)."""
from __future__ import annotations

import pytest

from remediation import propose_patch as pp
from remediation.change_proposer import ProposedChange
from remediation.propose_patch import propose_patch

REM = ["helm upgrade api ./chart -n production --set replicaCount=3"]
VALUES = "replicaCount: 1\n"


def test_dry_run_builds_patch_without_opening(monkeypatch):
    called = {"proposer": False}

    def _no_proposer(*a, **k):
        called["proposer"] = True
        raise AssertionError("dry-run must not open a PR")

    monkeypatch.setattr(pp, "make_change_proposer", _no_proposer)

    out = propose_patch(
        REM, repo_url="https://github.com/acme/api", file_path="chart/values.yaml",
        values_yaml=VALUES, open_pr=False,
    )
    assert out["proposed"] is False
    assert out["dry_run"] is True
    assert out["change"] is None
    assert "+replicaCount: 3" in out["patch"]["diff"]
    assert called["proposer"] is False


def test_empty_patch_is_reported_not_opened(monkeypatch):
    monkeypatch.setattr(pp, "make_change_proposer",
                        lambda *a, **k: pytest.fail("should not open"))
    # values.yaml already declares replicaCount: 3 → no-op
    out = propose_patch(
        REM, repo_url="https://github.com/acme/api", file_path="chart/values.yaml",
        values_yaml="replicaCount: 3\n", open_pr=True, token="tok",
    )
    assert out["proposed"] is False
    assert "no declarative values change" in out["reason"]
    assert out["change"] is None


def test_open_pr_invokes_proposer(monkeypatch):
    class StubProposer:
        def propose(self, patch, *, draft=True):
            assert not patch.is_empty
            return ProposedChange(url="https://github.com/acme/api/pull/5",
                                  branch="kubeverdict/fix-api-abcd1234",
                                  provider="github", draft=draft, number=5)

    captured = {}

    def _factory(url, *, base, token, provider):
        captured.update(url=url, base=base, token=token, provider=provider)
        return StubProposer()

    monkeypatch.setattr(pp, "make_change_proposer", _factory)

    out = propose_patch(
        REM, repo_url="https://github.com/acme/api", file_path="chart/values.yaml",
        values_yaml=VALUES, open_pr=True, token="tok",
    )
    assert out["proposed"] is True
    assert out["dry_run"] is False
    assert out["change"]["url"].endswith("/pull/5")
    assert out["change"]["provider"] == "github"
    assert captured["token"] == "tok"


def test_missing_file_is_handled(monkeypatch):
    class NoFileProvider:
        def get_file(self, path):
            return None

    monkeypatch.setattr(pp, "make_provider", lambda *a, **k: NoFileProvider())
    out = propose_patch(
        REM, repo_url="https://github.com/acme/api", file_path="chart/values.yaml",
        open_pr=False,
    )
    assert out["proposed"] is False
    assert "could not read" in out["reason"]


def test_reads_values_from_repo_when_not_supplied(monkeypatch):
    class Provider:
        def get_file(self, path):
            assert path == "chart/values.yaml"
            return VALUES

    monkeypatch.setattr(pp, "make_provider", lambda *a, **k: Provider())
    out = propose_patch(
        REM, repo_url="https://github.com/acme/api", file_path="chart/values.yaml",
        open_pr=False,
    )
    assert "+replicaCount: 3" in out["patch"]["diff"]
