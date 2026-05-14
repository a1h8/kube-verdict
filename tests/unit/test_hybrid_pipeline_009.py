"""
Pipeline test — case 009 Kyverno policy violations (no Ollama required).

Proves that PolicyViolation nodes are indexed, the policy_c confidence
component fires, and all three violations (CPU limit / memory limit /
runAsRoot) are visible in the ContextWindow and LLM prompt.
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

CASE_DIR = Path(__file__).parent.parent.parent / "cases" / "009_kyverno_violation"
QUERY    = "audit-exporter pod rejected by Kyverno, missing CPU/memory limits and runs as root"


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
# Step 2 — FAISS indexes PolicyViolation entities
# ---------------------------------------------------------------------------

class TestStep2DenseRetrieval:

    def test_index_not_empty(self, store):
        assert store.size > 0

    def test_policy_violation_indexed(self, store):
        hits = store.search("Kyverno resource limits runAsRoot policy violation", top_k=10)
        kinds = {h.get("kind", "") for h in hits}
        assert "PolicyViolation" in kinds, (
            f"PolicyViolation not found in FAISS index — kinds: {kinds}"
        )


# ---------------------------------------------------------------------------
# Step 5 — ContextWindow surfaces policy violations
# ---------------------------------------------------------------------------

class TestStep5ContextWindow:

    def test_policy_violations_in_context(self, ctx):
        assert ctx.policy_violations, (
            "No policy_violations in ContextWindow — PolicyViolation ingestion failed"
        )
        print(f"\n  [policy] {len(ctx.policy_violations)} violation(s):")
        for pv in ctx.policy_violations:
            print(f"    {pv[:140]}")

    def test_three_violations_detected(self, ctx):
        assert len(ctx.policy_violations) >= 3, (
            f"Expected 3 violations, got {len(ctx.policy_violations)}"
        )

    def test_cpu_violation_present(self, ctx):
        pv_text = " ".join(ctx.policy_violations).lower()
        assert "cpu" in pv_text, "CPU limit violation missing from context"

    def test_memory_violation_present(self, ctx):
        pv_text = " ".join(ctx.policy_violations).lower()
        assert "memory" in pv_text, "Memory limit violation missing from context"

    def test_runasroot_violation_present(self, ctx):
        pv_text = " ".join(ctx.policy_violations).lower()
        assert "root" in pv_text or "nonroot" in pv_text, (
            "runAsRoot violation missing from context"
        )

    def test_confidence_high(self, ctx):
        conf = ctx.pre_llm_confidence
        assert conf.score >= 0.64, (
            f"Expected confidence >= 0.64, got {conf.score:.3f} ({conf.label})"
        )
        assert conf.label == "HIGH", f"Expected HIGH confidence, got {conf.label}"

    def test_policy_c_nonzero(self, ctx):
        reasons_text = " ".join(ctx.pre_llm_confidence.reasons).lower()
        assert "policy" in reasons_text, (
            "policy_c component missing from confidence breakdown"
        )


# ---------------------------------------------------------------------------
# Step 6 — Keyword recall
# ---------------------------------------------------------------------------

class TestStep6E2ERecall:

    def test_kyverno_in_context(self, ctx):
        all_text = " ".join(
            ctx.seeds + ctx.policy_violations + ctx.events
        ).lower()
        assert "kyverno" in all_text or "require-resource-limits" in all_text, (
            "Kyverno policy name not visible in context"
        )

    def test_prompt_has_policy_section(self, ctx):
        block = ctx.to_prompt_block()
        assert "CRITICAL" in block


# ---------------------------------------------------------------------------
# Step 8 — LLM prompt dry run
# ---------------------------------------------------------------------------

class TestStep8PromptDryRun:

    @pytest.fixture(scope="class")
    def prompt(self, ctx):
        return _build_prompt(QUERY, ctx, kube_version="dry-run/v1.28")

    def test_prompt_not_empty(self, prompt):
        assert len(prompt) > 500

    def test_prompt_contains_resource_limits(self, prompt):
        assert "limit" in prompt.lower() or "cpu" in prompt.lower()

    def test_prompt_contains_kyverno_signal(self, prompt):
        assert "kyverno" in prompt.lower() or "policy" in prompt.lower()


# ---------------------------------------------------------------------------
# Step 9 — Proposals include policy category
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

    def test_proposals_have_unique_labels(self, proposals):
        labels = [p.label for p in proposals]
        assert len(labels) == len(set(labels)), f"Duplicate proposal labels: {labels}"
