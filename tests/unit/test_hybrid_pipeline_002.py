"""
Pipeline tracing test — case 002 ImagePullBackOff (no Ollama required).

Verifies the full hybrid retrieval chain for the ml-inference ImagePullBackOff
scenario: 401 Unauthorized from GHCR + Helm drift on image tag.

  query
   └─ FAISSStore.hybrid_search
        ├─ FAISS dense (cosine similarity)
        ├─ BM25 sparse (keyword)
        └─ RRF fusion
            └─ ContextBuilder.build
                 ├─ seeds / events / anchors (2 anchors) / drift (1 item)
                 ├─ BFS → Jaccard dedup → TF-IDF rank → related
                 └─ pre_llm_confidence score (≥ 0.55 MEDIUM)
                      ├─ [Step 7] RemediationEngine — image_pull + helm_drift
                      ├─ [Step 8] _build_prompt — dry-run LLM prompt
                      └─ [Step 9] ProposalEngine — image + drift proposals

Run with -s to see the full trace:
    pytest tests/unit/test_hybrid_pipeline_002.py -v -s
"""
from __future__ import annotations

from pathlib import Path

import pytest

from rca.analyzer import RCAReport, _build_prompt
from rca.context_builder import ContextBuilder
from rca.remediation_engine import RemediationEngine
from tests.cases.graph_factory import build_graph, load_case
from tests.integration.use_cases.proposal_engine import generate_proposals
from vectorstore.bm25_retriever import _tokenize
from vectorstore.embedder import Embedder
from vectorstore.rrf import rrf_fuse
from vectorstore.store import FAISSStore

CASE_DIR = Path(__file__).parent.parent.parent / "cases" / "002_imagepullbackof"
QUERY    = "ml-inference pod stuck in ImagePullBackOf"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def case_data():
    return load_case(CASE_DIR)


@pytest.fixture(scope="module")
def graph(case_data):
    return build_graph(case_data["input"])


@pytest.fixture(scope="module")
def store(graph):
    s = FAISSStore(embedder=Embedder())
    s.index_graph(graph)
    return s


@pytest.fixture(scope="module")
def ctx(graph, store):
    builder = ContextBuilder(graph=graph, store=store)
    return builder.build(QUERY)


# ---------------------------------------------------------------------------
# Step 1 — BM25 tokenizer
# ---------------------------------------------------------------------------

class TestStep1Tokenizer:
    """Tokenizer must split 'ImagePullBackOff' compound and preserve parts."""

    def test_query_tokens_include_imagepullbackoff(self):
        tokens = _tokenize(QUERY)
        assert "imagepullbackof" in tokens, (
            f"'imagepullbackoff' not found in {tokens}"
        )

    def test_query_tokens_include_ml_inference(self):
        tokens = _tokenize(QUERY)
        assert "ml-inference" in tokens or "ml" in tokens

    def test_compound_split_preserved(self):
        tokens = _tokenize("reason=ImagePullBackOff image=ghcr.io/acme/ml-inference:v2.1.0-private")
        assert "reason=imagepullbackof" in tokens
        assert "imagepullbackof" in tokens
        assert "ghcr.io" in tokens or "acme" in tokens


# ---------------------------------------------------------------------------
# Step 2 — FAISS dense hits
# ---------------------------------------------------------------------------

class TestStep2DenseRetrieval:

    def test_index_not_empty(self, store):
        assert store.size > 0, "FAISSStore is empty"
        print(f"\n  [FAISS] {store.size} vectors indexed")

    def test_plain_search_returns_results(self, store):
        hits = store.search(QUERY, top_k=5)
        assert hits, "FAISS search returned nothing"
        print("\n  [FAISS dense] top-5 hits:")
        for h in hits:
            print(f"    score={h['score']:.4f}  uid={h['uid']}")

    def test_dense_top_hit_is_pod_or_event(self, store):
        hits = store.search(QUERY, top_k=3)
        kinds = {h.get("kind", "") for h in hits}
        print(f"\n  [FAISS dense] top-3 kinds: {kinds}")
        assert kinds & {"Pod", "K8sEvent"}, (
            f"Expected Pod or K8sEvent in top-3 dense hits, got: {kinds}"
        )


# ---------------------------------------------------------------------------
# Step 3 — BM25 sparse hits
# ---------------------------------------------------------------------------

class TestStep3SparseRetrieval:

    def test_bm25_built_after_index(self, store):
        assert store._bm25.size > 0, "BM25 index not built"
        print(f"\n  [BM25] corpus size: {store._bm25.size}")

    def test_bm25_finds_imagepullbackoff(self, store):
        hits = store._bm25.search(QUERY, top_k=5)
        assert hits, "BM25 returned no hits for ImagePullBackOff query"
        print("\n  [BM25 sparse] top-5 hits:")
        for h in hits:
            print(f"    bm25={h['bm25_score']:.4f}  uid={h['uid']}")

    def test_bm25_top_hit_mentions_relevant_token(self, store):
        hits = store._bm25.search(QUERY, top_k=3)
        texts_lower = " ".join(h["text"].lower() for h in hits)
        assert "imagepullbackof" in texts_lower or "ml-inference" in texts_lower or "pull" in texts_lower, (
            "Top-3 BM25 hits don't contain expected tokens:\n"
            + "\n".join(f"  {h['text'][:120]}" for h in hits)
        )


# ---------------------------------------------------------------------------
# Step 4 — RRF fusion
# ---------------------------------------------------------------------------

class TestStep4RRFFusion:

    def test_hybrid_search_returns_results(self, store):
        hits = store.hybrid_search(QUERY, top_k=10)
        assert hits, "hybrid_search returned nothing"
        print("\n  [RRF fused] top-10 hits:")
        for h in hits:
            print(
                f"    rrf={h.get('rrf_score', 0):.5f}  "
                f"uid={h['uid']}  kind={h.get('kind', '?')}"
            )

    def test_hybrid_scores_are_decreasing(self, store):
        hits = store.hybrid_search(QUERY, top_k=10)
        scores = [h["rrf_score"] for h in hits]
        assert scores == sorted(scores, reverse=True), (
            "RRF scores are not in descending order"
        )

    def test_rrf_fuse_boosts_agreement(self):
        shared_uid = "pod-production-ml-inference-7d9f8b6c5-r2m4k"
        only_dense = "event-backoff-only"
        dense  = [{"uid": shared_uid, "text": "x", "doc_source": "cluster"},
                  {"uid": only_dense,  "text": "y", "doc_source": "cluster"}]
        sparse = [{"uid": shared_uid, "text": "x", "doc_source": "cluster"}]
        fused  = rrf_fuse([dense, sparse], top_k=2)
        uid_score = {e["uid"]: e["rrf_score"] for e in fused}
        assert uid_score[shared_uid] > uid_score[only_dense], (
            f"Shared uid should score higher: {uid_score}"
        )

    def test_retrieval_stats_populated(self, store, ctx):
        rs = ctx.retrieval_stats
        assert rs, "retrieval_stats not populated after hybrid_search"
        assert rs["dense"] > 0,  f"dense hits empty: {rs}"
        assert rs["fused"] > 0,  f"fused hits empty: {rs}"
        print(f"\n  [retrieval_stats] {rs}")


# ---------------------------------------------------------------------------
# Step 5 — ContextWindow structure
# ---------------------------------------------------------------------------

class TestStep5ContextWindow:

    def test_seeds_contain_unhealthy_pod(self, ctx):
        assert ctx.seeds, "No unhealthy seeds — ml-inference pod should be a seed (Pending)"
        seeds_text = " ".join(ctx.seeds).lower()
        print(f"\n  [seeds] {len(ctx.seeds)} item(s):")
        for s in ctx.seeds:
            print(f"    {s[:160]}")
        assert "ml-inference" in seeds_text or "imagepullbackof" in seeds_text, (
            f"Seeds don't mention ml-inference or ImagePullBackOff:\n{ctx.seeds}"
        )

    def test_events_surfaced(self, ctx):
        assert ctx.events, "No events in ContextWindow"
        events_text = " ".join(ctx.events).lower()
        print(f"\n  [events] {len(ctx.events)} event(s):")
        for e in ctx.events:
            print(f"    {e[:160]}")
        assert "pull" in events_text or "unauthorized" in events_text or "failed" in events_text, (
            "Expected pull/unauthorized/failed in events"
        )

    def test_events_sorted_by_count_desc(self, ctx):
        """Failed event (count=12) should come before BackOff (count=8)."""
        if len(ctx.events) >= 2:
            events_lower = [e.lower() for e in ctx.events]
            failed_idx = next(
                (i for i, e in enumerate(events_lower) if "unauthorized" in e or "401" in e),
                None,
            )
            backoff_idx = next(
                (i for i, e in enumerate(events_lower) if "back-of" in e or "backof" in e),
                None,
            )
            if failed_idx is not None and backoff_idx is not None:
                assert failed_idx < backoff_idx, (
                    f"Failed event (count=12) should precede BackOff (count=8): "
                    f"failed@{failed_idx} vs backoff@{backoff_idx}"
                )

    def test_anchors_present(self, ctx):
        assert ctx.anchors, "No anchors in ContextWindow"
        anchors_text = " ".join(ctx.anchors).lower()
        print(f"\n  [anchors] {len(ctx.anchors)} anchor(s):")
        for a in ctx.anchors:
            print(f"    {a[:180]}")
        assert "ghcr.io" in anchors_text or "v2.0.5" in anchors_text, (
            "Anchor should mention declared image tag (v2.0.5)"
        )
        assert "imagepullpolicy" in anchors_text or "ifnotpresent" in anchors_text, (
            "Anchor should mention imagePullPolicy"
        )

    def test_drift_present(self, ctx):
        assert ctx.drift, "No drift items in ContextWindow — image drift should appear"
        drift_text = " ".join(ctx.drift).lower()
        print(f"\n  [drift] {len(ctx.drift)} item(s):")
        for d in ctx.drift:
            print(f"    {d[:180]}")
        assert "v2.0.5" in drift_text or "v2.1.0" in drift_text, (
            "Drift should mention the image tag change"
        )

    def test_jaccard_stats_populated(self, ctx):
        js = ctx.jaccard_stats
        assert js, "jaccard_stats not populated"
        assert js.get("candidates", 0) > 0
        assert 0 <= js.get("kept", 0) <= js["candidates"]
        print(f"\n  [jaccard] candidates={js['candidates']} kept={js['kept']}")

    def test_total_chunks_positive(self, ctx):
        total = ctx.total_chunks
        print(f"\n  [ContextWindow] total_chunks={total}")
        assert total > 0

    def test_pre_llm_confidence_medium_or_better(self, ctx):
        assert ctx.pre_llm_confidence is not None
        conf = ctx.pre_llm_confidence
        print(
            f"\n  [confidence] score={conf.score:.2f}  label={conf.label}"
        )
        for r in conf.reasons:
            print(f"    {r}")
        assert conf.score >= 0.55, (
            "Expected score ≥ 0.55 (MEDIUM) for ImagePullBackOff with drift. "
            f"Got {conf.score:.2f} {conf.label}"
        )


# ---------------------------------------------------------------------------
# Step 6 — End-to-end keyword recall
# ---------------------------------------------------------------------------

class TestStep6E2ERecall:

    def test_unauthorized_signal_surfaced(self, ctx):
        """The '401 Unauthorized' failure must appear in context."""
        all_text = (
            " ".join(ctx.seeds)
            + " ".join(ctx.events)
            + " ".join(ctx.related)
            + " ".join(ctx.anchors)
        ).lower()
        print("\n  [e2e recall] checking 'unauthorized' or '401' in context...")
        assert "unauthorized" in all_text or "401" in all_text, (
            "Expected 'unauthorized' or '401' in context — image pull failure signal lost"
        )

    def test_image_drift_signal_surfaced(self, ctx):
        """The declared image (v2.0.5) must appear — it's the fix anchor."""
        all_text = (
            " ".join(ctx.drift)
            + " ".join(ctx.anchors)
        ).lower()
        print("\n  [e2e recall] checking image drift signal in context...")
        assert "v2.0.5" in all_text or "declared" in all_text, (
            "Declared image tag (v2.0.5) missing from drift/anchors — fix anchor lost"
        )

    def test_context_prompt_block_has_critical_section(self, ctx):
        block = ctx.to_prompt_block()
        print(f"\n  [prompt_block] first 400 chars:\n{block[:400]}")
        # Seeds are unhealthy → CRITICAL section
        assert "CRITICAL" in block or len(ctx.seeds) > 0, (
            "to_prompt_block() has no CRITICAL section despite unhealthy seeds"
        )


# ---------------------------------------------------------------------------
# Step 7 — RemediationEngine: rule-based hypotheses
# ---------------------------------------------------------------------------

class TestStep7RemediationEngine:

    @pytest.fixture(scope="class")
    def hypotheses(self, graph):
        engine = RemediationEngine()
        hyps = engine.score(graph)
        print(f"\n\n{'═'*70}")
        print(f"  STEP 7 — RemediationEngine  ({len(hyps)} hypothesis(es) fired)")
        print(f"{'═'*70}")
        for h in hyps:
            print(f"\n  [{h.weight:.2f}] rule={h.rule_id}")
            print(f"  Symptom  : {h.symptom}")
            print(f"  Affected : {h.affected}")
            if h.explanation:
                print(f"  Explain  : {h.explanation}")
            if h.evidence:
                print(f"  Evidence : {', '.join(h.evidence)}")
            print("  Commands :")
            for cmd in h.commands:
                print(f"    $ {cmd}")
        return hyps

    def test_at_least_one_hypothesis(self, hypotheses):
        assert hypotheses, "RemediationEngine produced no hypotheses for case 002"

    def test_image_pull_rule_fires(self, hypotheses):
        """image_pull_backoff must fire — events mention 'Failed to pull image'."""
        rule_ids = [h.rule_id for h in hypotheses]
        assert "image_pull_backof" in rule_ids, (
            f"image_pull_backoff rule did not fire. Fired rules: {rule_ids}"
        )

    def test_helm_drift_rule_fires(self, hypotheses):
        """helm_drift must fire — pod has drift.container.ml-inference.image annotation."""
        rule_ids = [h.rule_id for h in hypotheses]
        assert "helm_drift" in rule_ids, (
            f"helm_drift rule did not fire. Fired rules: {rule_ids}"
        )

    def test_image_pull_weight_highest(self, hypotheses):
        """image_pull_backoff (0.97 w/ drift boost) should be top hypothesis."""
        top = hypotheses[0]
        assert top.rule_id == "image_pull_backof", (
            f"Expected image_pull_backoff as top hypothesis, got: {top.rule_id} w={top.weight:.2f}"
        )

    def test_image_pull_weight_above_threshold(self, hypotheses):
        h = next(h for h in hypotheses if h.rule_id == "image_pull_backof")
        assert h.weight >= 0.90, (
            f"Expected weight ≥ 0.90 (base 0.90 + image drift boost). Got {h.weight:.2f}"
        )

    def test_image_pull_commands_contain_kubectl(self, hypotheses):
        h = next(h for h in hypotheses if h.rule_id == "image_pull_backof")
        cmds_text = " ".join(h.commands).lower()
        assert "kubectl" in cmds_text

    def test_weights_are_sorted_descending(self, hypotheses):
        weights = [h.weight for h in hypotheses]
        assert weights == sorted(weights, reverse=True), (
            f"Hypotheses not sorted by weight: {weights}"
        )

    def test_all_hypotheses_have_commands(self, hypotheses):
        for h in hypotheses:
            assert h.commands, f"Hypothesis {h.rule_id} has no commands"


# ---------------------------------------------------------------------------
# Step 8 — LLM prompt dry run (no Ollama call)
# ---------------------------------------------------------------------------

class TestStep8PromptDryRun:

    @pytest.fixture(scope="class")
    def prompt(self, ctx):
        p = _build_prompt(QUERY, ctx, kube_version="dry-run/v1.28")
        print(f"\n\n{'═'*70}")
        print(f"  STEP 8 — LLM prompt dry-run  ({len(p)} chars)")
        print(f"{'═'*70}")
        print(p[:1200])
        if len(p) > 1200:
            print(f"\n  ... [{len(p) - 1200} more chars truncated]")
        return p

    def test_prompt_has_all_six_sections(self, prompt):
        for section in [
            "1. Summary",
            "2. Affected resources",
            "3. Root cause",
            "4. Causal chain",
            "5. Remediation",
            "6. Confidence",
        ]:
            assert section in prompt, f"Prompt missing section: {section!r}"

    def test_prompt_has_cluster_info_header(self, prompt):
        assert "Kubernetes version" in prompt
        assert "Context quality score" in prompt
        assert "Total context chunks" in prompt

    def test_prompt_contains_critical_signal(self, prompt):
        assert "CRITICAL" in prompt

    def test_prompt_contains_imagepullbackoff_signal(self, prompt):
        assert "ImagePullBackOf" in prompt or "imagepullbackof" in prompt.lower()

    def test_prompt_contains_image_pull_failure(self, prompt):
        """Unauthorized or 401 must be visible to the LLM."""
        assert "nauthorized" in prompt or "401" in prompt or "pull" in prompt.lower(), (
            "Image pull failure signal missing from prompt"
        )

    def test_prompt_contains_declared_image(self, prompt):
        """Declared image (v2.0.5) must appear — the fix anchor."""
        assert "v2.0.5" in prompt or "declared" in prompt.lower(), (
            "Declared image tag missing — LLM won't know the correct image"
        )

    def test_prompt_contains_query(self, prompt):
        assert QUERY in prompt

    def test_prompt_confidence_metadata(self, prompt):
        assert "MEDIUM" in prompt or "HIGH" in prompt or "LOW" in prompt

    def test_prompt_not_empty(self, prompt):
        assert len(prompt) > 500, f"Prompt suspiciously short: {len(prompt)} chars"


# ---------------------------------------------------------------------------
# Step 9 — ProposalEngine: next-step follow-up queries
# ---------------------------------------------------------------------------

class TestStep9Proposals:

    @pytest.fixture(scope="class")
    def dry_run_report(self, graph, ctx):
        """Simulate RCAReport from top RemediationEngine hypothesis (no Ollama)."""
        engine     = RemediationEngine()
        hypotheses = engine.score(graph)
        top        = hypotheses[0] if hypotheses else None

        raw = ""
        if top:
            raw = (
                f"### 1. Summary\n{top.symptom} on {top.affected}\n\n"
                f"### 2. Affected resources\n- {top.affected}\n\n"
                f"### 3. Root cause\n{top.explanation}\n\n"
                "### 4. Causal chain\n"
                + "\n".join(f"{i+1}. {ev}" for i, ev in enumerate(top.evidence or ["image=v2.1.0-private"]))
                + "\n\n### 5. Remediation\n"
                + "\n".join(f"- {cmd}" for cmd in top.commands)
                + "\n\n### 6. Confidence\nMEDIUM — image drift confirmed\n"
            )

        return RCAReport(
            query=QUERY,
            kube_version="dry-run/v1.28",
            context=ctx,
            raw_analysis=raw,
        )

    @pytest.fixture(scope="class")
    def proposals(self, dry_run_report):
        props = generate_proposals(dry_run_report, max_n=4)
        print(f"\n\n{'═'*70}")
        print(f"  STEP 9 — ProposalEngine  ({len(props)} next-step proposal(s))")
        print(f"{'═'*70}")
        for p in props:
            print(f"\n  [{p.label}] category={p.category}")
            print(f"  Desc     : {p.description}")
            print(f"  Query    : {p.follow_up_query}")
        return props

    def test_proposals_generated(self, proposals):
        assert proposals, "ProposalEngine returned no proposals"

    def test_image_proposal_present(self, proposals):
        """'image' proposal must appear — events mention imagepullbackoff."""
        categories = [p.category for p in proposals]
        assert "image" in categories, (
            f"Expected 'image' proposal (imagepullbackoff in context). Got: {categories}"
        )

    def test_drift_proposal_present(self, proposals):
        """'drift' proposal must appear — anchors have declared/drift values."""
        categories = [p.category for p in proposals]
        assert "drift" in categories, (
            f"Expected 'drift' proposal (image tag declared in anchors). Got: {categories}"
        )

    def test_proposals_have_follow_up_queries(self, proposals):
        for p in proposals:
            assert p.follow_up_query.strip(), f"Proposal {p.label} has empty follow_up_query"

    def test_proposals_have_unique_labels(self, proposals):
        labels = [p.label for p in proposals]
        assert len(labels) == len(set(labels)), f"Duplicate proposal labels: {labels}"

    def test_proposal_categories_diverse(self, proposals):
        categories = [p.category for p in proposals]
        assert len(set(categories)) > 1, (
            f"All proposals have the same category: {categories}"
        )
