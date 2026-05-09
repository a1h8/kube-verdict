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
from workflow.nodes import confidence_router, human_router, MAX_RETRIES
from workflow.state import RCAState


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

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
    llm = MagicMock()
    llm.is_available.return_value = True
    llm.model_is_pulled.return_value = True
    llm.model = "mistral"
    llm.generate.return_value = response
    return llm


def _make_llm_sequence(*responses):
    llm = MagicMock()
    llm.is_available.return_value = True
    llm.model_is_pulled.return_value = True
    llm.model = "mistral"
    llm.generate.side_effect = list(responses)
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

    def test_low_at_max_retries(self):
        assert confidence_router({"confidence": "LOW", "retry_count": MAX_RETRIES}) == "review"

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
        for node in ("ingest", "index", "analyze", "human_review", "remediation"):
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

        # LLM must have been called twice
        assert llm.generate.call_count == 2

    def test_after_retry_confidence_is_high(self, synthetic_graph, store):
        llm = _make_llm_sequence(_CANNED_LOW, _CANNED_HIGH)
        compiled = build_graph()
        cfg_ = _config("t-retry-conf", synthetic_graph, store, llm)

        events = list(compiled.stream(_initial_state(), config=cfg_))
        payload = [e for e in events if "__interrupt__" in e][0]["__interrupt__"][0].value

        assert payload["confidence"].upper().startswith("HIGH")

    def test_max_retries_not_exceeded(self, synthetic_graph, store):
        # Always LOW — should stop retrying at MAX_RETRIES and still reach human_review
        low_responses = [_CANNED_LOW] * (MAX_RETRIES + 5)
        llm = _make_llm_sequence(*low_responses)
        compiled = build_graph()
        cfg_ = _config("t-max-retry", synthetic_graph, store, llm)

        events = list(compiled.stream(_initial_state(), config=cfg_))
        interrupt_events = [e for e in events if "__interrupt__" in e]

        # Still reaches human_review despite persistent LOW confidence
        assert len(interrupt_events) == 1
        # LLM was called at most MAX_RETRIES + 1 times
        assert llm.generate.call_count <= MAX_RETRIES + 1
