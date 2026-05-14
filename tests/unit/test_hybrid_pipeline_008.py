"""
Pipeline test — case 008 OTel traces (no Ollama required).

Proves that OtelTrace nodes are indexed, error span signals reach the
ContextWindow [TRACES] section, and the DB timeout root cause is visible
to the LLM prompt.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from rca.analyzer import _build_prompt
from rca.context_builder import ContextBuilder
from rca.remediation_engine import RemediationEngine
from tests.cases.graph_factory import build_graph, load_case
from tests.integration.use_cases.proposal_engine import generate_proposals
from vectorstore.embedder import Embedder
from vectorstore.store import FAISSStore

CASE_DIR = Path(__file__).parent.parent.parent / "cases" / "008_otel_trace"
QUERY    = "order-processor requests return HTTP 500, OTel traces show database timeout"


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
    return ContextBuilder(graph=graph, store=store).build(QUERY)


# ---------------------------------------------------------------------------
# Step 2 — FAISS indexes OtelTrace entities
# ---------------------------------------------------------------------------

class TestStep2DenseRetrieval:

    def test_index_not_empty(self, store):
        assert store.size > 0

    def test_otel_trace_indexed(self, store):
        hits = store.search("database timeout connection refused 5432", top_k=10)
        kinds = {h.get("kind", "") for h in hits}
        assert "OtelTrace" in kinds, (
            f"OtelTrace not found in FAISS index — kinds: {kinds}"
        )


# ---------------------------------------------------------------------------
# Step 5 — ContextWindow surfaces trace signals
# ---------------------------------------------------------------------------

class TestStep5ContextWindow:

    def test_traces_section_populated(self, ctx):
        assert ctx.traces, "No traces in ContextWindow — OtelTrace ingestion failed"
        print(f"\n  [traces] {len(ctx.traces)} trace(s):")
        for t in ctx.traces:
            print(f"    {t[:140]}")

    def test_db_error_in_traces(self, ctx):
        traces_text = " ".join(ctx.traces).lower()
        assert "connection" in traces_text or "timeout" in traces_text or "5432" in traces_text, (
            f"DB connection error not in traces: {ctx.traces}"
        )

    def test_order_processor_in_seeds(self, ctx):
        seeds_text = " ".join(ctx.seeds).lower()
        assert "order" in seeds_text or "processor" in seeds_text, (
            f"order-processor not in seeds: {ctx.seeds}"
        )

    def test_confidence_medium_or_higher(self, ctx):
        conf = ctx.pre_llm_confidence
        assert conf.score >= 0.43, (
            f"Expected confidence >= 0.43, got {conf.score:.3f} ({conf.label})"
        )


# ---------------------------------------------------------------------------
# Step 6 — Keyword recall
# ---------------------------------------------------------------------------

class TestStep6E2ERecall:

    def test_db_host_in_context(self, ctx):
        all_text = " ".join(
            ctx.seeds + ctx.traces + ctx.events + ctx.related
        ).lower()
        assert "orders-db" in all_text or "5432" in all_text, (
            "DB hostname / port not visible in context — LLM won't find root cause"
        )

    def test_prompt_has_traces_section(self, ctx):
        block = ctx.to_prompt_block()
        assert "TRACES" in block or "trace" in block.lower()


# ---------------------------------------------------------------------------
# Step 8 — LLM prompt dry run
# ---------------------------------------------------------------------------

class TestStep8PromptDryRun:

    @pytest.fixture(scope="class")
    def prompt(self, ctx):
        return _build_prompt(QUERY, ctx, kube_version="dry-run/v1.28")

    def test_prompt_not_empty(self, prompt):
        assert len(prompt) > 500

    def test_prompt_contains_db_signal(self, prompt):
        assert "orders-db" in prompt or "5432" in prompt or "connection" in prompt.lower()


# ---------------------------------------------------------------------------
# Step 9 — Proposals
# ---------------------------------------------------------------------------

class TestStep9Proposals:

    @pytest.fixture(scope="class")
    def proposals(self, graph, ctx):
        from rca.analyzer import RCAReport
        hyps = RemediationEngine().score(graph)
        top = hyps[0] if hyps else None
        raw = (
            f"### 3. Root cause\n{top.explanation}\n### 5. Remediation\n"
            + "\n".join(f"- {c}" for c in (top.commands or []))
        ) if top else ""
        report = RCAReport(query=QUERY, kube_version="dry-run/v1.28", context=ctx, raw_analysis=raw)
        return generate_proposals(report, max_n=4)

    def test_proposals_generated(self, proposals):
        assert proposals

    def test_proposals_have_labels(self, proposals):
        for p in proposals:
            assert p.label, f"Proposal missing label: {p}"
