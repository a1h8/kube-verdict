"""The CLI runs the canonical pipeline — docs/fix-plan-001.md, H3."""
from __future__ import annotations

import main as cli

STATE = {"report_dict": {"query": "q", "root_cause": "The ConfigMap cfg does not exist.", "confidence": "MEDIUM",
                         "remediation": ["kubectl create configmap cfg -n prod"],
                         "diagnostics": [{"command": "kubectl get configmap cfg -n prod"}]},
         "verdict": "HUMAN_REVIEW", "verdict_reasons": ["namespace 'prod' is production"]}


def _fake(calls):
    async def run_investigation(**kwargs):
        calls.append(kwargs)
        if kwargs.get("on_step"):
            kwargs["on_step"]({"query": "q"})
            kwargs["on_step"]({"query": "q", "verdict": "HUMAN_REVIEW"})
        return STATE
    return run_investigation


def test_the_cli_runs_the_investigation_service(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli, "run_investigation", _fake(calls))
    assert cli.main(["--query", "why is api down", "-n", "prod"]) == 0
    (kwargs,) = calls
    assert kwargs["query"] == "why is api down" and kwargs["namespaces"] == ["prod"]
    out = capsys.readouterr().out
    assert "HUMAN_REVIEW" in out and "ConfigMap cfg" in out and "kubectl get configmap cfg" in out


def test_the_cli_has_no_pipeline_of_its_own():
    """Control: the old path (its own collectors + RCAAnalyzer) is gone from the CLI module."""
    for name in ("RCAAnalyzer", "K8sCollector", "HelmDriftDetector", "PolicyCollector"):
        assert not hasattr(cli, name), name


def test_stream_prints_the_pipeline_steps(monkeypatch, capsys):
    monkeypatch.setattr(cli, "run_investigation", _fake([]))
    cli.main(["--query", "q", "--stream"])
    out = capsys.readouterr().out
    assert "· query" in out and "· verdict" in out
