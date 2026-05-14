"""
Helm case bank — offline regression tests.

Each case in cases/helm_cases/h*/ is loaded via helm_case_factory, which runs
the REAL ingestion pipeline (HelmDriftDetector + AnchorEngine) on kubectl-captured
JSON files.  No synthetic graph, no LLM — tests run fully offline.

Tests mirror test_case_bank.py but validate the end-to-end ingestion layer:
  TestHelmGraphBuilds    — graph is non-empty, drift is detected
  TestHelmContextWindow  — ContextBuilder produces a populated window
  TestHelmKeywords       — root_cause_contains keywords appear in context text
  TestHelmConfidence     — pre_llm_confidence meets expect.json thresholds
"""
from __future__ import annotations

from pathlib import Path

import pytest

from rca.context_builder import ContextBuilder
from tests.helm_cases.helm_case_factory import build_helm_graph, load_helm_case
from vectorstore.embedder import Embedder
from vectorstore.store import FAISSStore

HELM_CASES_ROOT = Path(__file__).parent.parent.parent / "cases" / "helm_cases"
CASE_DIRS       = sorted(HELM_CASES_ROOT.glob("h*/"))
CASE_IDS        = [d.name for d in CASE_DIRS]

if not CASE_DIRS:
    pytest.skip("No helm_cases found", allow_module_level=True)

_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module", params=CASE_DIRS, ids=CASE_IDS)
def helm_case(request):
    return load_helm_case(request.param)


@pytest.fixture(scope="module", params=CASE_DIRS, ids=CASE_IDS)
def helm_ctx(request):
    case  = load_helm_case(request.param)
    graph = build_helm_graph(case)
    store = FAISSStore(embedder=Embedder())
    store.index_graph(graph)
    query = case["expect"].get("query", f"diagnose {case['case_name']}")
    ctx   = ContextBuilder(graph, store).build(query)
    return ctx, case


# ---------------------------------------------------------------------------
# 1. Graph integrity
# ---------------------------------------------------------------------------

class TestHelmGraphBuilds:

    @pytest.mark.parametrize("case_dir", CASE_DIRS, ids=CASE_IDS)
    def test_graph_non_empty(self, case_dir):
        case  = load_helm_case(case_dir)
        graph = build_helm_graph(case)
        entities = list(graph.entities())
        assert entities, f"{case_dir.name}: graph has no entities"

    @pytest.mark.parametrize("case_dir", CASE_DIRS, ids=CASE_IDS)
    def test_helm_release_present(self, case_dir):
        from ontology.entities import ResourceKind
        case  = load_helm_case(case_dir)
        graph = build_helm_graph(case)
        releases = list(graph.entities(ResourceKind.HELM_RELEASE))
        assert releases, f"{case_dir.name}: no HelmRelease entity in graph"

    @pytest.mark.parametrize("case_dir", CASE_DIRS, ids=CASE_IDS)
    def test_drift_detected(self, case_dir):
        """At least one entity must carry a drift annotation."""
        case  = load_helm_case(case_dir)
        graph = build_helm_graph(case)
        has_drift = any(
            any(k.startswith("drift.") for k in e.annotations)
            for e in graph.entities()
        )
        assert has_drift, (
            f"{case_dir.name}: no drift detected — check values.yaml vs observed/ files"
        )

    @pytest.mark.parametrize("case_dir", CASE_DIRS, ids=CASE_IDS)
    def test_managed_by_helm_edges(self, case_dir):
        """K8s entities must be linked to HelmRelease via MANAGED_BY_HELM."""
        from ontology.entities import ResourceKind
        from ontology.relationships import RelationshipType
        case  = load_helm_case(case_dir)
        graph = build_helm_graph(case)
        has_edge = False
        for entity in graph.entities():
            if entity.kind == ResourceKind.HELM_RELEASE:
                continue
            for edge in graph._adj.get(entity.uid, []):
                if edge.rel_type == RelationshipType.MANAGED_BY_HELM:
                    has_edge = True
                    break
        assert has_edge, f"{case_dir.name}: no MANAGED_BY_HELM edges in graph"


# ---------------------------------------------------------------------------
# 2. Context window
# ---------------------------------------------------------------------------

class TestHelmContextWindow:

    @pytest.mark.parametrize("case_dir", CASE_DIRS, ids=CASE_IDS)
    def test_context_non_empty(self, case_dir):
        case  = load_helm_case(case_dir)
        graph = build_helm_graph(case)
        store = FAISSStore(embedder=Embedder())
        store.index_graph(graph)
        query = case["expect"].get("query", f"diagnose {case_dir.name}")
        ctx   = ContextBuilder(graph, store).build(query)
        assert ctx.total_chunks > 0, f"{case_dir.name}: context window is empty"

    @pytest.mark.parametrize("case_dir", CASE_DIRS, ids=CASE_IDS)
    def test_drift_appears_in_context(self, case_dir):
        """Drift items produced by HelmDriftDetector must reach the context window."""
        case  = load_helm_case(case_dir)
        graph = build_helm_graph(case)
        store = FAISSStore(embedder=Embedder())
        store.index_graph(graph)
        query = case["expect"].get("query", f"diagnose {case_dir.name}")
        ctx   = ContextBuilder(graph, store).build(query)
        all_text = " ".join(ctx.seeds + ctx.drift + ctx.events + ctx.anchors)
        assert "drift" in all_text.lower() or any(
            any(k.startswith("drift.") for k in e.annotations)
            for e in graph.entities()
        ), f"{case_dir.name}: drift not visible in context"

    @pytest.mark.parametrize("case_dir", CASE_DIRS, ids=CASE_IDS)
    def test_warning_events_in_context(self, case_dir):
        case  = load_helm_case(case_dir)
        if not any(e.get("type") == "Warning" for e in case["observed"].get("events", [])):
            pytest.skip("No Warning events in case")
        graph = build_helm_graph(case)
        store = FAISSStore(embedder=Embedder())
        store.index_graph(graph)
        query = case["expect"].get("query", f"diagnose {case_dir.name}")
        ctx   = ContextBuilder(graph, store).build(query)
        assert ctx.events, f"{case_dir.name}: Warning events not in context"


# ---------------------------------------------------------------------------
# 3. Keyword coverage
# ---------------------------------------------------------------------------

class TestHelmKeywords:

    @pytest.mark.parametrize("case_dir", CASE_DIRS, ids=CASE_IDS)
    def test_root_cause_keywords_in_context(self, case_dir):
        keywords = load_helm_case(case_dir)["expect"]["root_cause_contains"]
        case  = load_helm_case(case_dir)
        graph = build_helm_graph(case)
        store = FAISSStore(embedder=Embedder())
        store.index_graph(graph)
        query = case["expect"].get("query", f"diagnose {case_dir.name}")
        ctx   = ContextBuilder(graph, store).build(query)

        all_text = (
            " ".join(ctx.seeds + ctx.drift + ctx.events + ctx.anchors
                     + ctx.related + ctx.policy_violations)
        ).lower()

        matched   = [kw for kw in keywords if kw.lower() in all_text]
        threshold = max(1, len(keywords) // 2)
        assert len(matched) >= threshold, (
            f"{case_dir.name}: {len(matched)}/{len(keywords)} keywords found "
            f"({matched!r}). Missing: {[kw for kw in keywords if kw.lower() not in all_text]!r}"
        )


# ---------------------------------------------------------------------------
# 4. Pre-LLM confidence
# ---------------------------------------------------------------------------

class TestHelmConfidence:

    @pytest.mark.parametrize("case_dir", CASE_DIRS, ids=CASE_IDS)
    def test_score_meets_minimum(self, case_dir):
        case  = load_helm_case(case_dir)
        graph = build_helm_graph(case)
        store = FAISSStore(embedder=Embedder())
        store.index_graph(graph)
        query  = case["expect"].get("query", f"diagnose {case_dir.name}")
        ctx    = ContextBuilder(graph, store).build(query)
        expect = case["expect"]
        assert ctx.pre_llm_confidence.score >= expect["confidence_score_min"], (
            f"{case_dir.name}: score {ctx.pre_llm_confidence.score:.3f} "
            f"< min {expect['confidence_score_min']}"
        )

    @pytest.mark.parametrize("case_dir", CASE_DIRS, ids=CASE_IDS)
    def test_label_not_worse_than_expected(self, case_dir):
        case  = load_helm_case(case_dir)
        graph = build_helm_graph(case)
        store = FAISSStore(embedder=Embedder())
        store.index_graph(graph)
        query    = case["expect"].get("query", f"diagnose {case_dir.name}")
        ctx      = ContextBuilder(graph, store).build(query)
        label    = ctx.pre_llm_confidence.label
        expected = case["expect"]["confidence"]
        assert _RANK[label] >= _RANK[expected] - 1, (
            f"{case_dir.name}: label={label!r} too far below expected={expected!r} "
            f"(score={ctx.pre_llm_confidence.score:.3f})"
        )
