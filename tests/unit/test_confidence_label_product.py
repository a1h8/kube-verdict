"""The product reads the LLM's confidence label as the measurement does —
docs/fix-plan-001.md, H20 (26/57 mistral runs of 2026-10-17 wrote a decorated label)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from decision.decision_engine import DecisionEngine
from decision.labels import normalize_label
from tools.veracity import normalize_label as grader_label
from workflow.nodes import analyze_node


@pytest.mark.parametrize("raw,label", [("MEDIUM:", "MEDIUM"), ("LOW: sparse", "LOW"),
                                       ("HIGH.", "HIGH"), ("medium", "MEDIUM"), ("", "")])
def test_one_normaliser(raw, label):
    assert normalize_label(raw) == grader_label(raw) == label


def test_a_decorated_high_scores_as_high():
    engine = DecisionEngine()
    assert engine.score_for("HIGH:") == engine.score_for("HIGH") != engine.score_for("garbage")


def test_analyze_node_stores_the_bare_label():
    report = MagicMock(confidence="LOW: the context is sparse", raw_analysis="x")
    report.context.total_chunks = 0
    analyzer = MagicMock()
    analyzer.analyze.return_value = report
    with patch("workflow.nodes.RCAAnalyzer", return_value=analyzer), \
            patch("workflow.nodes.ContextBuilder"):
        out = analyze_node({"query": "q"}, {"configurable": {"graph": MagicMock(), "store": MagicMock(),
                                                              "llm": MagicMock()}})
    assert out["confidence"] == "LOW"            # the router compares == "LOW": retry happens
