"""
Integration tests — real LLM evaluation over the 20-case bank.

Each case in cases/0*/ is run through the full pipeline with a live Ollama
instance. Tests evaluate RCA response quality against expect.json criteria.

Skip conditions (not failures):
  - Ollama not reachable
  - Required model not pulled

Run:
    pytest tests/integration/use_cases/ -m integration -v
    pytest tests/integration/use_cases/ -m integration -k "001"
"""
from __future__ import annotations

from pathlib import Path

import pytest

import config as cfg
from llm.ollama_client import OllamaClient
from rca.analyzer import RCAAnalyzer, RCAReport
from tests.cases.graph_factory import build_graph, load_case
from vectorstore.embedder import Embedder
from vectorstore.store import FAISSStore

pytestmark = pytest.mark.integration

CASES_ROOT = Path(__file__).parent.parent.parent.parent / "cases"
CASE_DIRS  = sorted(CASES_ROOT.glob("0*/"))
CASE_IDS   = [d.name for d in CASE_DIRS]

assert CASE_DIRS, f"No case directories found under {CASES_ROOT}"

_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def ollama_client() -> OllamaClient:
    client = OllamaClient()
    if not client.is_available():
        pytest.skip(f"Ollama not reachable at {cfg.OLLAMA_URL} — run: ollama serve")
    if not client.model_is_pulled():
        pytest.skip(
            f"Model '{cfg.OLLAMA_MODEL}' not pulled — run: ollama pull {cfg.OLLAMA_MODEL}"
        )
    return client


@pytest.fixture(scope="module", params=CASE_DIRS, ids=CASE_IDS)
def case_rca(request, ollama_client: OllamaClient):
    """Build graph + store, run RCAAnalyzer, return (report, expect, case_name)."""
    case_dir = request.param
    data     = load_case(case_dir)
    inp      = data["input"]

    graph = build_graph(inp)
    store = FAISSStore(embedder=Embedder())
    store.index_graph(graph)

    analyzer = RCAAnalyzer(graph=graph, store=store, llm=ollama_client)
    report   = analyzer.analyze(inp["query"])

    return report, data["expect"], case_dir.name


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _full_text(report: RCAReport) -> str:
    """Concatenate all textual fields of a report for keyword search."""
    parts = [
        report.raw_analysis,
        report.root_cause,
        " ".join(report.affected),
        " ".join(report.remediation),
        report.confidence,
    ]
    return " ".join(p for p in parts if p).lower()


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

class TestRCAQuality:

    def test_report_is_not_empty(self, case_rca):
        report, _, case_name = case_rca
        assert report.raw_analysis.strip(), f"{case_name}: raw_analysis is empty"

    def test_root_cause_keyword_coverage(self, case_rca):
        """At least 50 % of root_cause_contains keywords must appear in the response."""
        report, expect, case_name = case_rca
        keywords  = expect["root_cause_contains"]
        full_text = _full_text(report)

        matched   = [kw for kw in keywords if kw.lower() in full_text]
        threshold = max(1, len(keywords) // 2)

        assert len(matched) >= threshold, (
            f"{case_name}: only {len(matched)}/{len(keywords)} root-cause keywords found "
            f"({matched!r}). Missing: {[kw for kw in keywords if kw.lower() not in full_text]!r}"
        )

    def test_fix_commands_present(self, case_rca):
        """At least 1 expected fix command substring must appear in the response."""
        report, expect, case_name = case_rca
        fix_substrings = expect.get("fix_commands_contain", [])
        if not fix_substrings:
            pytest.skip(f"{case_name}: no fix_commands_contain in expect.json")

        full_text = _full_text(report)
        matched   = [s for s in fix_substrings if s.lower() in full_text]

        assert matched, (
            f"{case_name}: none of the expected fix substrings found in response. "
            f"Expected one of: {fix_substrings!r}"
        )

    def test_affected_resources_mentioned(self, case_rca):
        """At least one affected resource name must appear in the response."""
        report, expect, case_name = case_rca
        affected_resources = expect.get("affected_resources", [])
        if not affected_resources:
            pytest.skip(f"{case_name}: no affected_resources in expect.json")

        full_text = _full_text(report)
        # Extract the bare name (last segment after /) for a loose match
        resource_names = [r.split("/")[-1].lower() for r in affected_resources]
        matched = [name for name in resource_names if name in full_text]

        assert matched, (
            f"{case_name}: none of {resource_names!r} found in response"
        )

    def test_confidence_not_worse_than_expected(self, case_rca):
        """
        The pre-LLM confidence label must be no more than one rank below expected.
        We use pre-LLM because LLM output parsing is fragile; the pipeline score
        is what the offline test suite already validates.
        """
        report, expect, case_name = case_rca
        expected_label = expect["confidence"]
        actual_label   = (report.context.pre_llm_confidence.label
                          if report.context and report.context.pre_llm_confidence
                          else "LOW")

        assert _RANK.get(actual_label, 0) >= _RANK.get(expected_label, 0) - 1, (
            f"{case_name}: pre_llm label={actual_label!r} too far below "
            f"expected={expected_label!r}"
        )

    def test_remediation_non_empty(self, case_rca):
        """The LLM must suggest at least one remediation command."""
        report, _, case_name = case_rca
        assert report.remediation, (
            f"{case_name}: remediation list is empty — LLM did not produce fix commands"
        )

    def test_fallback_behavior(self, case_rca):
        """
        If expect.json sets fallback_expected=true the report must have been
        enriched by the RemediationEngine (remediation list non-empty).
        If fallback_expected=false we do not assert the absence of fallback
        because a LOW-confidence response legitimately triggers it.
        """
        report, expect, case_name = case_rca
        if expect.get("fallback_expected", False):
            assert report.remediation, (
                f"{case_name}: fallback_expected=true but remediation is empty"
            )
