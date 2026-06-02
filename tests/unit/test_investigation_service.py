"""Unit tests for the canonical investigation service (MCP/API unification)."""
from __future__ import annotations

import pytest

import services.investigation_service as inv
from services.investigation_service import run_investigation, verdict_summary


# ── verdict_summary projection ──────────────────────────────────────────────────

class TestVerdictSummary:
    def test_projects_report_and_decision(self):
        state = {
            "report_dict": {
                "query": "pods crashlooping",
                "summary": "api OOMKilled",
                "root_cause": "memory limit too low",
                "causal_chain": ["limit", "OOM"],
                "affected": ["Deployment/prod/api"],
                "remediation": ["kubectl set resources ..."],
                "rollback": ["kubectl rollout undo ..."],
                "confidence": "HIGH",
                "pre_llm_confidence": {"score": 0.8, "label": "HIGH"},
            },
            "verdict": "HUMAN_REVIEW",
            "verdict_reasons": ["namespace 'prod' is production"],
            "blast_radius": {"risk": "MEDIUM"},
        }
        out = verdict_summary(state)
        assert out["root_cause"] == "memory limit too low"
        assert out["verdict"] == "HUMAN_REVIEW"
        assert out["verdict_reasons"] == ["namespace 'prod' is production"]
        assert out["blast_radius"] == {"risk": "MEDIUM"}
        assert out["pre_llm_confidence"] == {"score": 0.8, "label": "HIGH"}

    def test_empty_state_is_safe(self):
        out = verdict_summary({})
        assert out["root_cause"] == ""
        assert out["remediation"] == []
        assert out["verdict"] is None
        assert out["blast_radius"] is None


# ── run_investigation: proposal-only (stops at verdict) ─────────────────────────

class _FakeGraph:
    """Stand-in compiled graph whose astream yields a fixed sequence of states."""

    def __init__(self, states):
        self._states = states
        self.consumed: list[dict] = []

    async def astream(self, initial_state, cfg, stream_mode="values"):
        for s in self._states:
            self.consumed.append(s)
            yield s


@pytest.mark.asyncio
async def test_run_investigation_stops_at_verdict(monkeypatch):
    states = [
        {"report_dict": {"root_cause": "x"}},                                  # pre-decision
        {"report_dict": {"root_cause": "x"}, "verdict": "HUMAN_REVIEW"},        # decision point
        {"report_dict": {"root_cause": "x"}, "verdict": "HUMAN_REVIEW",
         "remediation_applied": True},                                          # MUST NOT be reached
    ]
    fake = _FakeGraph(states)
    monkeypatch.setattr(inv, "_graph", fake)

    result = await run_investigation(query="pods crashlooping", namespaces=["demo"])

    assert result["verdict"] == "HUMAN_REVIEW"
    # Proposal-only: streaming stopped at the verdict, before the apply state.
    assert len(fake.consumed) == 2
    assert "remediation_applied" not in result


@pytest.mark.asyncio
async def test_run_investigation_returns_last_state_when_no_verdict(monkeypatch):
    states = [{"report_dict": {"summary": "a"}}, {"report_dict": {"summary": "b"}}]
    monkeypatch.setattr(inv, "_graph", _FakeGraph(states))
    result = await run_investigation(query="q")
    assert result["report_dict"]["summary"] == "b"
