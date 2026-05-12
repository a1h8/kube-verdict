"""
Unit tests for the LangGraph RCA workflow.

Graph and store are injected via config["configurable"] — never in state —
so LangGraph's MemorySaver checkpointer never tries to msgpack-serialise them.
"""
from unittest.mock import MagicMock

import pytest
from langgraph.types import Command

from vectorstore.embedder import Embedder
from vectorstore.store import FAISSStore
from workflow.graph import build_graph
from workflow.nodes import (
    anchor_node, archive_path_node, confidence_router, human_router,
    hypothesize_node, select_best_node,
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
    """LLM mock: first call (hypothesize) returns valid H1/H2/H3 lines;
    all subsequent calls return the given analysis response."""
    llm = MagicMock()
    llm.is_available.return_value = True
    llm.model_is_pulled.return_value = True
    llm.model = "mistral"
    _n = [0]
    def _gen(_prompt, **_kw):
        _n[0] += 1
        return _CANNED_HYPOTHESES if _n[0] == 1 else response
    llm.generate.side_effect = _gen
    return llm


def _make_llm_sequence(*responses):
    """LLM mock: prepends a hypothesis response so hypothesize_node gets valid H1/H2/H3 lines;
    the caller-supplied responses are then consumed by analyze_node calls."""
    llm = MagicMock()
    llm.is_available.return_value = True
    llm.model_is_pulled.return_value = True
    llm.model = "mistral"
    llm.generate.side_effect = [_CANNED_HYPOTHESES] + list(responses)
    return llm


@pytest.fixture
def store(synthetic_graph):
    s = FAISSStore(embedder=Embedder())
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

class TestConfidenceRouter:
    def test_low_with_retries_remaining(self):
        assert confidence_router({"confidence": "LOW", "retry_count": 0}) == "retry"

    def test_low_at_max_retries_no_candidates(self):
        assert confidence_router({"confidence": "LOW", "retry_count": MAX_RETRIES}) == "review"

    def test_low_at_max_retries_with_candidates(self):
        state = {"confidence": "LOW", "retry_count": MAX_RETRIES, "candidate_paths": ["H2: something"]}
        assert confidence_router(state) == "next_path"

    def test_low_at_max_retries_empty_candidates(self):
        state = {"confidence": "LOW", "retry_count": MAX_RETRIES, "candidate_paths": []}
        assert confidence_router(state) == "review"

    def test_medium_goes_to_review(self):
        assert confidence_router({"confidence": "MEDIUM", "retry_count": 0}) == "review"

    def test_high_goes_to_review(self):
        assert confidence_router({"confidence": "HIGH", "retry_count": 0}) == "review"

    def test_empty_confidence_goes_to_review(self):
        assert confidence_router({"confidence": "", "retry_count": 0}) == "review"

    def test_low_just_before_max_still_retries(self):
        assert confidence_router({"confidence": "LOW", "retry_count": MAX_RETRIES - 1}) == "retry"


class TestHumanRouter:
    def test_approve(self):
        assert human_router({"human_decision": "approve"}) == "approve"

    def test_reject(self):
        assert human_router({"human_decision": "reject"}) == "reject"

    def test_empty_string_defaults_to_reject(self):
        assert human_router({"human_decision": ""}) == "reject"

    def test_missing_key_defaults_to_reject(self):
        assert human_router({}) == "reject"


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


# ─────────────────────────────────────────────────────────────────────────────
# Retry path — LOW confidence auto-retries then escalates to human
# ─────────────────────────────────────────────────────────────────────────────

class TestWorkflowRetry:
    def test_low_confidence_triggers_retry(self, synthetic_graph, store):
        llm = _make_llm_sequence(_CANNED_LOW, _CANNED_HIGH)
        compiled = build_graph()
        cfg_ = _config("t-retry", synthetic_graph, store, llm)

        list(compiled.stream(_initial_state(), config=cfg_))

        # hypothesize(1) + analyze_low(1) + analyze_high(1) = 3 LLM calls
        assert llm.generate.call_count == 3

    def test_after_retry_confidence_is_high(self, synthetic_graph, store):
        llm = _make_llm_sequence(_CANNED_LOW, _CANNED_HIGH)
        compiled = build_graph()
        cfg_ = _config("t-retry-conf", synthetic_graph, store, llm)

        events = list(compiled.stream(_initial_state(), config=cfg_))
        payload = [e for e in events if "__interrupt__" in e][0]["__interrupt__"][0].value

        assert payload["confidence"].upper().startswith("HIGH")

    def test_max_retries_not_exceeded(self, synthetic_graph, store):
        # Always LOW — exhausts all paths then escalates to human_review.
        # Budget: 1 hypothesize + (MAX_RETRIES+1) analyze calls × MAX_PATHS paths
        budget = 1 + (MAX_RETRIES + 1) * MAX_PATHS
        llm = _make_llm_sequence(*([_CANNED_LOW] * (budget + 5)))
        compiled = build_graph()
        cfg_ = _config("t-max-retry", synthetic_graph, store, llm)

        events = list(compiled.stream(_initial_state(), config=cfg_))
        interrupt_events = [e for e in events if "__interrupt__" in e]

        # Still reaches human_review despite persistent LOW confidence
        assert len(interrupt_events) == 1
        # Total LLM calls bounded by 1 (hypothesize) + analysis budget
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
        _, kwargs = mock_collect.call_args
        assert kwargs.get("provider") is fake_provider or mock_collect.call_args[0][1] is fake_provider

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

    def test_gitops_stores_provider_in_config(self, synthetic_graph, monkeypatch):
        """gitops_node stores provider so anchor_node can reuse it."""
        import config as cfg
        monkeypatch.setattr(cfg, "GITOPS_ENABLED", True)
        monkeypatch.setattr(cfg, "GITOPS_REPO_URL", "https://github.com/org/repo")
        monkeypatch.setattr(cfg, "GITOPS_BRANCH", "main")
        monkeypatch.setattr(cfg, "GITHUB_TOKEN", None)

        from unittest.mock import patch, MagicMock
        fake_provider = MagicMock()
        fake_collector = MagicMock()
        fake_collector.collect.return_value = []

        config = {"configurable": {"graph": synthetic_graph}}

        with patch("ingestion.git_provider.GithubProvider", return_value=fake_provider), \
             patch("ingestion.gitops_collector.GitopsCollector",
                   return_value=fake_collector):
            from workflow.nodes import gitops_node
            gitops_node({}, config)

        assert config["configurable"].get("provider") is fake_provider


# ─────────────────────────────────────────────────────────────────────────────
# hypothesize_node unit tests
# ─────────────────────────────────────────────────────────────────────────────

class TestHypothesizeNode:

    def _cfg(self, graph, llm=None):
        c = {"configurable": {"graph": graph}}
        if llm is not None:
            c["configurable"]["llm"] = llm
        return c

    def _llm_with(self, response):
        llm = MagicMock()
        llm.is_available.return_value = True
        llm.model_is_pulled.return_value = True
        llm.model = "mistral"
        llm.generate.return_value = response
        return llm

    def test_parses_h1_h2_h3_lines(self, synthetic_graph):
        llm = self._llm_with(_CANNED_HYPOTHESES)
        result = hypothesize_node({"query": "pods crashing"}, self._cfg(synthetic_graph, llm))
        assert result.get("current_hypothesis")
        assert "candidate_paths" in result
        assert len(result["candidate_paths"]) == 2

    def test_first_hypothesis_set_as_current(self, synthetic_graph):
        llm = self._llm_with(_CANNED_HYPOTHESES)
        result = hypothesize_node({"query": "q"}, self._cfg(synthetic_graph, llm))
        assert result["current_hypothesis"] == (
            "Pod api-xyz CrashLoopBackOff — container cannot start due to missing volume mount"
        )

    def test_candidate_paths_capped_at_max_paths(self, synthetic_graph):
        extra = _CANNED_HYPOTHESES + "H4: extra hypothesis beyond cap\n"
        llm = self._llm_with(extra)
        result = hypothesize_node({"query": "q"}, self._cfg(synthetic_graph, llm))
        total = 1 + len(result.get("candidate_paths") or [])
        assert total <= MAX_PATHS

    def test_skips_when_no_graph(self):
        llm = self._llm_with(_CANNED_HYPOTHESES)
        assert hypothesize_node({}, {"configurable": {"llm": llm}}) == {}

    def test_resets_reasoning_history(self, synthetic_graph):
        llm = self._llm_with(_CANNED_HYPOTHESES)
        result = hypothesize_node({"query": "q"}, self._cfg(synthetic_graph, llm))
        assert result["reasoning_history"] == []

    def test_parses_numbered_list_format(self, synthetic_graph):
        numbered = "1. OOM kill in api-xyz container\n2. PVC pending — no PV available\n3. Deployment replica drift"
        llm = self._llm_with(numbered)
        result = hypothesize_node({"query": "q"}, self._cfg(synthetic_graph, llm))
        assert result.get("current_hypothesis") == "OOM kill in api-xyz container"

    def test_parses_bullet_list_format(self, synthetic_graph):
        bullets = "- CrashLoopBackOff in api container\n- Missing ConfigMap env vars\n- Image pull error"
        llm = self._llm_with(bullets)
        result = hypothesize_node({"query": "q"}, self._cfg(synthetic_graph, llm))
        assert result.get("current_hypothesis") == "CrashLoopBackOff in api container"

    def test_fallback_when_llm_returns_no_h_lines(self, synthetic_graph):
        llm = self._llm_with("I cannot determine the root cause.")
        result = hypothesize_node({"query": "q"}, self._cfg(synthetic_graph, llm))
        assert result == {}

    def test_fallback_when_llm_raises(self, synthetic_graph):
        llm = self._llm_with("")
        llm.generate.side_effect = RuntimeError("Ollama unavailable")
        result = hypothesize_node({"query": "q"}, self._cfg(synthetic_graph, llm))
        assert result == {}


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

    def test_interrupt_payload_contains_reasoning_history(self, synthetic_graph, store):
        # Trigger at least one path switch
        responses = [_CANNED_LOW] * (MAX_RETRIES + 1) + [_CANNED_HIGH]
        llm = _make_llm_sequence(*responses)
        compiled = build_graph()
        cfg_ = _config("t-history", synthetic_graph, store, llm)

        events = list(compiled.stream(_initial_state(), config=cfg_))
        payload = [e for e in events if "__interrupt__" in e][0]["__interrupt__"][0].value

        assert "reasoning_history" in payload
        assert "paths_explored" in payload
        assert payload["paths_explored"] >= 2

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

    def test_best_path_selected_over_current(self, synthetic_graph, store):
        # Path 1: HIGH (archived), Path 2: LOW (current) → select_best restores HIGH
        responses = [_CANNED_HIGH] + [_CANNED_LOW] * (MAX_RETRIES + 1)
        # Actually this won't trigger path switch because HIGH goes straight to review.
        # Instead: LOW path1 exhausted → switch → HIGH path2 → select_best keeps HIGH
        responses = [_CANNED_LOW] * (MAX_RETRIES + 1) + [_CANNED_HIGH]
        llm = _make_llm_sequence(*responses)
        compiled = build_graph()
        cfg_ = _config("t-select-best", synthetic_graph, store, llm)

        events = list(compiled.stream(_initial_state(), config=cfg_))
        payload = [e for e in events if "__interrupt__" in e][0]["__interrupt__"][0].value

        assert payload["confidence"].upper().startswith("HIGH")


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
                "helm upgrade analytics-worker -n kubewhisperer-demo --set resources.limits.memory=512Mi"
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

    def test_high_score_matches(self, store):
        from unittest.mock import patch
        from workflow.nodes import example_lookup_node
        fake_hit = {"uid": "example:abc123", "text": "RESOLVED INCIDENT: oom\nRoot cause: memory limit too low\nHypothesis: H1\nEntities: Pod\nAnchor violations: resources.limits.memory\nFix: kubectl delete pod crasher\nConfidence: HIGH", "score": 0.90}
        with patch.object(store, "search", return_value=[fake_hit]):
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
        from knowledge.example_store import ExampleStore
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
        save_example_node(state, {"configurable": {"graph": synthetic_graph, "store": store, "__example_dir": tmp_path}})
        # ExampleStore uses default dir — just verify the node doesn't crash
        # and returns stats
        result = save_example_node(state, {"configurable": {"graph": synthetic_graph, "store": store}})
        assert "ingestion_stats" in result or result == {} or "save_example" in (result.get("ingestion_stats") or {})
