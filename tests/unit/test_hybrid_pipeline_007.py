"""
Pipeline test — case 007 Prometheus alerts (no Ollama required).

Proves that PrometheusAlert nodes are indexed, alert signals reach the
ContextWindow [CRITICAL] section, and the drift+alert combination drives
MEDIUM confidence.
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

CASE_DIR = Path(__file__).parent.parent.parent / "cases" / "007_prometheus_alert"
QUERY    = "metrics-api is CrashLoopBackOff, Prometheus alert KubePodCrashLooping firing"


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
# Step 2 — FAISS indexes PrometheusAlert entities
# ---------------------------------------------------------------------------

class TestStep2DenseRetrieval:

    def test_index_not_empty(self, store):
        assert store.size > 0

    def test_prometheus_alert_indexed(self, store):
        hits = store.search("KubePodCrashLooping critical alert firing", top_k=10)
        kinds = {h.get("kind", "") for h in hits}
        assert "PrometheusAlert" in kinds, (
            f"PrometheusAlert not found in FAISS index — kinds: {kinds}"
        )


# ---------------------------------------------------------------------------
# Step 5 — ContextWindow surfaces alert signals
# ---------------------------------------------------------------------------

class TestStep5ContextWindow:

    def test_alerts_section_populated(self, ctx):
        assert ctx.alerts, "No alerts in ContextWindow — PrometheusAlert ingestion failed"
        print(f"\n  [alerts] {len(ctx.alerts)} alert(s):")
        for a in ctx.alerts:
            print(f"    {a[:140]}")

    def test_critical_alert_present(self, ctx):
        alerts_text = " ".join(ctx.alerts).lower()
        assert "kubepodcrashlooping" in alerts_text or "crash" in alerts_text, (
            f"KubePodCrashLooping not found in alert context: {ctx.alerts}"
        )

    def test_memory_alert_present(self, ctx):
        alerts_text = " ".join(ctx.alerts).lower()
        assert "memory" in alerts_text, (
            "KubeMemoryUsageHigh alert missing from context"
        )

    def test_drift_section_populated(self, ctx):
        all_text = " ".join(ctx.seeds + ctx.drift + ctx.related).lower()
        assert "512mi" in all_text or "256mi" in all_text, (
            "Memory limit drift (512Mi→256Mi) not visible in context"
        )

    def test_confidence_medium_or_higher(self, ctx):
        conf = ctx.pre_llm_confidence
        assert conf.score >= 0.56, (
            f"Expected confidence >= 0.56, got {conf.score:.3f} ({conf.label})"
        )


# ---------------------------------------------------------------------------
# Step 6 — Keyword recall
# ---------------------------------------------------------------------------

class TestStep6E2ERecall:

    def test_oomkilled_in_context(self, ctx):
        all_text = " ".join(ctx.seeds + ctx.events + ctx.related).lower()
        assert "oomkill" in all_text or "oom" in all_text, (
            "OOMKilled signal lost — memory root cause won't be visible to LLM"
        )

    def test_prompt_has_critical_section(self, ctx):
        block = ctx.to_prompt_block()
        assert "CRITICAL" in block


# ---------------------------------------------------------------------------
# Step 7 — RemediationEngine fires OOMKill rule
# ---------------------------------------------------------------------------

class TestStep7RemediationEngine:

    @pytest.fixture(scope="class")
    def hypotheses(self, graph):
        return RemediationEngine().score(graph)

    def test_hypotheses_non_empty(self, hypotheses):
        assert hypotheses

    def test_oomkill_rule_fires(self, hypotheses):
        rule_ids = [h.rule_id for h in hypotheses]
        assert "oom_kill" in rule_ids, (
            f"oom_kill rule did not fire. Rules: {rule_ids}"
        )


# ---------------------------------------------------------------------------
# Step 8 — LLM prompt dry run
# ---------------------------------------------------------------------------

class TestStep8PromptDryRun:

    @pytest.fixture(scope="class")
    def prompt(self, ctx):
        return _build_prompt(QUERY, ctx, kube_version="dry-run/v1.28")

    def test_prompt_not_empty(self, prompt):
        assert len(prompt) > 500

    def test_prompt_contains_alert_name(self, prompt):
        assert "KubePodCrashLooping" in prompt or "kubepodcrashlooping" in prompt.lower()

    def test_prompt_contains_memory_drift(self, prompt):
        assert "512Mi" in prompt or "256Mi" in prompt


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
