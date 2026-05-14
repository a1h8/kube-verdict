"""
Pipeline tracing test — case 001 CrashLoopBackOff (no Ollama required).

Verifies the full hybrid retrieval chain AND dry-run resolution path:

  query
   └─ FAISSStore.hybrid_search
        ├─ FAISS dense (cosine similarity)
        ├─ BM25 sparse (keyword)
        └─ RRF fusion
            └─ ContextBuilder.build
                 ├─ seeds / events / anchors / helm
                 ├─ BFS → Jaccard dedup → TF-IDF rank → related
                 └─ pre_llm_confidence score
                      ├─ [Step 7] RemediationEngine — rule-based hypotheses
                      ├─ [Step 8] _build_prompt — dry-run LLM prompt
                      └─ [Step 9] ProposalEngine — next-step follow-up queries

Run with -s to see the full trace:
    pytest tests/unit/test_hybrid_pipeline_001.py -v -s
"""
from __future__ import annotations

from pathlib import Path

import pytest

from rca.analyzer import RCAReport, _build_prompt
from rca.context_builder import ContextBuilder
from rca.remediation_engine import RemediationEngine
from tests.cases.graph_factory import build_graph, load_case
from tests.integration.use_cases.proposal_engine import generate_proposals
from vectorstore.bm25_retriever import BM25Retriever, _tokenize
from vectorstore.embedder import Embedder
from vectorstore.rrf import rrf_fuse
from vectorstore.store import FAISSStore

CASE_DIR = Path(__file__).parent.parent.parent / "cases" / "001_crashloopbackoff"
QUERY    = "payment-service is in CrashLoopBackOff"


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
    """The tokenizer must split K8s compound tokens AND preserve them."""

    def test_query_tokens_include_crashloopbackoff(self):
        tokens = _tokenize(QUERY)
        assert "crashloopbackoff" in tokens, (
            f"'crashloopbackoff' not found in {tokens}"
        )

    def test_query_tokens_include_payment(self):
        tokens = _tokenize(QUERY)
        assert "payment-service" in tokens or "payment" in tokens

    def test_compound_split_preserved(self):
        # "reason=CrashLoopBackOff" → both compound AND parts indexed
        tokens = _tokenize("reason=CrashLoopBackOff restarts=47")
        assert "reason=crashloopbackoff" in tokens
        assert "crashloopbackoff" in tokens
        assert "restarts=47" in tokens
        assert "restarts" in tokens


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
        print(f"\n  [FAISS dense] top-5 hits:")
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

    def test_bm25_finds_crashloopbackoff(self, store):
        hits = store._bm25.search(QUERY, top_k=5)
        assert hits, "BM25 returned no hits for CrashLoopBackOff query"
        print(f"\n  [BM25 sparse] top-5 hits:")
        for h in hits:
            print(f"    bm25={h['bm25_score']:.4f}  uid={h['uid']}")

    def test_bm25_top_hit_mentions_relevant_token(self, store):
        hits = store._bm25.search(QUERY, top_k=3)
        texts_lower = " ".join(h["text"].lower() for h in hits)
        assert "crashloopbackoff" in texts_lower or "payment" in texts_lower, (
            f"Top-3 BM25 hits don't contain CrashLoopBackOff or payment:\n"
            + "\n".join(f"  {h['text'][:120]}" for h in hits)
        )


# ---------------------------------------------------------------------------
# Step 4 — RRF fusion
# ---------------------------------------------------------------------------

class TestStep4RRFFusion:

    def test_hybrid_search_returns_results(self, store):
        hits = store.hybrid_search(QUERY, top_k=10)
        assert hits, "hybrid_search returned nothing"
        print(f"\n  [RRF fused] top-10 hits:")
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
        """A uid in BOTH dense and sparse lists must score higher than one in only one."""
        shared_uid = "pod-production-payment-service-6d8f9b7c4-xk9p2"
        only_dense = "event-BackOff-only"

        dense  = [{"uid": shared_uid, "text": "x", "doc_source": "cluster"},
                  {"uid": only_dense,  "text": "y", "doc_source": "cluster"}]
        sparse = [{"uid": shared_uid, "text": "x", "doc_source": "cluster"}]

        fused = rrf_fuse([dense, sparse], top_k=2)
        uid_score = {e["uid"]: e["rrf_score"] for e in fused}
        assert uid_score[shared_uid] > uid_score[only_dense], (
            f"Shared uid should score higher: {uid_score}"
        )


# ---------------------------------------------------------------------------
# Step 5 — ContextWindow structure
# ---------------------------------------------------------------------------

class TestStep5ContextWindow:

    def test_seeds_contain_unhealthy_pod(self, ctx):
        assert ctx.seeds, "No unhealthy seeds found — pod should be in seeds"
        seeds_text = " ".join(ctx.seeds).lower()
        print(f"\n  [seeds] {len(ctx.seeds)} item(s):")
        for s in ctx.seeds:
            print(f"    {s[:140]}")
        assert "payment" in seeds_text or "crashloopbackoff" in seeds_text, (
            f"Seeds don't mention payment/CrashLoopBackOff:\n{ctx.seeds}"
        )

    def test_events_surfaced(self, ctx):
        assert ctx.events, "No events in ContextWindow"
        events_text = " ".join(ctx.events).lower()
        print(f"\n  [events] {len(ctx.events)} event(s):")
        for e in ctx.events[:3]:
            print(f"    {e[:140]}")
        assert "backoff" in events_text or "failed" in events_text, (
            "Expected BackOff or Failed in events"
        )

    def test_events_sorted_by_count_desc(self, ctx):
        """The event with count=47 should come before count=1."""
        events_text = " ".join(ctx.events)
        if "count=47" in events_text and "count=1" in events_text:
            idx_47 = next(i for i, e in enumerate(ctx.events) if "count=47" in e)
            idx_1  = next(i for i, e in enumerate(ctx.events) if "count=1" in e)
            assert idx_47 < idx_1, (
                f"Event count=47 should precede count=1 (got {idx_47} vs {idx_1})"
            )

    def test_related_context_non_empty(self, ctx):
        assert ctx.related, "related context is empty after BFS+dedup+TF-IDF"
        print(f"\n  [related] {len(ctx.related)} chunk(s) after dedup+TF-IDF:")
        for r in ctx.related[:3]:
            print(f"    {r[:140]}")

    def test_total_chunks_positive(self, ctx):
        total = ctx.total_chunks
        print(f"\n  [ContextWindow] total_chunks={total}")
        assert total > 0

    def test_pre_llm_confidence_set(self, ctx):
        assert ctx.pre_llm_confidence is not None
        conf = ctx.pre_llm_confidence
        print(
            f"\n  [confidence] score={conf.score:.2f}  label={conf.label}"
        )
        for r in conf.reasons:
            print(f"    {r}")
        assert conf.label in ("LOW", "MEDIUM", "HIGH")

    def test_confidence_at_least_low(self, ctx):
        assert ctx.pre_llm_confidence.score > 0.0, (
            "Confidence score is 0 — pipeline produced no signal"
        )


# ---------------------------------------------------------------------------
# Step 6 — End-to-end keyword recall (was the hybrid retrieval useful?)
# ---------------------------------------------------------------------------

class TestStep6E2ERecall:

    def test_secret_signal_surfaced(self, ctx):
        """
        The 'payment-db-secret not found' event must appear somewhere in the
        context — either in events or related.  BM25 should pick it up even
        if the embedding distance is mediocre for the query wording.
        """
        all_text = (
            " ".join(ctx.seeds)
            + " ".join(ctx.events)
            + " ".join(ctx.related)
        ).lower()
        print(f"\n  [e2e recall] checking 'secret' in combined context...")
        assert "secret" in all_text, (
            "Expected 'secret' to appear in seeds/events/related — "
            "the missing-secret signal was lost"
        )

    def test_context_prompt_block_has_critical_section(self, ctx):
        block = ctx.to_prompt_block()
        assert "CRITICAL" in block, "to_prompt_block() has no CRITICAL section"
        print(f"\n  [prompt_block] first 400 chars:\n{block[:400]}")


# ---------------------------------------------------------------------------
# Step 7 — RemediationEngine: rule-based hypotheses (no LLM)
# ---------------------------------------------------------------------------

class TestStep7RemediationEngine:
    """
    The RemediationEngine scores the graph with deterministic rules and returns
    weighted RemediationHypothesis objects with ready-to-run kubectl commands.
    This fires WITHOUT an LLM — it is the fallback path and also used here as
    a dry-run to expose every possible resolution.
    """

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
            print(f"  Commands :")
            for cmd in h.commands:
                print(f"    $ {cmd}")
        return hyps

    def test_at_least_one_hypothesis(self, hypotheses):
        assert hypotheses, "RemediationEngine produced no hypotheses for case 001"

    def test_missing_config_rule_fires(self, hypotheses):
        """missing_config rule must fire — 'secret not found' event is present."""
        rule_ids = [h.rule_id for h in hypotheses]
        assert "missing_config" in rule_ids, (
            f"missing_config rule did not fire. Fired rules: {rule_ids}"
        )

    def test_crashloop_rule_fires(self, hypotheses):
        """crashloop_db must fire — restart_count=47 with no OOM."""
        rule_ids = [h.rule_id for h in hypotheses]
        assert "crashloop_db" in rule_ids, (
            f"crashloop_db rule did not fire. Fired rules: {rule_ids}"
        )

    def test_missing_config_weight_highest(self, hypotheses):
        """missing_config (w=0.92 base) should be the top-weighted hypothesis."""
        top = hypotheses[0]
        assert top.rule_id == "missing_config", (
            f"Expected missing_config as top hypothesis, got: {top.rule_id} w={top.weight:.2f}"
        )

    def test_missing_config_commands_contain_kubectl(self, hypotheses):
        h = next(h for h in hypotheses if h.rule_id == "missing_config")
        cmds_text = " ".join(h.commands).lower()
        assert "kubectl" in cmds_text
        assert "secret" in cmds_text

    def test_missing_config_commands_reference_namespace(self, hypotheses):
        h = next(h for h in hypotheses if h.rule_id == "missing_config")
        cmds_text = " ".join(h.commands)
        assert "production" in cmds_text, (
            f"Commands don't reference the 'production' namespace: {h.commands}"
        )

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
    """
    Build the exact prompt that would be sent to Ollama — verifying structure,
    content completeness, and that critical signals are visible to the LLM.
    """

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
        """CRITICAL section must be present — unhealthy seeds drive priority."""
        assert "CRITICAL" in prompt

    def test_prompt_contains_crashloopbackoff(self, prompt):
        assert "CrashLoopBackOff" in prompt or "crashloopbackoff" in prompt.lower()

    def test_prompt_contains_secret_signal(self, prompt):
        """The secret-not-found event must be visible to the LLM."""
        assert "secret" in prompt.lower(), (
            "Secret signal missing from prompt — LLM won't see the root cause"
        )

    def test_prompt_contains_anchor_info(self, prompt):
        """Declared env var anchor must appear — helps LLM identify config drift."""
        assert "anchor" in prompt.lower() or "declared" in prompt.lower(), (
            "Anchor/declared values missing from prompt"
        )

    def test_prompt_contains_query(self, prompt):
        assert QUERY in prompt

    def test_prompt_confidence_metadata(self, prompt):
        """Pre-LLM confidence score must be embedded in the prompt header."""
        assert "MEDIUM" in prompt or "HIGH" in prompt or "LOW" in prompt

    def test_prompt_not_empty(self, prompt):
        assert len(prompt) > 500, f"Prompt suspiciously short: {len(prompt)} chars"


# ---------------------------------------------------------------------------
# Step 9 — ProposalEngine: next-step follow-up queries
# ---------------------------------------------------------------------------

class TestStep9Proposals:
    """
    Given a dry-run RCAReport (built from RemediationEngine output, no LLM),
    ProposalEngine generates ranked follow-up queries for the operator.
    These are the 'next steps' surfaced in the dialogue tree.
    """

    @pytest.fixture(scope="class")
    def dry_run_report(self, graph, ctx):
        """
        Simulate a LOW-confidence RCAReport without calling Ollama:
        use the top RemediationEngine hypothesis as the 'LLM' answer.
        """
        engine = RemediationEngine()
        hypotheses = engine.score(graph)
        top = hypotheses[0] if hypotheses else None

        raw = ""
        if top:
            raw = (
                f"### 1. Summary\n{top.symptom} on {top.affected}\n\n"
                f"### 2. Affected resources\n- {top.affected}\n\n"
                f"### 3. Root cause\n{top.explanation}\n\n"
                f"### 4. Causal chain\n"
                + "\n".join(f"{i+1}. {ev}" for i, ev in enumerate(top.evidence or ["restarts=47"]))
                + "\n\n### 5. Remediation\n"
                + "\n".join(f"- {cmd}" for cmd in top.commands)
                + "\n\n### 6. Confidence\nLOW — rule-assisted\n"
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

    def test_config_proposal_present(self, proposals):
        """
        'config' proposal must appear — events mention 'secret not found',
        so ProposalEngine should propose a Secret/ConfigMap key check.
        """
        categories = [p.category for p in proposals]
        assert "config" in categories, (
            f"Expected 'config' proposal (secret check). Got categories: {categories}"
        )

    def test_generic_logs_proposal_present(self, proposals):
        """Container logs proposal must fire — CrashLoop/restart signals."""
        all_desc = " ".join(p.description + " " + p.follow_up_query for p in proposals).lower()
        assert "log" in all_desc or "restart" in all_desc, (
            f"No logs/restart proposal found.\nProposals: {[(p.category, p.description) for p in proposals]}"
        )

    def test_proposals_have_follow_up_queries(self, proposals):
        for p in proposals:
            assert p.follow_up_query.strip(), f"Proposal {p.label} has empty follow_up_query"

    def test_proposals_have_unique_labels(self, proposals):
        labels = [p.label for p in proposals]
        assert len(labels) == len(set(labels)), f"Duplicate proposal labels: {labels}"

    def test_proposal_categories_diverse(self, proposals):
        """Proposals must not all be the same category."""
        categories = [p.category for p in proposals]
        assert len(set(categories)) > 1, (
            f"All proposals have the same category: {categories}"
        )
