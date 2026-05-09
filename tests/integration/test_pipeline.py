"""
Integration test — full RCA pipeline without a real cluster or Ollama.

Uses the synthetic_graph fixture + an in-memory FAISS index +
a mock OllamaClient that returns a canned structured response.
"""
from unittest.mock import MagicMock

import pytest

from rca.analyzer import RCAAnalyzer, RCAReport
from rca.context_builder import ContextBuilder
from vectorstore.embedder import Embedder
from vectorstore.store import FAISSStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_CANNED_RESPONSE = """\
### 1. Summary
Pod api-xyz is in CrashLoopBackOff due to a missing PersistentVolume for api-data.

### 2. Affected resources
- Pod/production/api-xyz — CrashLoopBackOff, restarts=15
- Deployment/production/api — readyReplicas=1 (expected 3)
- PersistentVolumeClaim/production/api-data — Pending

### 3. Root cause
The PVC api-data cannot be bound because no PersistentVolume matches the storage
class 'standard' with 10Gi capacity. Pods depending on this volume fail to start.

### 4. Causal chain
1. HelmRelease api deployed with persistence.enabled=true and size=10Gi.
2. PVC api-data created but no matching PV available — status=Pending.
3. Pod api-xyz cannot mount the volume — stays in ContainerCreating then CrashLoopBackOff.
4. Deployment api reports readyReplicas=1 instead of 3.

### 5. Remediation
kubectl get pv -o wide
kubectl describe pvc api-data -n production
helm upgrade api ./chart --set persistence.storageClass=local-path -n production

### 6. Confidence
HIGH — drift between declared and observed PVC state is confirmed, events corroborate.
"""


@pytest.fixture
def faiss_store(synthetic_graph) -> FAISSStore:
    embedder = Embedder()
    store = FAISSStore(embedder=embedder)
    store.index_graph(synthetic_graph)
    return store


@pytest.fixture
def mock_llm() -> MagicMock:
    llm = MagicMock()
    llm.is_available.return_value = True
    llm.model_is_pulled.return_value = True
    llm.model = "mistral"
    llm.generate.return_value = _CANNED_RESPONSE
    return llm


@pytest.fixture
def analyzer(synthetic_graph, faiss_store, mock_llm) -> RCAAnalyzer:
    return RCAAnalyzer(graph=synthetic_graph, store=faiss_store, llm=mock_llm)


# ---------------------------------------------------------------------------
# ContextBuilder
# ---------------------------------------------------------------------------

class TestContextBuilder:
    def test_seeds_present(self, synthetic_graph, faiss_store):
        ctx = ContextBuilder(synthetic_graph, faiss_store).build("pod crashlooping")
        assert len(ctx.seeds) >= 2
        seed_texts = " ".join(ctx.seeds)
        assert "api-xyz" in seed_texts or "Failed" in seed_texts

    def test_drift_present(self, synthetic_graph, faiss_store):
        ctx = ContextBuilder(synthetic_graph, faiss_store).build("replica drift")
        assert len(ctx.drift) >= 1
        assert any("declared" in d for d in ctx.drift)

    def test_warning_events_present(self, synthetic_graph, faiss_store):
        ctx = ContextBuilder(synthetic_graph, faiss_store).build("pod events")
        assert len(ctx.events) >= 2
        event_text = " ".join(ctx.events)
        assert "Warning" in event_text

    def test_events_sorted_by_count_desc(self, synthetic_graph, faiss_store):
        ctx = ContextBuilder(synthetic_graph, faiss_store).build("events")
        # ev-crashloop has count=42, ev-pvc has count=8 → crashloop should be first
        assert "BackOff" in ctx.events[0] or "crashloop" in ctx.events[0].lower() or "42" in ctx.events[0]

    def test_helm_section_present(self, synthetic_graph, faiss_store):
        ctx = ContextBuilder(synthetic_graph, faiss_store).build("helm release")
        assert len(ctx.helm) >= 1
        helm_text = " ".join(ctx.helm)
        assert "api" in helm_text

    def test_no_seed_uids_in_related(self, synthetic_graph, faiss_store):
        ctx = ContextBuilder(synthetic_graph, faiss_store).build("pod crashloop")
        seed_texts = set(ctx.seeds)
        for r in ctx.related:
            assert r not in seed_texts

    def test_prompt_block_sections(self, synthetic_graph, faiss_store):
        ctx = ContextBuilder(synthetic_graph, faiss_store).build("crashloop")
        block = ctx.to_prompt_block()
        assert "CRITICAL" in block
        assert "WARNING" in block
        assert "Helm" in block

    def test_total_chunks(self, synthetic_graph, faiss_store):
        ctx = ContextBuilder(synthetic_graph, faiss_store).build("test")
        assert ctx.total_chunks == (len(ctx.seeds) + len(ctx.drift) +
                                     len(ctx.events) + len(ctx.helm) + len(ctx.related))


# ---------------------------------------------------------------------------
# RCAAnalyzer
# ---------------------------------------------------------------------------

class TestRCAAnalyzer:
    def test_returns_rca_report(self, analyzer):
        report = analyzer.analyze("pods are crashlooping")
        assert isinstance(report, RCAReport)

    def test_llm_called_once(self, analyzer, mock_llm):
        analyzer.analyze("pods are crashlooping")
        mock_llm.generate.assert_called_once()

    def test_llm_prompt_contains_critical(self, analyzer, mock_llm):
        analyzer.analyze("pods are crashlooping")
        prompt_arg = mock_llm.generate.call_args[0][0]
        assert "CRITICAL" in prompt_arg

    def test_llm_prompt_contains_drift(self, analyzer, mock_llm):
        analyzer.analyze("replica drift")
        prompt_arg = mock_llm.generate.call_args[0][0]
        assert "declared" in prompt_arg

    def test_llm_prompt_contains_query(self, analyzer, mock_llm):
        query = "pod api-xyz crashlooping in production"
        analyzer.analyze(query)
        prompt_arg = mock_llm.generate.call_args[0][0]
        assert query in prompt_arg

    def test_report_kube_version(self, analyzer):
        report = analyzer.analyze("test")
        assert "v1.28" in report.kube_version

    def test_report_context_stats(self, analyzer):
        report = analyzer.analyze("test")
        assert report.context.total_chunks > 0


# ---------------------------------------------------------------------------
# RCAReport parsing
# ---------------------------------------------------------------------------

class TestRCAReportParsing:
    @pytest.fixture
    def report(self, analyzer):
        return analyzer.analyze("pods are crashlooping")

    def test_summary_extracted(self, report):
        assert "CrashLoopBackOff" in report.summary or len(report.summary) > 10

    def test_affected_is_list(self, report):
        assert isinstance(report.affected, list)
        assert len(report.affected) >= 1

    def test_affected_mentions_pod(self, report):
        combined = " ".join(report.affected)
        assert "api-xyz" in combined or "Pod" in combined

    def test_root_cause_non_empty(self, report):
        assert len(report.root_cause) > 20

    def test_causal_chain_is_list(self, report):
        assert isinstance(report.causal_chain, list)
        assert len(report.causal_chain) >= 2

    def test_remediation_contains_kubectl(self, report):
        combined = " ".join(report.remediation)
        assert "kubectl" in combined or "helm" in combined

    def test_confidence_extracted(self, report):
        assert report.confidence.upper().startswith(("LOW", "MEDIUM", "HIGH"))

    def test_to_dict_keys(self, report):
        d = report.to_dict()
        for key in ("query", "kube_version", "summary", "affected",
                     "root_cause", "causal_chain", "remediation",
                     "confidence", "context_stats", "raw_analysis"):
            assert key in d

    def test_to_dict_context_stats(self, report):
        stats = report.to_dict()["context_stats"]
        assert stats["seeds"] >= 2
        assert stats["drift"] >= 1
        assert stats["events"] >= 2


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------

class TestStreamAnalyze:
    def test_yields_tokens_then_report(self, synthetic_graph, faiss_store):
        mock_llm = MagicMock()
        mock_llm.is_available.return_value = True
        mock_llm.model_is_pulled.return_value = True
        mock_llm.model = "mistral"
        mock_llm.stream_generate.return_value = iter(["### 1. Summary\n", "test\n"])

        analyzer = RCAAnalyzer(synthetic_graph, faiss_store, mock_llm)
        items = list(analyzer.stream_analyze("test"))

        str_tokens = [i for i in items if isinstance(i, str)]
        reports = [i for i in items if isinstance(i, RCAReport)]

        assert len(str_tokens) >= 1
        assert len(reports) == 1
        assert isinstance(reports[0], RCAReport)
