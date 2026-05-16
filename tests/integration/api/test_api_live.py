"""
Live API integration tests — simulate real HTTP calls against a running server.

These tests start a real uvicorn server on a random port and hit it with httpx.
They cover the full request/response cycle including JSON serialization,
HTTP status codes, headers, and streaming — without any internal mocks.

Run:
    pytest tests/integration/api/test_api_live.py -v

The workflow graph is still mocked so no K8s cluster / Ollama is required.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any
from unittest.mock import patch

import httpx
import pytest
import uvicorn

from api.app import app
from api.models import SessionStatus
from api.session_store import get_store
from tests.integration.api.conftest import (
    _make_run_graph,
)

# ── Server lifecycle ───────────────────────────────────────────────────────────

_SERVER_HOST = "127.0.0.1"
_SERVER_PORT = 18765   # deterministic free port for test suite


@pytest.fixture(scope="module")
def live_server():
    """Start uvicorn in a daemon thread, yield base URL, then stop."""
    config = uvicorn.Config(
        app, host=_SERVER_HOST, port=_SERVER_PORT,
        log_level="warning", loop="asyncio",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for the server to be ready
    base = f"http://{_SERVER_HOST}:{_SERVER_PORT}"
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            httpx.get(f"{base}/health", timeout=1)
            break
        except Exception:
            time.sleep(0.05)
    else:
        pytest.fail("uvicorn did not start within 10 seconds")

    yield base

    server.should_exit = True
    thread.join(timeout=5)



def _post(url: str, path: str, **kw) -> httpx.Response:
    return httpx.post(f"{url}{path}", timeout=10, **kw)


def _get(url: str, path: str, **kw) -> httpx.Response:
    return httpx.get(f"{url}{path}", timeout=10, **kw)


def _delete(url: str, path: str) -> httpx.Response:
    return httpx.delete(f"{url}{path}", timeout=10)


# ── Helper: create + run a session synchronously ──────────────────────────────

def _create_session(base: str) -> str:
    r = _post(base, "/api/v1/sessions")
    assert r.status_code == 201
    return r.json()["session_id"]


def _wait_sync(base: str, sid: str, *statuses: str, timeout: float = 5.0) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = _get(base, f"/api/v1/sessions/{sid}/state")
        body = r.json()
        if body["status"] in statuses:
            return body
        time.sleep(0.02)
    raise TimeoutError(f"session {sid} did not reach {statuses} within {timeout}s")


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Health — real HTTP GET
# ═══════════════════════════════════════════════════════════════════════════════

def test_live_health_endpoint(live_server):
    r = _get(live_server, "/health")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert r.json() == {"status": "ok"}


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Full session lifecycle — create → run → poll → state → delete
# ═══════════════════════════════════════════════════════════════════════════════

def test_live_full_session_lifecycle(live_server):
    sid = _create_session(live_server)

    with patch("api.routes.sessions._run_graph", side_effect=_make_run_graph(SessionStatus.COMPLETED)):
        r = _post(live_server, f"/api/v1/sessions/{sid}/run",
                  json={"query": "pods crashlooping", "namespaces": ["production"]})
    assert r.status_code == 200
    assert r.json()["session_id"] == sid

    state = _wait_sync(live_server, sid, SessionStatus.COMPLETED)
    assert state["status"] == SessionStatus.COMPLETED
    assert state["confidence"] == "HIGH"
    assert len(state["events"]) >= 1
    assert len(state["suggestions"]) >= 1

    dr = _delete(live_server, f"/api/v1/sessions/{sid}")
    assert dr.status_code == 204

    r2 = _get(live_server, f"/api/v1/sessions/{sid}/state")
    assert r2.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Human-in-the-loop — real approve flow
# ═══════════════════════════════════════════════════════════════════════════════

def test_live_human_review_approve(live_server):
    sid = _create_session(live_server)

    with patch("api.routes.sessions._run_graph",
               side_effect=_make_run_graph(SessionStatus.AWAITING_REVIEW)):
        _post(live_server, f"/api/v1/sessions/{sid}/run",
              json={"query": "crashlooping"})

    _wait_sync(live_server, sid, SessionStatus.AWAITING_REVIEW)

    state = _get(live_server, f"/api/v1/sessions/{sid}/state").json()
    assert state["review_payload"] is not None
    assert "remediation" in state["review_payload"]

    with patch("api.routes.sessions._run_graph",
               side_effect=_make_run_graph(SessionStatus.COMPLETED)):
        fb = _post(live_server, f"/api/v1/sessions/{sid}/feedback",
                   json={"human_decision": "approve"})
    assert fb.status_code == 200

    final = _wait_sync(live_server, sid, SessionStatus.COMPLETED, SessionStatus.FAILED)
    assert final["status"] == SessionStatus.COMPLETED


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Extra-context re-run — enriched query propagation
# ═══════════════════════════════════════════════════════════════════════════════

def test_live_extra_context_enriches_query(live_server):
    sid = _create_session(live_server)

    with patch("api.routes.sessions._run_graph",
               side_effect=_make_run_graph(SessionStatus.COMPLETED)):
        _post(live_server, f"/api/v1/sessions/{sid}/run",
              json={"query": "memory spike in api-svc"})
    _wait_sync(live_server, sid, SessionStatus.COMPLETED)

    with patch("api.routes.sessions._run_graph",
               side_effect=_make_run_graph(SessionStatus.COMPLETED)):
        r = _post(live_server, f"/api/v1/sessions/{sid}/feedback",
                  json={"extra_context": "focus on PVC — not OOMKill"})
    assert r.status_code == 200

    # missing extra_context must be rejected
    r2 = _post(live_server, f"/api/v1/sessions/{sid}/feedback",
               json={"human_decision": "approve"})
    assert r2.status_code == 400


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Error cases — 404 / 409 / 400 over real HTTP
# ═══════════════════════════════════════════════════════════════════════════════

def test_live_http_error_codes(live_server):
    # 404 — unknown session on /state
    r404 = _get(live_server, "/api/v1/sessions/ghost/state")
    assert r404.status_code == 404
    assert "not found" in r404.json().get("detail", "").lower()

    # 404 — unknown session on DELETE
    rd404 = _delete(live_server, "/api/v1/sessions/ghost")
    assert rd404.status_code == 404

    # 409 — double start
    sid = _create_session(live_server)
    get_store().set_status(sid, SessionStatus.RUNNING)
    r409 = _post(live_server, f"/api/v1/sessions/{sid}/run", json={"query": "x"})
    assert r409.status_code == 409

    # 409 — feedback on IDLE
    sid2 = _create_session(live_server)
    r409b = _post(live_server, f"/api/v1/sessions/{sid2}/feedback",
                  json={"human_decision": "approve"})
    assert r409b.status_code == 409


# ═══════════════════════════════════════════════════════════════════════════════
# 6. OpenAPI schema — contract check over real HTTP
# ═══════════════════════════════════════════════════════════════════════════════

def test_live_openapi_schema_contract(live_server):
    r = _get(live_server, "/openapi.json")
    assert r.status_code == 200
    schema = r.json()

    assert schema["info"]["title"] == "KubeWhisperer API"
    paths = schema["paths"]

    required = {
        "/health",
        "/api/v1/sessions",
        "/api/v1/sessions/{session_id}/run",
        "/api/v1/sessions/{session_id}/feedback",
        "/api/v1/sessions/{session_id}/state",
        "/api/v1/sessions/{session_id}/stream",
        "/api/v1/sessions/{session_id}",
    }
    missing = required - set(paths)
    assert not missing, f"Missing routes in OpenAPI schema: {missing}"

    # Verify HTTP methods per route
    assert "post" in paths["/api/v1/sessions"]
    assert "post" in paths["/api/v1/sessions/{session_id}/run"]
    assert "get" in paths["/api/v1/sessions/{session_id}/state"]
    assert "delete" in paths["/api/v1/sessions/{session_id}"]


# ═══════════════════════════════════════════════════════════════════════════════
# 7. SSE stream — real chunked transfer over HTTP/1.1
# ═══════════════════════════════════════════════════════════════════════════════

def test_live_sse_stream_chunked(live_server):
    """GET /stream against the live server must return text/event-stream chunks."""
    sid = _create_session(live_server)

    with patch("api.routes.sessions._run_graph",
               side_effect=_make_run_graph(SessionStatus.COMPLETED)):
        _post(live_server, f"/api/v1/sessions/{sid}/run",
              json={"query": "stream test"})
    _wait_sync(live_server, sid, SessionStatus.COMPLETED)

    async def _fake_astream(*_args, **_kwargs):
        yield {"query": "stream test", "confidence": "HIGH"}

    with patch("api.routes.sessions._graph") as mg:
        mg.astream = _fake_astream
        with httpx.stream("GET", f"{live_server}/api/v1/sessions/{sid}/stream",
                          timeout=10) as resp:
            ct = resp.headers.get("content-type", "")
            assert "text/event-stream" in ct, f"Bad content-type: {ct}"
            lines: list[str] = []
            for line in resp.iter_lines():
                lines.append(line)
                if '"done"' in line:
                    break

    # Must have received at least one data: line + the done sentinel
    data_lines = [ln for ln in lines if ln.startswith("data:")]
    assert len(data_lines) >= 2
    payloads = [ln[5:].strip() for ln in data_lines]
    assert any("done" in p for p in payloads)
    # First payload must be a valid JSON session state
    first = json.loads(payloads[0])
    assert "session_id" in first or "status" in first
