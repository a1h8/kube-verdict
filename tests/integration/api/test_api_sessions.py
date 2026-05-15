"""
Integration tests — FastAPI session lifecycle.

Full HTTP cycle: health → create → run → state → feedback → delete.
Workflow execution is mocked (no K8s cluster / Ollama required).
"""
from __future__ import annotations
import asyncio
from unittest.mock import patch

import pytest

from api.models import SessionStatus
from api.session_store import store as _store
from tests.integration.api.conftest import (
    COMPLETED_STATE, REVIEW_PAYLOAD,
    _make_run_graph, _wait_for_status,
)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Health
# ═══════════════════════════════════════════════════════════════════════════════

async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Session creation
# ═══════════════════════════════════════════════════════════════════════════════

async def test_create_session_returns_uuid(client):
    r = await client.post("/api/v1/sessions")
    assert r.status_code == 201
    body = r.json()
    assert "session_id" in body
    assert len(body["session_id"]) == 36   # UUID4 format


async def test_create_multiple_sessions_unique_ids(client):
    ids = {(await client.post("/api/v1/sessions")).json()["session_id"] for _ in range(3)}
    assert len(ids) == 3


async def test_get_state_unknown_session_returns_404(client):
    r = await client.get("/api/v1/sessions/does-not-exist/state")
    assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Run — state shape and signal content
# ═══════════════════════════════════════════════════════════════════════════════

async def test_run_session_returns_running_status(client, session_id):
    with patch("api.routes.sessions._run_graph", side_effect=_make_run_graph(SessionStatus.COMPLETED)):
        r = await client.post(f"/api/v1/sessions/{session_id}/run",
                              json={"query": "pods crashlooping", "namespaces": ["production"]})
    assert r.status_code == 200
    body = r.json()
    assert body["session_id"] == session_id
    assert body["status"] in (SessionStatus.RUNNING, SessionStatus.COMPLETED)


async def test_completed_state_has_full_signal_content(client, completed_session):
    r = await client.get(f"/api/v1/sessions/{completed_session}/state")
    assert r.status_code == 200
    body = r.json()

    assert body["status"] == SessionStatus.COMPLETED
    assert body["confidence"] == "HIGH"

    # K8s events
    assert len(body["events"]) >= 1
    assert any("BackOff" in e for e in body["events"])

    # OTel traces
    assert len(body["traces"]) >= 1
    assert any("timeout" in t for t in body["traces"])

    # Prometheus alerts
    assert len(body["alerts"]) >= 1
    assert any("CrashLooping" in a for a in body["alerts"])

    # Anchor fixes (drift remediation commands)
    assert len(body["anchor_fixes"]) >= 1
    assert any("helm upgrade" in f for f in body["anchor_fixes"])

    # Policy violations
    assert len(body["policy_violations"]) >= 1
    assert any("memory limit" in v for v in body["policy_violations"])

    # Causal chain
    assert len(body["causal_chain"]) >= 1

    # Suggestions (remediation commands)
    assert len(body["suggestions"]) >= 1
    assert any("kubectl" in s for s in body["suggestions"])


async def test_run_double_start_returns_409(client, session_id):
    """Starting a RUNNING session must be rejected."""
    # Force status to RUNNING without mock finishing
    sess = _store.get(session_id)
    sess.status = SessionStatus.RUNNING

    r = await client.post(f"/api/v1/sessions/{session_id}/run",
                          json={"query": "anything"})
    assert r.status_code == 409


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Edge log — routing decisions with reasons
# ═══════════════════════════════════════════════════════════════════════════════

async def test_edge_log_present_and_structured(client, completed_session):
    r = await client.get(f"/api/v1/sessions/{completed_session}/state")
    body = r.json()

    assert len(body["edge_log"]) >= 2

    first = body["edge_log"][0]
    assert first["router"] == "confidence"
    assert first["edge_taken"] == "retry"
    assert "LOW" in first["reason"]
    assert "prometheus" in first["reason"]          # ingestion KO visible
    assert "snapshot" in first
    assert first["snapshot"]["ingestion_failures"] == ["prometheus"]

    second = body["edge_log"][1]
    assert second["edge_taken"] == "review"
    assert "HIGH" in second["reason"]


async def test_edge_log_exposes_ingestion_kos(client, completed_session):
    """ingestion_stats fallback must be echoed in the first LOW-confidence edge reason."""
    r = await client.get(f"/api/v1/sessions/{completed_session}/state")
    body = r.json()

    assert body["ingestion_stats"]["prometheus"]["fallback"] is True
    assert body["ingestion_stats"]["prometheus"]["error"] == "connection refused"

    low_edges = [e for e in body["edge_log"] if e["edge_taken"] == "retry"]
    assert low_edges, "expected at least one retry edge"
    assert "prometheus" in low_edges[0]["reason"]


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Human-in-the-loop — AWAITING_REVIEW + feedback
# ═══════════════════════════════════════════════════════════════════════════════

async def test_awaiting_review_state_has_payload(client, awaiting_review_session):
    r = await client.get(f"/api/v1/sessions/{awaiting_review_session}/state")
    body = r.json()

    assert body["status"] == SessionStatus.AWAITING_REVIEW
    assert body["review_payload"] is not None
    assert "remediation" in body["review_payload"]
    assert body["review_payload"]["no_solution"] is False


async def test_feedback_approve_resumes_session(client, awaiting_review_session):
    with patch("api.routes.sessions._run_graph",
               side_effect=_make_run_graph(SessionStatus.COMPLETED)):
        r = await client.post(
            f"/api/v1/sessions/{awaiting_review_session}/feedback",
            json={"human_decision": "approve"},
        )
    assert r.status_code == 200
    await _wait_for_status(client, awaiting_review_session, SessionStatus.COMPLETED)

    r2 = await client.get(f"/api/v1/sessions/{awaiting_review_session}/state")
    assert r2.json()["status"] == SessionStatus.COMPLETED


async def test_feedback_reject_ends_session(client, awaiting_review_session):
    with patch("api.routes.sessions._run_graph",
               side_effect=_make_run_graph(SessionStatus.FAILED,
                                           state_override={"error": "operator rejected"})):
        r = await client.post(
            f"/api/v1/sessions/{awaiting_review_session}/feedback",
            json={"human_decision": "reject"},
        )
    assert r.status_code == 200
    await _wait_for_status(client, awaiting_review_session, SessionStatus.FAILED, SessionStatus.COMPLETED)

    r2 = await client.get(f"/api/v1/sessions/{awaiting_review_session}/state")
    assert r2.json()["status"] in (SessionStatus.FAILED, SessionStatus.COMPLETED)


async def test_feedback_on_running_session_returns_409(client, session_id):
    sess = _store.get(session_id)
    sess.status = SessionStatus.RUNNING

    r = await client.post(f"/api/v1/sessions/{session_id}/feedback",
                          json={"human_decision": "approve"})
    assert r.status_code == 409


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Extra context injection — re-run after failure
# ═══════════════════════════════════════════════════════════════════════════════

async def test_extra_context_reruns_session(client, completed_session):
    """POST /feedback with extra_context on a COMPLETED session triggers a new run."""
    with patch("api.routes.sessions._run_graph",
               side_effect=_make_run_graph(SessionStatus.COMPLETED)):
        r = await client.post(
            f"/api/v1/sessions/{completed_session}/feedback",
            json={"extra_context": "focus on networking — not a memory issue"},
        )
    assert r.status_code == 200
    await _wait_for_status(client, completed_session, SessionStatus.COMPLETED)

    r2 = await client.get(f"/api/v1/sessions/{completed_session}/state")
    assert r2.json()["status"] == SessionStatus.COMPLETED


async def test_extra_context_missing_on_completed_returns_400(client, completed_session):
    r = await client.post(f"/api/v1/sessions/{completed_session}/feedback",
                          json={"human_decision": "approve"})
    assert r.status_code == 400


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Reasoning history
# ═══════════════════════════════════════════════════════════════════════════════

async def test_reasoning_history_surfaced(client, completed_session):
    r = await client.get(f"/api/v1/sessions/{completed_session}/state")
    body = r.json()

    assert len(body["reasoning_history"]) >= 1
    entry = body["reasoning_history"][0]
    assert entry["step"] == 1
    assert "confidence" in entry
    assert entry["confidence"] == "LOW"
    assert "hypothesis" in entry


# ═══════════════════════════════════════════════════════════════════════════════
# 8. Delete session
# ═══════════════════════════════════════════════════════════════════════════════

async def test_delete_session(client, completed_session):
    r = await client.delete(f"/api/v1/sessions/{completed_session}")
    assert r.status_code == 204

    r2 = await client.get(f"/api/v1/sessions/{completed_session}/state")
    assert r2.status_code == 404


async def test_delete_unknown_session_returns_404(client):
    r = await client.delete("/api/v1/sessions/ghost-session")
    assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# 9. Dry-run results
# ═══════════════════════════════════════════════════════════════════════════════

async def test_dry_run_results_in_state(client, completed_session):
    r = await client.get(f"/api/v1/sessions/{completed_session}/state")
    body = r.json()

    assert len(body["dry_run_results"]) >= 1
    dr = body["dry_run_results"][0]
    assert "dry_cmd" in dr
    assert "exit_code" in dr
    assert dr["exit_code"] == 0


# ═══════════════════════════════════════════════════════════════════════════════
# 10. OpenAPI schema integrity
# ═══════════════════════════════════════════════════════════════════════════════

async def test_openapi_schema_has_all_routes(client):
    r = await client.get("/openapi.json")
    assert r.status_code == 200
    paths = r.json()["paths"]
    assert "/health" in paths
    assert "/api/v1/sessions" in paths
    assert "/api/v1/sessions/{session_id}/run" in paths
    assert "/api/v1/sessions/{session_id}/feedback" in paths
    assert "/api/v1/sessions/{session_id}/state" in paths
    assert "/api/v1/sessions/{session_id}/stream" in paths


# ═══════════════════════════════════════════════════════════════════════════════
# 11. IDLE state immediately after creation
# ═══════════════════════════════════════════════════════════════════════════════

async def test_session_idle_state_after_create(client):
    """A freshly-created session must be IDLE with no query or confidence set."""
    r = await client.post("/api/v1/sessions")
    sid = r.json()["session_id"]

    r2 = await client.get(f"/api/v1/sessions/{sid}/state")
    assert r2.status_code == 200
    body = r2.json()

    assert body["status"] == SessionStatus.IDLE
    assert body["query"] is None
    assert body["confidence"] is None
    assert body["report"] is None
    assert body["edge_log"] == []


# ═══════════════════════════════════════════════════════════════════════════════
# 12. Run — minimal body (query-only, no namespaces / kubeconfig)
# ═══════════════════════════════════════════════════════════════════════════════

async def test_run_minimal_request_body(client, session_id):
    """POST /run with only the 'query' field must be accepted — all optional fields default."""
    with patch("api.routes.sessions._run_graph", side_effect=_make_run_graph(SessionStatus.COMPLETED)):
        r = await client.post(
            f"/api/v1/sessions/{session_id}/run",
            json={"query": "why is my deployment failing"},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["query"] == "why is my deployment failing"
    assert isinstance(body["candidate_paths"], list)


# ═══════════════════════════════════════════════════════════════════════════════
# 13. FAILED session — error field is populated
# ═══════════════════════════════════════════════════════════════════════════════

async def test_failed_session_exposes_error_field(client, session_id):
    """When the workflow raises, the session must reach FAILED and expose the error message."""
    async def _crash(session, initial_state, resume_cmd=None):
        await asyncio.sleep(0)
        session.error = "simulated workflow crash"
        session.status = SessionStatus.FAILED

    with patch("api.routes.sessions._run_graph", side_effect=_crash):
        await client.post(
            f"/api/v1/sessions/{session_id}/run",
            json={"query": "crashing"},
        )
    await _wait_for_status(client, session_id, SessionStatus.FAILED)

    r = await client.get(f"/api/v1/sessions/{session_id}/state")
    body = r.json()
    assert body["status"] == SessionStatus.FAILED
    assert body["error"] == "simulated workflow crash"


# ═══════════════════════════════════════════════════════════════════════════════
# 14. DELETE cancels a running background task
# ═══════════════════════════════════════════════════════════════════════════════

async def test_delete_running_session_cancels_task(client, session_id):
    """Deleting a RUNNING session must cancel its asyncio Task and return 204."""
    cancelled = asyncio.Event()

    async def _slow_run(session, initial_state, resume_cmd=None):
        session.status = SessionStatus.RUNNING
        try:
            await asyncio.sleep(60)   # blocks until cancelled
        except asyncio.CancelledError:
            cancelled.set()
            raise

    with patch("api.routes.sessions._run_graph", side_effect=_slow_run):
        await client.post(f"/api/v1/sessions/{session_id}/run", json={"query": "slow"})

    # Give the background task a tick to start
    await asyncio.sleep(0.05)

    r = await client.delete(f"/api/v1/sessions/{session_id}")
    assert r.status_code == 204

    # Task must have been cancelled
    await asyncio.wait_for(cancelled.wait(), timeout=1.0)

    # Session no longer reachable
    r2 = await client.get(f"/api/v1/sessions/{session_id}/state")
    assert r2.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# 15. Feedback on IDLE session returns 409
# ═══════════════════════════════════════════════════════════════════════════════

async def test_feedback_on_idle_session_returns_409(client, session_id):
    """A fresh (IDLE) session must reject feedback with 409 — nothing to resume."""
    r = await client.post(
        f"/api/v1/sessions/{session_id}/feedback",
        json={"human_decision": "approve"},
    )
    assert r.status_code == 409


# ═══════════════════════════════════════════════════════════════════════════════
# 16. SSE stream — content-type and basic event shape
# ═══════════════════════════════════════════════════════════════════════════════

async def test_sse_stream_returns_event_stream(client, completed_session):
    """GET /stream must respond with Content-Type: text/event-stream and emit events."""
    async def _fake_astream(*_args, **_kwargs):
        # Yield one synthetic state update then stop
        yield {"query": "pods crashlooping", "confidence": "HIGH"}

    with patch("api.routes.sessions._graph") as mock_graph:
        mock_graph.astream = _fake_astream
        async with client.stream("GET", f"/api/v1/sessions/{completed_session}/stream") as r:
            assert "text/event-stream" in r.headers.get("content-type", "")
            chunks: list[str] = []
            async for line in r.aiter_lines():
                if line.startswith("data:"):
                    chunks.append(line[5:].strip())
                    if '"done"' in line:
                        break
            assert len(chunks) >= 2   # at least one state event + done sentinel
            assert any("done" in c for c in chunks)
