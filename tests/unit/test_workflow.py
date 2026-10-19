"""
Unit tests for the LangGraph RCA workflow.

Graph and store are injected via config["configurable"] — never in state —
so LangGraph's MemorySaver checkpointer never tries to msgpack-serialise them.
"""
import json
from unittest.mock import MagicMock

import pytest
from langgraph.types import Command

from vectorstore.store import FAISSStore
from tests.conftest import FakeEmbedder
from workflow.graph import build_graph
from workflow.nodes import (
    anchor_node, archive_path_node, confidence_router, human_router,
    hypothesize_node, select_best_node,
    log_confidence_decision_node, log_human_decision_node,
    MAX_RETRIES, MAX_PATHS,
)
from workflow.state import RCAState


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

_CANNED_HYPOTHESES = """\
H1: Pod api-xyz CrashLoopBackOff — container cannot start due to missing volume mount
H2: PVC api-data Pending — no available PersistentVolume matches the StorageClass
H3: Deployment api degraded — HelmRelease drift caused replica mismatch
"""

_CANNED_HIGH = """\
### 1. Summary
Pod api-xyz is in CrashLoopBackOff.

### 2. Affected resources
- Pod/production/api-xyz — CrashLoopBackOff

### 3. Root cause
Missing PersistentVolume for api-data PVC.

### 4. Causal chain
1. PVC api-data is Pending.
2. Pod cannot mount volume.

### 5. Remediation
kubectl describe pvc api-data -n production
helm upgrade api ./chart -n production

### 6. Confidence
HIGH — evidence confirmed.
"""

_CANNED_LOW = """\
### 1. Summary
Unknown issue.

### 2. Affected resources
- Pod/production/api-xyz — Unknown

### 3. Root cause
Unclear.

### 4. Causal chain
1. Something happened.

### 5. Remediation
kubectl get pods -n production

### 6. Confidence
LOW — insufficient context.
"""


def _make_llm(response):
    """LLM mock: all calls return the given analysis response.
    hypothesize_node fills the pool from rules+ontology (no LLM needed)
    when synthetic_graph has sufficient signals."""
    llm = MagicMock()
    llm.is_available.return_value = True
    llm.model_is_pulled.return_value = True
    llm.model = "mistral"
    llm.generate.return_value = response
    return llm


def _make_llm_sequence(*responses):
    """LLM mock: responses are consumed in order by analyze_node calls.
    hypothesize_node does not call the LLM when evidence fills the pool."""
    llm = MagicMock()
    llm.is_available.return_value = True
    llm.model_is_pulled.return_value = True
    llm.model = "mistral"
    llm.generate.side_effect = list(responses)
    return llm


@pytest.fixture
def store(synthetic_graph):
    # Workflow tests exercise routing/confidence logic, not embedding quality —
    # use the deterministic offline FakeEmbedder so they run without the model.
    s = FAISSStore(embedder=FakeEmbedder())
    s.index_graph(synthetic_graph)
    return s


def _config(thread_id, synthetic_graph, store, llm):
    return {
        "configurable": {
            "thread_id": thread_id,
            "graph": synthetic_graph,
            "store": store,
            "llm": llm,
        }
    }


def _initial_state() -> RCAState:
    return {
        "query": "pods crashlooping",
        "retry_count": 0,
        "human_decision": "",
        "error": "",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Routing functions
# ─────────────────────────────────────────────────────────────────────────────

def _route_confidence(state: dict) -> str:
    """Helper: run log_confidence_decision_node then confidence_router (the real pipeline)."""
    merged = {**state, **log_confidence_decision_node(state)}
    return confidence_router(merged)


def _route_human(state: dict) -> str:
    """Helper: run log_human_decision_node then human_router (the real pipeline)."""
    merged = {**state, **log_human_decision_node(state)}
    return human_router(merged)


class TestConfidenceRouter:
    def test_low_with_retries_remaining(self):
        assert _route_confidence({"confidence": "LOW", "retry_count": 0}) == "retry"

    def test_low_at_max_retries_no_candidates(self):
        assert _route_confidence({"confidence": "LOW", "retry_count": MAX_RETRIES}) == "review"

    def test_low_at_max_retries_with_candidates(self):
        state = {"confidence": "LOW", "retry_count": MAX_RETRIES, "candidate_paths": ["H2: something"]}
        assert _route_confidence(state) == "next_path"

    def test_low_at_max_retries_empty_candidates(self):
        state = {"confidence": "LOW", "retry_count": MAX_RETRIES, "candidate_paths": []}
        assert _route_confidence(state) == "review"

    def test_medium_goes_to_review(self):
        assert _route_confidence({"confidence": "MEDIUM", "retry_count": 0}) == "review"

    def test_high_goes_to_review(self):
        assert _route_confidence({"confidence": "HIGH", "retry_count": 0}) == "review"

    def test_empty_confidence_goes_to_review(self):
        assert _route_confidence({"confidence": "", "retry_count": 0}) == "review"

    def test_low_just_before_max_still_retries(self):
        assert _route_confidence({"confidence": "LOW", "retry_count": MAX_RETRIES - 1}) == "retry"

    def test_log_node_writes_edge_log_entry(self):
        result = log_confidence_decision_node({"confidence": "LOW", "retry_count": 0})
        assert result["_confidence_edge"] == "retry"
        assert len(result["edge_log"]) == 1
        assert result["edge_log"][0]["router"] == "confidence"

    # ── Probability-decline / early-switch tests ──────────────────────────────

    def test_declining_probability_switches_early_with_candidates(self):
        # LOW × 2 consecutive on same path → switch without exhausting max_retries
        state = {
            "confidence": "LOW",
            "retry_count": 1,
            "candidate_paths": ["H2: something"],
            "path_confidence_history": ["LOW"],   # previous retry was also LOW
        }
        assert _route_confidence(state) == "next_path"

    def test_declining_probability_goes_review_without_candidates(self):
        state = {
            "confidence": "LOW",
            "retry_count": 1,
            "candidate_paths": [],
            "path_confidence_history": ["LOW"],
        }
        assert _route_confidence(state) == "review"

    def test_single_low_still_retries(self):
        # First LOW retry — no history yet, not declining
        state = {
            "confidence": "LOW",
            "retry_count": 0,
            "candidate_paths": ["H2: something"],
            "path_confidence_history": [],
        }
        assert _route_confidence(state) == "retry"

    def test_snapshot_includes_declining_flag(self):
        state = {
            "confidence": "LOW",
            "retry_count": 1,
            "candidate_paths": ["H2"],
            "path_confidence_history": ["LOW"],
        }
        result = log_confidence_decision_node(state)
        snapshot = result["edge_log"][-1]["snapshot"]
        assert snapshot["declining"] is True

    def test_path_confidence_history_appended(self):
        state = {
            "confidence": "LOW",
            "retry_count": 0,
            "path_confidence_history": ["LOW"],
        }
        result = log_confidence_decision_node(state)
        assert result["path_confidence_history"] == ["LOW", "LOW"]

    def test_path_confidence_history_reset_on_archive(self, synthetic_graph, store):
        # archive_path_node resets path_confidence_history for the new path
        state: RCAState = {
            "current_hypothesis":      "H1: PVC stuck",
            "candidate_paths":         ["H2: image drift"],
            "confidence":              "LOW",
            "retry_count":             1,
            "path_confidence_history": ["LOW", "LOW"],
            "reasoning_history":       [],
            "report_dict":             {},
            "raw_analysis":            "",
        }
        cfg_ = {"configurable": {"store": store}}
        result = archive_path_node(state, cfg_)
        assert result["path_confidence_history"] == []


class TestHumanRouter:
    def test_approve(self):
        assert _route_human({"human_decision": "approve"}) == "approve"

    def test_reject(self):
        assert _route_human({"human_decision": "reject"}) == "reject"

    def test_empty_string_defaults_to_reject(self):
        assert _route_human({"human_decision": ""}) == "reject"

    def test_missing_key_defaults_to_reject(self):
        assert _route_human({}) == "reject"

    def test_log_node_writes_edge_log_entry(self):
        result = log_human_decision_node({"human_decision": "approve"})
        assert result["_human_edge"] == "approve"
        assert len(result["edge_log"]) == 1
        assert result["edge_log"][0]["router"] == "human"


# ─────────────────────────────────────────────────────────────────────────────
# Graph structure
# ─────────────────────────────────────────────────────────────────────────────

class TestGraphStructure:
    def test_compiles_without_error(self):
        assert build_graph() is not None

    def test_has_expected_nodes(self):
        g = build_graph()
        for node in ("ingest", "gitops", "anchor", "index", "analyze",
                     "hypothesize", "archive_path", "select_best",
                     "dry_run", "human_review", "remediation",
                     "example_lookup", "save_example"):
            assert node in g.nodes

    def test_increment_retry_node_present(self):
        assert "increment_retry" in build_graph().nodes


# ─────────────────────────────────────────────────────────────────────────────
# Happy path — HIGH confidence → human approves
# ─────────────────────────────────────────────────────────────────────────────

class TestWorkflowApprove:
    def test_graph_pauses_at_human_review(self, synthetic_graph, store):
        llm = _make_llm(_CANNED_HIGH)
        compiled = build_graph()
        cfg_ = _config("t-pause", synthetic_graph, store, llm)

        events = list(compiled.stream(_initial_state(), config=cfg_))

        interrupt_events = [e for e in events if "__interrupt__" in e]
        assert len(interrupt_events) == 1

    def test_interrupt_payload_has_summary_and_remediation(self, synthetic_graph, store):
        llm = _make_llm(_CANNED_HIGH)
        compiled = build_graph()
        cfg_ = _config("t-payload", synthetic_graph, store, llm)

        events = list(compiled.stream(_initial_state(), config=cfg_))
        payload = [e for e in events if "__interrupt__" in e][0]["__interrupt__"][0].value

        assert "summary" in payload
        assert "remediation" in payload
        assert isinstance(payload["remediation"], list)
        assert "confidence" in payload

    def test_resume_approve_sets_human_decision(self, synthetic_graph, store):
        llm = _make_llm(_CANNED_HIGH)
        compiled = build_graph()
        cfg_ = _config("t-approve", synthetic_graph, store, llm)

        list(compiled.stream(_initial_state(), config=cfg_))
        final = compiled.invoke(Command(resume="approve"), config=cfg_)

        assert final.get("human_decision") == "approve"

    def test_resume_approve_report_is_populated(self, synthetic_graph, store):
        llm = _make_llm(_CANNED_HIGH)
        compiled = build_graph()
        cfg_ = _config("t-report", synthetic_graph, store, llm)

        list(compiled.stream(_initial_state(), config=cfg_))
        final = compiled.invoke(Command(resume="approve"), config=cfg_)

        assert final.get("report_dict") is not None
        assert final["report_dict"].get("summary")

    def test_resume_reject_ends_without_remediation(self, synthetic_graph, store):
        llm = _make_llm(_CANNED_HIGH)
        compiled = build_graph()
        cfg_ = _config("t-reject", synthetic_graph, store, llm)

        list(compiled.stream(_initial_state(), config=cfg_))
        final = compiled.invoke(Command(resume="reject"), config=cfg_)

        assert final.get("human_decision") == "reject"

    # The path, not only the decision (docs/fix-plan-001.md, H18): the decision used to be
    # logged before the interrupt, so the router always read "reject".
    @staticmethod
    def _resumed_nodes(compiled, cfg_, decision):
        list(compiled.stream(_initial_state(), config=cfg_))
        return [next(iter(u)) for u in
                compiled.stream(Command(resume=decision), config=cfg_, stream_mode="updates")]

    def test_approve_reaches_remediation(self, synthetic_graph, store):
        compiled = build_graph()
        cfg_ = _config("t-approve-path", synthetic_graph, store, _make_llm(_CANNED_HIGH))
        nodes = self._resumed_nodes(compiled, cfg_, "approve")
        assert nodes[:2] == ["human_review", "log_human_decision"]
        assert "remediation" in nodes and "save_example" in nodes
        human = [e for e in compiled.get_state(cfg_).values["edge_log"] if e["router"] == "human"]
        assert [e["edge_taken"] for e in human] == ["approve"]

    def test_the_approver_is_recorded(self, synthetic_graph, store):
        # docs/fix-plan-001.md, H44: who approved is kept with the decision
        compiled = build_graph()
        cfg_ = _config("t-approver", synthetic_graph, store, _make_llm(_CANNED_HIGH))
        list(compiled.stream(_initial_state(), config=cfg_))
        final = compiled.invoke(Command(resume={"decision": "approve", "by": "oidc:idp#alice"}),
                                config=cfg_)
        assert final["human_decision"] == "approve"
        assert final["human_decided_by"] == "oidc:idp#alice"
        human = [e for e in final["edge_log"] if e["router"] == "human"]
        assert human[-1]["snapshot"]["decided_by"] == "oidc:idp#alice"

    def test_reject_does_not_reach_remediation(self, synthetic_graph, store):
        compiled = build_graph()
        cfg_ = _config("t-reject-path", synthetic_graph, store, _make_llm(_CANNED_HIGH))
        nodes = self._resumed_nodes(compiled, cfg_, "reject")
        assert "remediation" not in nodes                                        # control
        human = [e for e in compiled.get_state(cfg_).values["edge_log"] if e["router"] == "human"]
        assert [e["edge_taken"] for e in human] == ["reject"]


# ─────────────────────────────────────────────────────────────────────────────
# Retry path — LOW confidence auto-retries then escalates to human
# ─────────────────────────────────────────────────────────────────────────────

class TestWorkflowRetry:
    def test_an_llm_low_does_not_retry_nor_lower_the_diagnosis(self, synthetic_graph, store):
        # docs/structured-in-product.md, rules 2 and 4: the confidence is the diagnosis'; the
        # LLM's own label is recorded, it no longer drives the retry loop
        llm = _make_llm_sequence(_CANNED_LOW, _CANNED_HIGH)
        compiled = build_graph()
        cfg_ = _config("t-retry", synthetic_graph, store, llm)
        events = list(compiled.stream(_initial_state(), config=cfg_))
        payload = [e for e in events if "__interrupt__" in e][0]["__interrupt__"][0].value
        assert llm.generate.call_count == 1
        assert payload["confidence"] == "MEDIUM"                    # config.drift, score 0.65


    def test_max_retries_not_exceeded(self, synthetic_graph, store):
        # Always LOW — exhausts all paths then escalates to human_review.
        # Budget: (MAX_RETRIES+1) analyze calls × MAX_PATHS paths
        budget = (MAX_RETRIES + 1) * MAX_PATHS
        llm = _make_llm_sequence(*([_CANNED_LOW] * (budget + 5)))
        compiled = build_graph()
        cfg_ = _config("t-max-retry", synthetic_graph, store, llm)

        events = list(compiled.stream(_initial_state(), config=cfg_))
        interrupt_events = [e for e in events if "__interrupt__" in e]

        # Still reaches human_review despite persistent LOW confidence
        assert len(interrupt_events) == 1
        assert llm.generate.call_count <= budget


# ─────────────────────────────────────────────────────────────────────────────
# anchor_node unit tests
# ─────────────────────────────────────────────────────────────────────────────

class TestAnchorNode:

    def _cfg(self, graph, provider=None):
        c = {"configurable": {"graph": graph}}
        if provider is not None:
            c["configurable"]["provider"] = provider
        return c

    def test_returns_empty_dict(self, synthetic_graph):
        result = anchor_node({}, self._cfg(synthetic_graph))
        # nodes now return ingestion_stats; check no unexpected top-level keys
        assert set(result.keys()) <= {"ingestion_stats"}

    def test_skips_when_no_graph(self):
        result = anchor_node({}, {"configurable": {}})
        assert result == {}

    def test_annotates_entities_with_anchor_prefix(self, synthetic_graph):
        anchor_node({}, self._cfg(synthetic_graph))
        annotated = [
            e for e in synthetic_graph.entities()
            if any(k.startswith("anchor.") for k in e.annotations)
        ]
        assert len(annotated) > 0

    def test_k8s_schema_anchors_written_without_provider(self, synthetic_graph):
        anchor_node({}, self._cfg(synthetic_graph, provider=None))
        # At least Deployment spec.replicas anchor should exist
        from ontology.entities import ResourceKind
        deps = list(synthetic_graph.entities(ResourceKind.DEPLOYMENT))
        if deps:
            assert any(
                k.startswith("anchor.") for k in deps[0].annotations
            )

    def test_anchor_node_uses_provider_from_config(self, synthetic_graph):
        """When a provider is in config, AnchorEngine.collect is called with it."""
        from unittest.mock import patch, MagicMock
        fake_provider = MagicMock()
        fake_provider.local_path.return_value = None

        with patch("ingestion.anchor_engine.AnchorEngine.collect",
                   return_value=[]) as mock_collect:
            anchor_node({}, self._cfg(synthetic_graph, provider=fake_provider))

        mock_collect.assert_called_once()
        assert mock_collect.call_args.kwargs["sources"][0][0] is fake_provider

    def test_fails_silently_on_exception(self, synthetic_graph):
        """AnchorEngine crash must not propagate — analysis must continue."""
        from unittest.mock import patch
        with patch("ingestion.anchor_engine.AnchorEngine.collect",
                   side_effect=RuntimeError("boom")):
            result = anchor_node({}, self._cfg(synthetic_graph))
        assert set(result.keys()) <= {"ingestion_stats"}
        assert result.get("ingestion_stats", {}).get("anchor", {}).get("fallback") is True

    def test_anchor_node_in_graph_topology(self):
        g = build_graph()
        assert "anchor" in g.nodes

    def test_gitops_sources_reach_anchor_node_through_langgraph(self, synthetic_graph, monkeypatch):
        """The provider used by gitops_node reaches anchor_node in a real LangGraph run.

        docs/fix-plan-001.md, H10: it was stored in config, which LangGraph does not pass from
        one node to the next, so the rendered-manifest anchors never ran. Calling the two nodes
        by hand with one shared dict (as this test used to) could not see it.
        """
        import config as cfg
        from unittest.mock import patch, MagicMock
        from langgraph.graph import StateGraph, START, END
        from workflow.nodes import gitops_node
        from workflow.state import RCAState
        monkeypatch.setattr(cfg, "GITOPS_ENABLED", True)
        monkeypatch.setattr(cfg, "GIT_REPOS", [{"url": "https://github.com/org/repo.git",
                                                "branch": "main", "charts_path": "charts"}])
        fake_provider = MagicMock()
        fake_collector = MagicMock()
        fake_collector.collect.return_value = []

        b = StateGraph(RCAState)
        b.add_node("gitops", gitops_node)
        b.add_node("anchor", anchor_node)
        b.add_edge(START, "gitops"); b.add_edge("gitops", "anchor"); b.add_edge("anchor", END)
        with patch("ingestion.git_provider.make_provider", return_value=fake_provider) as mk, \
             patch("ingestion.gitops_collector.GitopsCollector", return_value=fake_collector), \
             patch("ingestion.anchor_engine.AnchorEngine.collect", return_value=[]) as collect:
            b.compile().invoke({"query": "q"}, {"configurable": {"graph": synthetic_graph,
                                                               "thread_id": "t-h10"}})
        assert mk.call_args.args[0] == "https://github.com/org/repo.git"   # cloned, GitHub too
        assert collect.call_args.kwargs["sources"] == [(fake_provider, "charts")]


# ─────────────────────────────────────────────────────────────────────────────
# hypothesize_node unit tests
# ─────────────────────────────────────────────────────────────────────────────

class TestHypothesizeNode:
    """The paths are the ranked structured diagnoses (docs/structured-in-product.md, rule 7)."""

    @staticmethod
    def _state(diagnoses, outcome="cause"):
        return {"diagnosis": {"outcome": outcome, "diagnoses": diagnoses}}

    @staticmethod
    def _d(rule, cause, score):
        return {"family": rule, "cause": cause, "location": f"Pod/prod/{rule}", "score": score,
                "label": "MEDIUM", "rule": rule, "checks": [], "signal_ids": ["s1"]}

    def test_paths_follow_the_diagnosis_ranking(self):
        out = hypothesize_node(self._state([self._d("a", "cause A here", 0.8),
                                            self._d("b", "cause B here", 0.6)]), {})
        assert out["current_hypothesis"] == "cause A here"
        assert out["candidate_paths"] == ["cause B here"]
        assert out["reasoning_history"] == []
        assert [s["rule_id"] for s in out["hypothesis_sources"]] == ["a", "b"]

    def test_an_abstention_gives_no_path(self):                          # control
        out = hypothesize_node(self._state([], outcome="no_incident"), {})
        assert "current_hypothesis" not in out and out["hypothesis_sources"] == []

    def test_the_old_engine_is_not_called(self):
        from unittest.mock import patch
        with patch("rca.remediation_engine.RemediationEngine.score",
                   side_effect=AssertionError("old engine called")):
            hypothesize_node(self._state([self._d("a", "cause A here", 0.8)]), {})


# ─────────────────────────────────────────────────────────────────────────────
# archive_path_node unit tests
# ─────────────────────────────────────────────────────────────────────────────

class TestArchivePathNode:

    def test_appends_to_history(self):
        state = {
            "current_hypothesis": "Pod/ns/foo — CrashLoopBackOff",
            "confidence": "LOW",
            "retry_count": MAX_RETRIES,
            "raw_analysis": "some analysis",
            "report_dict": {"summary": "s", "root_cause": "r", "remediation": []},
            "reasoning_history": [],
            "candidate_paths": ["H2: something"],
        }
        result = archive_path_node(state, {"configurable": {}})
        assert len(result["reasoning_history"]) == 1
        assert result["reasoning_history"][0]["hypothesis"] == "Pod/ns/foo — CrashLoopBackOff"

    def test_pops_next_hypothesis(self):
        state = {
            "current_hypothesis": "H1",
            "confidence": "LOW",
            "retry_count": MAX_RETRIES,
            "raw_analysis": "",
            "report_dict": {},
            "reasoning_history": [],
            "candidate_paths": ["H2", "H3"],
        }
        result = archive_path_node(state, {"configurable": {}})
        assert result["current_hypothesis"] == "H2"
        assert result["candidate_paths"] == ["H3"]

    def test_resets_retry_count(self):
        state = {
            "current_hypothesis": "H1",
            "confidence": "LOW",
            "retry_count": MAX_RETRIES,
            "raw_analysis": "",
            "report_dict": {},
            "reasoning_history": [],
            "candidate_paths": ["H2"],
        }
        result = archive_path_node(state, {"configurable": {}})
        assert result["retry_count"] == 0

    def test_empty_candidates_clears_hypothesis(self):
        state = {
            "current_hypothesis": "H1",
            "confidence": "LOW",
            "retry_count": MAX_RETRIES,
            "raw_analysis": "",
            "report_dict": {},
            "reasoning_history": [],
            "candidate_paths": [],
        }
        result = archive_path_node(state, {"configurable": {}})
        assert result["current_hypothesis"] == ""


# ─────────────────────────────────────────────────────────────────────────────
# select_best_node unit tests
# ─────────────────────────────────────────────────────────────────────────────

class TestSelectBestNode:

    def test_noop_when_no_history(self):
        assert select_best_node({"confidence": "LOW", "reasoning_history": []},
                                {"configurable": {}}) == {}

    def test_restores_higher_confidence_path(self):
        state = {
            "confidence": "LOW",
            "raw_analysis": "current low",
            "report_dict": {"summary": "low"},
            "reasoning_history": [
                {"step": 1, "hypothesis": "H1", "confidence": "HIGH",
                 "raw_analysis": "high analysis",
                 "report_dict": {"summary": "high summary"}, "retry_count": 0},
            ],
        }
        result = select_best_node(state, {"configurable": {}})
        assert result["confidence"] == "HIGH"
        assert result["report_dict"]["summary"] == "high summary"

    def test_noop_when_current_is_already_best(self):
        state = {
            "confidence": "HIGH",
            "reasoning_history": [
                {"step": 1, "hypothesis": "H1", "confidence": "LOW",
                 "raw_analysis": "", "report_dict": {}, "retry_count": 2},
            ],
        }
        assert select_best_node(state, {"configurable": {}}) == {}

    def test_medium_beats_low(self):
        state = {
            "confidence": "LOW",
            "raw_analysis": "",
            "report_dict": {},
            "reasoning_history": [
                {"step": 1, "hypothesis": "H1", "confidence": "MEDIUM",
                 "raw_analysis": "medium", "report_dict": {"summary": "mid"}, "retry_count": 1},
            ],
        }
        result = select_best_node(state, {"configurable": {}})
        assert result["confidence"] == "MEDIUM"


# ─────────────────────────────────────────────────────────────────────────────
# Multi-path end-to-end tests
# ─────────────────────────────────────────────────────────────────────────────

class TestWorkflowMultiPath:

    def test_path_switch_triggers_on_persistent_low(self, synthetic_graph, store):
        # Enough LOW to exhaust first path, then HIGH on second path
        responses = [_CANNED_LOW] * (MAX_RETRIES + 1) + [_CANNED_HIGH]
        llm = _make_llm_sequence(*responses)
        compiled = build_graph()
        cfg_ = _config("t-multipath", synthetic_graph, store, llm)

        events = list(compiled.stream(_initial_state(), config=cfg_))
        interrupt_events = [e for e in events if "__interrupt__" in e]

        assert len(interrupt_events) == 1

    def test_one_path_per_diagnosis_no_llm_driven_switch(self, synthetic_graph, store):
        responses = [_CANNED_LOW] * (MAX_RETRIES + 1) + [_CANNED_HIGH]
        llm = _make_llm_sequence(*responses)
        compiled = build_graph()
        cfg_ = _config("t-history", synthetic_graph, store, llm)
        events = list(compiled.stream(_initial_state(), config=cfg_))
        payload = [e for e in events if "__interrupt__" in e][0]["__interrupt__"][0].value
        assert payload["paths_explored"] == 1 and payload["reasoning_history"] == []

    def test_reasoning_history_entries_have_required_fields(self, synthetic_graph, store):
        responses = [_CANNED_LOW] * (MAX_RETRIES + 1) + [_CANNED_HIGH]
        llm = _make_llm_sequence(*responses)
        compiled = build_graph()
        cfg_ = _config("t-history-fields", synthetic_graph, store, llm)

        events = list(compiled.stream(_initial_state(), config=cfg_))
        payload = [e for e in events if "__interrupt__" in e][0]["__interrupt__"][0].value

        for entry in payload["reasoning_history"]:
            assert "step" in entry
            assert "hypothesis" in entry
            assert "confidence" in entry
            assert "summary" in entry


    def test_interrupt_payload_contains_edge_log(self, synthetic_graph, store):
        llm = _make_llm(_CANNED_HIGH)
        compiled = build_graph()
        cfg_ = _config("t-edge-log", synthetic_graph, store, llm)

        events = list(compiled.stream(_initial_state(), config=cfg_))
        payload = [e for e in events if "__interrupt__" in e][0]["__interrupt__"][0].value

        assert "edge_log" in payload
        assert isinstance(payload["edge_log"], list)
        assert len(payload["edge_log"]) >= 1
        # At least one confidence router decision must be present
        conf_entries = [e for e in payload["edge_log"] if e["router"] == "confidence"]
        assert len(conf_entries) >= 1
        entry = conf_entries[0]
        assert entry["edge_taken"] in ("retry", "next_path", "review")
        assert "reason" in entry
        assert "snapshot" in entry


# ─────────────────────────────────────────────────────────────────────────────
# dry_run_node unit tests
# ─────────────────────────────────────────────────────────────────────────────

class TestDryRunNode:
    """Tests for dry_run_node and _exec_dry_run."""

    def _state_with_remediation(self, cmds):
        return {"report_dict": {"remediation": cmds}}

    def test_empty_remediation_returns_empty(self):
        from workflow.nodes import dry_run_node
        result = dry_run_node({}, {"configurable": {}})
        assert result == {}

    def test_empty_report_dict_returns_empty(self):
        from workflow.nodes import dry_run_node
        result = dry_run_node({"report_dict": {}}, {"configurable": {}})
        assert result == {}

    def test_results_keyed_correctly(self):
        from unittest.mock import patch
        from workflow.nodes import dry_run_node

        state = self._state_with_remediation(["kubectl get pods -n default"])
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "pod/api-xyz   Running"
            mock_run.return_value.stderr = ""
            result = dry_run_node(state, {"configurable": {}})

        assert "dry_run_results" in result
        items = result["dry_run_results"]
        assert len(items) == 1
        assert items[0]["original_cmd"] == "kubectl get pods -n default"
        assert items[0]["exit_code"] == 0
        assert "output" in items[0]
        assert "dry_cmd" in items[0]

    def test_kubectl_gets_dry_run_server_flag(self):
        from unittest.mock import patch
        from workflow.nodes import _exec_dry_run

        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "configmap/x created (server dry run)"
            mock_run.return_value.stderr = ""
            dry_cmd, out, rc = _exec_dry_run(
                "kubectl create configmap x --from-literal=k=v -n demo"
            )

        assert "--dry-run=server" in dry_cmd
        assert rc == 0

    def test_helm_upgrade_gets_dry_run_flag(self):
        from unittest.mock import patch
        from workflow.nodes import _exec_dry_run

        # Simulate helm diff not installed (FileNotFoundError) → falls back to _helm_values_diff
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                FileNotFoundError("helm diff not found"),   # helm diff attempt
                # _helm_values_diff calls helm get values — return empty JSON
                type("R", (), {"returncode": 0, "stdout": "{}", "stderr": ""})(),
            ]
            dry_cmd, out, rc = _exec_dry_run(
                "helm upgrade analytics-worker -n kubeverdict-demo --set resources.limits.memory=512Mi"
            )

        # fallback is a values diff: dry_cmd describes the helm get + proposed --set
        assert "helm get values" in dry_cmd
        assert "analytics-worker" in dry_cmd
        assert rc == 0

    def test_shell_construct_skipped(self):
        from workflow.nodes import _exec_dry_run
        dry_cmd, out, rc = _exec_dry_run("kubectl apply -f - <<EOF\napiVersion: v1\nEOF")
        assert "skipped" in out
        assert rc == 0

    def test_unsupported_tool_returns_note(self):
        from workflow.nodes import _exec_dry_run
        dry_cmd, out, rc = _exec_dry_run("docker restart my-container")
        assert "not supported" in out
        assert rc == 0

    def test_dry_run_results_in_interrupt_payload(self, synthetic_graph, store):
        """dry_run node runs before human_review; results appear in interrupt payload."""
        from unittest.mock import patch
        llm = _make_llm(_CANNED_HIGH)
        compiled = build_graph()
        cfg_ = _config("t-dryrun-payload", synthetic_graph, store, llm)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "pod/api deleted (server dry run)"
            mock_run.return_value.stderr = ""
            events = list(compiled.stream(_initial_state(), config=cfg_))

        interrupt_events = [e for e in events if "__interrupt__" in e]
        assert interrupt_events, "workflow should have reached human_review"
        payload = interrupt_events[0]["__interrupt__"][0].value
        assert "dry_run_results" in payload
        assert "no_solution" in payload
        # Results may be empty if canned report has no remediation commands —
        # just check the keys exist


# ─────────────────────────────────────────────────────────────────────────────
# no_solution flag in human_review payload
# ─────────────────────────────────────────────────────────────────────────────

class TestNoSolutionFlag:
    def _payload(self, confidence, remediation, root_cause="some cause"):
        """Call human_review_node and capture the interrupt payload."""
        from unittest.mock import patch
        from workflow.nodes import human_review_node
        state = {
            "confidence":         confidence,
            "report_dict":        {"root_cause": root_cause, "remediation": remediation},
            "reasoning_history":  [],
            "dry_run_results":    [],
        }
        captured = {}
        def fake_interrupt(payload):
            captured["payload"] = payload
            return "reject"
        with patch("workflow.nodes.interrupt", side_effect=fake_interrupt):
            human_review_node(state, {"configurable": {}})
        return captured["payload"]

    def test_no_remediation_sets_no_solution(self):
        p = self._payload("HIGH", [])
        assert p["no_solution"] is True

    def test_low_confidence_no_root_cause_sets_no_solution(self):
        p = self._payload("LOW", [], root_cause="")
        assert p["no_solution"] is True

    def test_high_confidence_with_remediation_not_no_solution(self):
        p = self._payload("HIGH", ["kubectl delete pod x"])
        assert p["no_solution"] is False

    def test_low_confidence_with_remediation_not_no_solution(self):
        # LOW confidence but HAS remediation → still show approve/reject with warning
        p = self._payload("LOW", ["kubectl rollout restart deployment/x"])
        assert p["no_solution"] is False


# ─────────────────────────────────────────────────────────────────────────────
# Example lookup + save
# ─────────────────────────────────────────────────────────────────────────────

class TestExampleLookup:
    def test_no_store_returns_empty(self):
        from workflow.nodes import example_lookup_node
        result = example_lookup_node({"query": "crashloop"}, {"configurable": {}})
        assert result == {}

    def test_no_example_hits_returns_empty(self, store):
        from workflow.nodes import example_lookup_node
        # store has no example: UIDs
        result = example_lookup_node(
            {"query": "pods crashlooping"},
            {"configurable": {"store": store}},
        )
        assert not result.get("example_match")

    def test_low_score_no_match(self, store):
        from unittest.mock import patch
        from workflow.nodes import example_lookup_node
        fake_hit = {"uid": "example:abc123", "text": "RESOLVED INCIDENT: x\nRoot cause: y\nHypothesis: z\nEntities: \nAnchor violations: \nFix: kubectl delete pod p\nConfidence: HIGH", "score": 0.50}
        with patch.object(store, "search", return_value=[fake_hit]):
            result = example_lookup_node({"query": "crashloop"}, {"configurable": {"store": store}})
        assert not result.get("example_match")

    _STRONG_HIT = {"uid": "example:abc123", "text": "RESOLVED INCIDENT: oom\nRoot cause: memory limit too low\nHypothesis: H1\nEntities: Pod\nAnchor violations: resources.limits.memory\nFix: kubectl delete pod crasher\nConfidence: HIGH", "score": 0.90}

    def test_shortcut_is_off_by_default(self, store):
        """A strong match no longer skips the analysis unless EXAMPLE_SHORTCUT_ENABLED is set."""
        from unittest.mock import patch
        from workflow.nodes import example_lookup_node
        with patch.object(store, "search", return_value=[self._STRONG_HIT]):
            result = example_lookup_node({"query": "oom kill"}, {"configurable": {"store": store}})
        assert result == {}

    def test_high_score_matches(self, store):
        from unittest.mock import patch

        import config as cfg
        from workflow.nodes import example_lookup_node
        with patch.object(store, "search", return_value=[self._STRONG_HIT]), \
                patch.object(cfg, "EXAMPLE_SHORTCUT_ENABLED", True):
            result = example_lookup_node({"query": "oom kill"}, {"configurable": {"store": store}})
        assert result.get("example_match") is True
        assert result.get("matched_example_id") == "example:abc123"
        assert result.get("confidence") == "HIGH"
        assert "kubectl delete pod" in (result.get("report_dict") or {}).get("remediation", [""])[0]

    def test_example_router_skip(self):
        from workflow.nodes import example_router
        assert example_router({"example_match": True}) == "skip"

    def test_example_router_analyze(self):
        from workflow.nodes import example_router
        assert example_router({}) == "analyze"
        assert example_router({"example_match": False}) == "analyze"

    def test_save_example_persists(self, synthetic_graph, store, tmp_path):
        from workflow.nodes import save_example_node
        state = {
            "query": "pods oom",
            "current_hypothesis": "memory limit too low",
            "confidence": "HIGH",
            "report_dict": {
                "root_cause": "memory limit too low",
                "remediation": ["kubectl delete pod crasher -n demo"],
                "affected_resources": [{"kind": "Pod", "name": "crasher"}],
            },
        }
        result = save_example_node(
            state, {"configurable": {"graph": synthetic_graph, "store": store, "example_dir": tmp_path}})
        assert "ingestion_stats" in result or result == {} or "save_example" in (result.get("ingestion_stats") or {})
        saved = list(tmp_path.glob("*.json"))
        assert len(saved) == 1
        assert json.loads(saved[0].read_text())["root_cause"] == "memory limit too low"


def test_tests_never_write_to_the_real_example_store():
    """Negative control: the suite's default example store is a temp dir, not data/examples/."""
    from pathlib import Path

    from knowledge.example_store import ExampleStore, default_example_dir

    real = (Path(__file__).resolve().parents[2] / "data" / "examples").resolve()
    assert default_example_dir().resolve() != real
    assert ExampleStore()._dir.resolve() != real
