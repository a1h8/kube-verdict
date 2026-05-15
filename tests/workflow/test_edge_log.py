"""Unit tests for log_confidence_decision_node and log_human_decision_node."""
from workflow.nodes import (
    log_confidence_decision_node,
    log_human_decision_node,
    confidence_router,
    human_router,
)


def _conf_state(confidence="LOW", retry=0, candidates=None, ingestion_stats=None):
    return {
        "confidence": confidence,
        "retry_count": retry,
        "candidate_paths": candidates or [],
        "ingestion_stats": ingestion_stats or {},
        "edge_log": [],
    }


# ── confidence decision node ──────────────────────────────────────────────────

def test_confidence_low_retry():
    state = _conf_state(confidence="LOW", retry=0)
    out = log_confidence_decision_node(state)
    assert out["_confidence_edge"] == "retry"
    assert len(out["edge_log"]) == 1
    entry = out["edge_log"][0]
    assert entry["router"] == "confidence"
    assert entry["edge_taken"] == "retry"
    assert "LOW" in entry["reason"]
    assert "retry" in entry["reason"]


def test_confidence_low_retries_exhausted_next_path():
    state = _conf_state(confidence="LOW", retry=2, candidates=["network hypothesis"])
    out = log_confidence_decision_node(state)
    assert out["_confidence_edge"] == "next_path"
    assert "next hypothesis" in out["edge_log"][0]["reason"]


def test_confidence_low_retries_exhausted_no_candidates():
    state = _conf_state(confidence="LOW", retry=2, candidates=[])
    out = log_confidence_decision_node(state)
    assert out["_confidence_edge"] == "review"
    assert "escalating" in out["edge_log"][0]["reason"]


def test_confidence_high_goes_to_review():
    state = _conf_state(confidence="HIGH")
    out = log_confidence_decision_node(state)
    assert out["_confidence_edge"] == "review"
    assert "HIGH" in out["edge_log"][0]["reason"]


def test_ingestion_failures_appear_in_reason():
    state = _conf_state(
        confidence="LOW", retry=0,
        ingestion_stats={"prometheus": {"fallback": True, "error": "timeout"}, "otel": {"fallback": False}},
    )
    out = log_confidence_decision_node(state)
    assert "prometheus" in out["edge_log"][0]["reason"]
    assert "otel" not in out["edge_log"][0]["reason"]


def test_edge_log_accumulates():
    state = _conf_state(confidence="LOW", retry=0, candidates=["h2"])
    out1 = log_confidence_decision_node(state)
    # second call on updated state
    state2 = {**state, "edge_log": out1["edge_log"], "retry_count": 2, "candidate_paths": ["h2"]}
    out2 = log_confidence_decision_node(state2)
    assert len(out2["edge_log"]) == 2


def test_confidence_router_reads_precomputed_edge():
    state = {"_confidence_edge": "retry"}
    assert confidence_router(state) == "retry"

def test_confidence_router_defaults_review():
    assert confidence_router({}) == "review"


# ── human decision node ───────────────────────────────────────────────────────

def _human_state(decision="", remediation=None, dry_runs=None, confidence="HIGH"):
    return {
        "human_decision": decision,
        "confidence": confidence,
        "report_dict": {"remediation": remediation or [], "root_cause": "OOMKill"},
        "dry_run_results": dry_runs or [],
        "edge_log": [],
    }


def test_human_approve():
    state = _human_state(decision="approve", remediation=["kubectl rollout restart deploy/api"])
    out = log_human_decision_node(state)
    assert out["_human_edge"] == "approve"
    assert "approved" in out["edge_log"][0]["reason"]


def test_human_reject_explicit():
    state = _human_state(decision="reject")
    out = log_human_decision_node(state)
    assert out["_human_edge"] == "reject"


def test_human_reject_default_no_decision():
    state = _human_state(decision="")
    out = log_human_decision_node(state)
    assert out["_human_edge"] == "reject"
    assert "no human decision" in out["edge_log"][0]["reason"]


def test_human_reject_with_failed_dryrun():
    dry_runs = [{"dry_cmd": "kubectl delete ns production", "exit_code": 1, "output": "error"}]
    state = _human_state(decision="reject", dry_runs=dry_runs)
    out = log_human_decision_node(state)
    assert out["_human_edge"] == "reject"
    assert "dry-run" in out["edge_log"][0]["reason"]
    assert "kubectl delete ns production" in out["edge_log"][0]["reason"]


def test_human_router_reads_precomputed_edge():
    assert human_router({"_human_edge": "approve"}) == "approve"

def test_human_router_defaults_reject():
    assert human_router({}) == "reject"
