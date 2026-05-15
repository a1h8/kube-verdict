"""
Shared fixtures for FastAPI integration tests.

Graph execution is mocked — no K8s cluster nor Ollama required.
"""
from __future__ import annotations
import asyncio
from typing import Any
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from api.app import app
from api.models import SessionStatus
from api.session_store import Session, store as _store

# ── canned workflow state ─────────────────────────────────────────────────────

COMPLETED_STATE: dict[str, Any] = {
    "query":       "pods crashlooping in production",
    "kube_version": "v1.28.3+k3s1",
    "confidence":  "HIGH",
    "report_dict": {
        "summary":           "Pod api-xyz is CrashLoopBackOff — PVC api-data unbound.",
        "root_cause":        "No PersistentVolume matches storage class 'standard' 10Gi.",
        "affected":          ["Pod/production/api-xyz", "PVC/production/api-data"],
        "causal_chain":      ["PVC api-data Pending", "pod cannot mount volume", "CrashLoopBackOff"],
        "remediation":       ["kubectl describe pvc api-data -n production",
                              "kubectl apply -f pv-standard-10gi.yaml"],
        "events":            ["Warning BackOff pod/api-xyz: Back-off restarting failed container"],
        "traces":            ["span error: db connection timeout in payment-svc"],
        "alerts":            ["FIRING KubePodCrashLooping: api-xyz severity=critical"],
        "anchor_fixes":      ["helm upgrade api ./chart --set persistence.size=20Gi"],
        "policy_violations": ["FAIL require-limits: container api has no memory limit"],
        "confidence":        "HIGH",
        "timestamp":         "2026-05-15T12:00:00+00:00",
    },
    "edge_log": [
        {
            "router": "confidence", "edge_taken": "retry",
            "reason": "confidence=LOW — retrying (1/2); ingestion failures: ['prometheus']",
            "snapshot": {"confidence": "LOW", "retry_count": 0, "max_retries": 2,
                         "candidates_remaining": 1, "ingestion_failures": ["prometheus"]},
            "ts": "2026-05-15T12:00:01+00:00",
        },
        {
            "router": "confidence", "edge_taken": "review",
            "reason": "confidence=HIGH — forwarding to human review",
            "snapshot": {"confidence": "HIGH", "retry_count": 1, "max_retries": 2,
                         "candidates_remaining": 0, "ingestion_failures": []},
            "ts": "2026-05-15T12:00:05+00:00",
        },
    ],
    "ingestion_stats": {
        "ingest":     {"fallback": False, "entities": 42},
        "prometheus": {"fallback": True,  "error": "connection refused"},
        "otel":       {"fallback": False, "traces": 3},
    },
    "reasoning_history": [
        {
            "step": 1, "hypothesis": "OOMKill due to memory spike",
            "confidence": "LOW", "retry_count": 2,
            "summary": "Insufficient signals — no metrics data.",
        }
    ],
    "candidate_paths":    [],
    "current_hypothesis": "PVC binding failure",
    "dry_run_results": [
        {"original_cmd": "kubectl describe pvc api-data -n production",
         "dry_cmd":       "kubectl describe pvc api-data -n production --dry-run=client",
         "output": "PersistentVolumeClaim/api-data — Pending", "exit_code": 0},
    ],
}

REVIEW_PAYLOAD: dict[str, Any] = {
    "summary":       COMPLETED_STATE["report_dict"]["summary"],
    "root_cause":    COMPLETED_STATE["report_dict"]["root_cause"],
    "remediation":   COMPLETED_STATE["report_dict"]["remediation"],
    "confidence":    "HIGH",
    "no_solution":   False,
    "paths_explored": 2,
    "dry_run_results": COMPLETED_STATE["dry_run_results"],
}


def _make_run_graph(target_status: SessionStatus, review_payload=None, state_override=None):
    """Return an async mock that directly sets session state (no real LangGraph)."""
    async def _fake_run_graph(session: Session, initial_state: dict, resume_cmd=None):
        await asyncio.sleep(0)   # yield to event loop so create_task is scheduled
        session.last_state = {**COMPLETED_STATE, **(state_override or {})}
        if target_status == SessionStatus.AWAITING_REVIEW:
            session.review_payload = review_payload or REVIEW_PAYLOAD
        session.status = target_status

    return _fake_run_graph


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clean_store():
    """Wipe the session store between tests."""
    _store._sessions.clear()
    yield
    _store._sessions.clear()


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def session_id(client):
    """Create a fresh session and return its ID."""
    r = await client.post("/api/v1/sessions")
    assert r.status_code == 201
    return r.json()["session_id"]


async def _wait_for_status(client, session_id: str, *statuses: SessionStatus, timeout: float = 3.0):
    """Poll /state until session reaches one of the expected statuses."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        r = await client.get(f"/api/v1/sessions/{session_id}/state")
        if r.json()["status"] in statuses:
            return
        await asyncio.sleep(0.02)
    raise TimeoutError(f"session {session_id} did not reach {statuses} within {timeout}s")


@pytest_asyncio.fixture
async def completed_session(client, session_id):
    """Session that has completed a full RCA run (mocked)."""
    with patch("api.routes.sessions._run_graph", side_effect=_make_run_graph(SessionStatus.COMPLETED)):
        r = await client.post(f"/api/v1/sessions/{session_id}/run",
                              json={"query": "pods crashlooping in production",
                                    "namespaces": ["production"]})
    assert r.status_code == 200
    await _wait_for_status(client, session_id, SessionStatus.COMPLETED)
    return session_id


@pytest_asyncio.fixture
async def awaiting_review_session(client, session_id):
    """Session paused at human_review interrupt."""
    with patch("api.routes.sessions._run_graph", side_effect=_make_run_graph(SessionStatus.AWAITING_REVIEW)):
        r = await client.post(f"/api/v1/sessions/{session_id}/run",
                              json={"query": "pods crashlooping in production"})
    assert r.status_code == 200
    await _wait_for_status(client, session_id, SessionStatus.AWAITING_REVIEW)
    return session_id
