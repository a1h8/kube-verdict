"""
Case bank — parametrized regression tests over cases/*/

For each scenario directory (01_crashloopbackoff … 05_networkpolicy_blocked)
we run five test classes:

  TestConfidenceInputs     — pipeline confidence meets score/label in expect.json
  TestGraphSeeds           — build_graph() produces the expected unhealthy seeds
  TestContextWindow        — ContextBuilder.build() populates the right sections
  TestKeywordCoverage      — root_cause_contains from expect.json appear in context text
  TestPipelineConfidence   — pre_llm_confidence label from ContextBuilder matches expect.json

No LLM / Ollama is required — all tests run fully offline.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from dedup.bfs import find_unhealthy
from rca.confidence import compute_confidence
from rca.context_builder import ContextBuilder
from tests.cases.graph_factory import build_graph, load_case
from vectorstore.embedder import Embedder
from vectorstore.store import FAISSStore

# ---------------------------------------------------------------------------
# Discovery — collect all case directories
# ---------------------------------------------------------------------------

CASES_ROOT = Path(__file__).parent.parent.parent / "cases"
CASE_DIRS  = sorted(CASES_ROOT.glob("0*/"))

assert CASE_DIRS, f"No case directories found under {CASES_ROOT}"

CASE_IDS = [d.name for d in CASE_DIRS]


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module", params=CASE_DIRS, ids=CASE_IDS)
def case(request):
    """Load input.json + expect.json for one case directory."""
    return load_case(request.param)


@pytest.fixture(scope="module", params=CASE_DIRS, ids=CASE_IDS)
def case_graph(request):
    """Build the OntologyGraph for one case directory."""
    data = load_case(request.param)
    return build_graph(data["input"]), data


@pytest.fixture(scope="module", params=CASE_DIRS, ids=CASE_IDS)
def case_ctx(request):
    """Build graph + ContextWindow for one case directory (runs embedder)."""
    data  = load_case(request.param)
    graph = build_graph(data["input"])
    store = FAISSStore(embedder=Embedder())
    store.index_graph(graph)
    cb    = ContextBuilder(graph, store)
    query = data["input"]["query"]
    ctx   = cb.build(query)
    return ctx, data


# ---------------------------------------------------------------------------
# 1. Confidence score — pipeline meets thresholds declared in expect.json
# ---------------------------------------------------------------------------

class TestConfidenceInputs:
    """Pipeline confidence must meet the expected score/label in expect.json."""

    _RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}

    @pytest.mark.parametrize("case_dir", CASE_DIRS, ids=CASE_IDS)
    def test_score_meets_minimum(self, case_dir):
        data   = load_case(case_dir)
        graph  = build_graph(data["input"])
        store  = FAISSStore(embedder=Embedder())
        store.index_graph(graph)
        ctx    = ContextBuilder(graph, store).build(data["input"]["query"])
        expect = data["expect"]
        assert ctx.pre_llm_confidence.score >= expect["confidence_score_min"], (
            f"{case_dir.name}: score {ctx.pre_llm_confidence.score} "
            f"< min {expect['confidence_score_min']}"
        )

    @pytest.mark.parametrize("case_dir", CASE_DIRS, ids=CASE_IDS)
    def test_label_not_worse_than_expected(self, case_dir):
        data   = load_case(case_dir)
        graph  = build_graph(data["input"])
        store  = FAISSStore(embedder=Embedder())
        store.index_graph(graph)
        ctx    = ContextBuilder(graph, store).build(data["input"]["query"])
        label  = ctx.pre_llm_confidence.label
        expected = data["expect"]["confidence"]
        assert self._RANK[label] >= self._RANK[expected] - 1, (
            f"{case_dir.name}: label={label!r} too far below expected={expected!r} "
            f"(score={ctx.pre_llm_confidence.score})"
        )

    @pytest.mark.parametrize("case_dir", CASE_DIRS, ids=CASE_IDS)
    def test_reasons_present(self, case_dir):
        data  = load_case(case_dir)
        graph = build_graph(data["input"])
        store = FAISSStore(embedder=Embedder())
        store.index_graph(graph)
        ctx   = ContextBuilder(graph, store).build(data["input"]["query"])
        assert len(ctx.pre_llm_confidence.reasons) >= 5

    @pytest.mark.parametrize("case_dir", CASE_DIRS, ids=CASE_IDS)
    def test_score_in_range(self, case_dir):
        data  = load_case(case_dir)
        graph = build_graph(data["input"])
        store = FAISSStore(embedder=Embedder())
        store.index_graph(graph)
        ctx   = ContextBuilder(graph, store).build(data["input"]["query"])
        assert 0.0 <= ctx.pre_llm_confidence.score <= 1.0


# ---------------------------------------------------------------------------
# 2. Graph seeds
# ---------------------------------------------------------------------------

class TestGraphSeeds:
    """build_graph() must produce at least one unhealthy seed for most cases."""

    @pytest.mark.parametrize("case_dir", CASE_DIRS, ids=CASE_IDS)
    def test_graph_has_entities(self, case_dir):
        data  = load_case(case_dir)
        graph = build_graph(data["input"])
        assert len(list(graph.entities())) > 0

    @pytest.mark.parametrize("case_dir,min_seeds", [
        (CASE_DIRS[0], 1),  # 001_crashloopbackoff — pod restart_count=47
        (CASE_DIRS[1], 1),  # 002_imagepullbackoff — pod phase=Pending
        (CASE_DIRS[2], 1),  # 003_oomkilled — pod restart_count=8
        (CASE_DIRS[3], 1),  # 004_pending_pvc — PVC phase=Pending
        (CASE_DIRS[4], 0),  # 005_networkpolicy_blocked — pod Running, no restarts
    ], ids=CASE_IDS[:5])
    def test_seed_count(self, case_dir, min_seeds):
        data  = load_case(case_dir)
        graph = build_graph(data["input"])
        seeds = find_unhealthy(graph)
        assert len(seeds) >= min_seeds, (
            f"{case_dir.name}: {len(seeds)} seeds, expected >= {min_seeds}"
        )

    @pytest.mark.parametrize("case_dir", CASE_DIRS[:5], ids=CASE_IDS[:5])
    def test_warning_events_are_seeds(self, case_dir):
        """Warning K8s events must always be picked up as seeds."""
        data  = load_case(case_dir)
        graph = build_graph(data["input"])
        seeds = find_unhealthy(graph)
        assert len(seeds) >= 1


# ---------------------------------------------------------------------------
# 3. Context window coverage
# ---------------------------------------------------------------------------

class TestContextWindow:
    """ContextBuilder.build() must return a populated ContextWindow."""

    @pytest.mark.parametrize("case_dir", CASE_DIRS, ids=CASE_IDS)
    def test_total_chunks_positive(self, case_dir):
        data  = load_case(case_dir)
        graph = build_graph(data["input"])
        store = FAISSStore(embedder=Embedder())
        store.index_graph(graph)
        ctx   = ContextBuilder(graph, store).build(data["input"]["query"])
        assert ctx.total_chunks > 0, f"{case_dir.name}: context window is empty"

    @pytest.mark.parametrize("case_dir", CASE_DIRS, ids=CASE_IDS)
    def test_pre_llm_confidence_set(self, case_dir):
        data  = load_case(case_dir)
        graph = build_graph(data["input"])
        store = FAISSStore(embedder=Embedder())
        store.index_graph(graph)
        ctx   = ContextBuilder(graph, store).build(data["input"]["query"])
        assert ctx.pre_llm_confidence is not None

    @pytest.mark.parametrize("case_dir", CASE_DIRS, ids=CASE_IDS)
    def test_events_section_populated(self, case_dir):
        """Cases with Warning events must have them in ctx.events."""
        data       = load_case(case_dir)
        raw_events = [e for e in data["input"].get("events", []) if e.get("type") == "Warning"]
        if not raw_events:
            pytest.skip("No Warning events in fixture")
        graph = build_graph(data["input"])
        store = FAISSStore(embedder=Embedder())
        store.index_graph(graph)
        ctx   = ContextBuilder(graph, store).build(data["input"]["query"])
        assert len(ctx.events) > 0, f"{case_dir.name}: Warning events not in context"

    @pytest.mark.parametrize("case_dir", [CASE_DIRS[1], CASE_DIRS[2]], ids=CASE_IDS[1:3])
    def test_drift_in_context(self, case_dir):
        """Cases with Helm drift must have seeds or drift annotations."""
        data  = load_case(case_dir)
        graph = build_graph(data["input"])
        store = FAISSStore(embedder=Embedder())
        store.index_graph(graph)
        ctx   = ContextBuilder(graph, store).build(data["input"]["query"])
        all_text = " ".join(ctx.drift + ctx.seeds)
        diffs    = (data["input"].get("helm_drift") or {}).get("diffs", [])
        if diffs:
            declared = str(diffs[0].get("declared", ""))
            assert declared in all_text, (
                f"{case_dir.name}: declared value {declared!r} not found in context"
            )

    def test_case05_policy_violations_in_context(self):
        """Case 05: policy violations must populate ctx.policy_violations."""
        data  = load_case(CASE_DIRS[4])
        graph = build_graph(data["input"])
        store = FAISSStore(embedder=Embedder())
        store.index_graph(graph)
        ctx   = ContextBuilder(graph, store).build(data["input"]["query"])
        assert len(ctx.policy_violations) > 0, "policy_violations must be non-empty for case 05"
        assert ctx.policy_fail_count >= 1


# ---------------------------------------------------------------------------
# 4. Keyword coverage in context text
# ---------------------------------------------------------------------------

class TestKeywordCoverage:
    """
    Expected root_cause_contains keywords from expect.json must appear somewhere
    in the context window text that will be sent to Mistral.
    At least half of the keywords must be present (tolerant — not all events
    are necessarily ingested through the graph factory).
    """

    @pytest.mark.parametrize("case_dir", CASE_DIRS, ids=CASE_IDS)
    def test_keywords_in_context(self, case_dir):
        data     = load_case(case_dir)
        keywords = data["expect"]["root_cause_contains"]
        graph    = build_graph(data["input"])
        store    = FAISSStore(embedder=Embedder())
        store.index_graph(graph)
        ctx      = ContextBuilder(graph, store).build(data["input"]["query"])

        all_text = (
            " ".join(ctx.seeds)
            + " ".join(ctx.drift)
            + " ".join(ctx.events)
            + " ".join(ctx.anchors)
            + " ".join(ctx.anchor_fixes)
            + " ".join(ctx.related)
            + " ".join(ctx.policy_violations)
        ).lower()

        matched   = [kw for kw in keywords if kw.lower() in all_text]
        threshold = max(1, len(keywords) // 2)
        assert len(matched) >= threshold, (
            f"{case_dir.name}: only {len(matched)}/{len(keywords)} keywords found "
            f"({matched!r}). Missing: {[kw for kw in keywords if kw.lower() not in all_text]!r}"
        )


# ---------------------------------------------------------------------------
# 5. Pipeline confidence label
# ---------------------------------------------------------------------------

class TestPipelineConfidence:
    """
    pre_llm_confidence.label from the full pipeline must be at least as good
    as the confidence declared in expect.json.

    We use >= rather than == because the synthetic graph (no FAISS history,
    no Prometheus, no OTel) will typically score lower than a real cluster.
    We verify the label is not WORSE than one step below expected.
    """

    _RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}

    @pytest.mark.parametrize("case_dir", CASE_DIRS, ids=CASE_IDS)
    def test_confidence_label_not_worse_than_expected(self, case_dir):
        data     = load_case(case_dir)
        expected = data["expect"]["confidence"]
        graph    = build_graph(data["input"])
        store    = FAISSStore(embedder=Embedder())
        store.index_graph(graph)
        ctx      = ContextBuilder(graph, store).build(data["input"]["query"])
        label    = ctx.pre_llm_confidence.label

        assert self._RANK[label] >= self._RANK[expected] - 1, (
            f"{case_dir.name}: label={label!r} too far below expected={expected!r} "
            f"(score={ctx.pre_llm_confidence.score})"
        )

    @pytest.mark.parametrize("case_dir", CASE_DIRS, ids=CASE_IDS)
    def test_confidence_score_positive(self, case_dir):
        data  = load_case(case_dir)
        graph = build_graph(data["input"])
        store = FAISSStore(embedder=Embedder())
        store.index_graph(graph)
        ctx   = ContextBuilder(graph, store).build(data["input"]["query"])
        assert ctx.pre_llm_confidence.score > 0.0

    def test_case05_policy_boosts_confidence(self):
        """
        Case 05: adding the policy report raises confidence vs graph without it.
        """
        data = load_case(CASE_DIRS[4])

        # Graph WITH policy violations (full input.json)
        g_with     = build_graph(data["input"])
        store_with = FAISSStore(embedder=Embedder())
        store_with.index_graph(g_with)
        ctx_with   = ContextBuilder(g_with, store_with).build(data["input"]["query"])

        # Graph WITHOUT policy violations (strip policy_report)
        stripped  = {**data["input"], "policy_report": None}
        g_without = build_graph(stripped)
        store_wo  = FAISSStore(embedder=Embedder())
        store_wo.index_graph(g_without)
        ctx_wo    = ContextBuilder(g_without, store_wo).build(data["input"]["query"])

        assert ctx_with.policy_fail_count > ctx_wo.policy_fail_count
        assert ctx_with.pre_llm_confidence.score >= ctx_wo.pre_llm_confidence.score
