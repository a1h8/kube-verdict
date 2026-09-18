"""
Integration tests — pushed signal webhook endpoint.

POST /api/v1/webhook/signal
  → anomalous signals (severity != normal) → sessions created + RCA started
  → normal-severity signals → skipped

No K8s cluster or Ollama required — _run_graph is mocked.
"""
from __future__ import annotations
import asyncio
from unittest.mock import patch

from api.models import SessionStatus
from api.session_store import Session
from tests.integration.api.conftest import COMPLETED_STATE, _make_run_graph, _wait_for_status


def _make_run_graph_preserve_query():
    """Like _make_run_graph(COMPLETED) but keeps the query set by the caller."""
    import api.session_store as _ss_mod

    async def _fake(session: Session, initial_state: dict, resume_cmd=None):
        await asyncio.sleep(0)
        merged = {**COMPLETED_STATE, "query": initial_state.get("query", "")}
        _ss_mod.get_store().set_last_state(session.session_id, merged)
        _ss_mod.get_store().set_status(session.session_id, SessionStatus.COMPLETED)

    return _fake


# ── helpers ───────────────────────────────────────────────────────────────────

def _payload(alerts: list[dict]) -> dict:
    return {"alerts": alerts}


def _anomaly(severity="critical", entity_uid="Pod/production/api-7c9", metric_name="cpu_usage", **labels) -> dict:
    return {
        "entity_uid": entity_uid,
        "metric_name": metric_name,
        "ts": 1_700_000_000_000,
        "severity": severity,
        "score": 3.2,
        "method": "patchtst",
        "horizon": "short",
        "n_points": 64,
        "labels": labels,
        "text": "",
    }


def _normal(**kwargs) -> dict:
    return _anomaly(severity="normal", **kwargs)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Basic response shape
# ═══════════════════════════════════════════════════════════════════════════════

async def test_webhook_returns_202(client):
    with patch("api.routes.sessions._run_graph", side_effect=_make_run_graph(SessionStatus.COMPLETED)):
        r = await client.post("/api/v1/webhook/signal", json=_payload([_anomaly()]))
    assert r.status_code == 202


async def test_webhook_response_has_session_ids_and_skipped(client):
    with patch("api.routes.sessions._run_graph", side_effect=_make_run_graph(SessionStatus.COMPLETED)):
        r = await client.post("/api/v1/webhook/signal", json=_payload([_anomaly()]))
    body = r.json()
    assert "session_ids" in body
    assert "skipped" in body


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Anomalous signals → sessions created
# ═══════════════════════════════════════════════════════════════════════════════

async def test_single_anomalous_signal_creates_one_session(client):
    with patch("api.routes.sessions._run_graph", side_effect=_make_run_graph(SessionStatus.COMPLETED)):
        r = await client.post("/api/v1/webhook/signal", json=_payload([_anomaly()]))
    body = r.json()
    assert len(body["session_ids"]) == 1
    assert body["skipped"] == 0


async def test_multiple_anomalous_signals_create_multiple_sessions(client):
    signals = [_anomaly(metric_name="cpu_usage"), _anomaly(metric_name="memory_bytes"), _anomaly(metric_name="restart_count")]
    with patch("api.routes.sessions._run_graph", side_effect=_make_run_graph(SessionStatus.COMPLETED)):
        r = await client.post("/api/v1/webhook/signal", json=_payload(signals))
    body = r.json()
    assert len(body["session_ids"]) == 3
    assert len(set(body["session_ids"])) == 3   # all unique


async def test_session_is_retrievable_after_webhook(client):
    with patch("api.routes.sessions._run_graph", side_effect=_make_run_graph(SessionStatus.COMPLETED)):
        r = await client.post("/api/v1/webhook/signal", json=_payload([_anomaly()]))
    session_id = r.json()["session_ids"][0]

    await _wait_for_status(client, session_id, SessionStatus.COMPLETED)
    state = (await client.get(f"/api/v1/sessions/{session_id}/state")).json()
    assert state["session_id"] == session_id
    assert state["status"] == SessionStatus.COMPLETED


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Normal-severity signals → skipped
# ═══════════════════════════════════════════════════════════════════════════════

async def test_normal_only_payload_creates_no_sessions(client):
    with patch("api.routes.sessions._run_graph", side_effect=_make_run_graph(SessionStatus.COMPLETED)):
        r = await client.post("/api/v1/webhook/signal", json=_payload([_normal(), _normal()]))
    body = r.json()
    assert body["session_ids"] == []
    assert body["skipped"] == 2


async def test_mixed_payload_only_fires_on_anomalous_signals(client):
    signals = [_anomaly(metric_name="cpu_usage"), _normal(), _anomaly(metric_name="memory_bytes"), _normal()]
    with patch("api.routes.sessions._run_graph", side_effect=_make_run_graph(SessionStatus.COMPLETED)):
        r = await client.post("/api/v1/webhook/signal", json=_payload(signals))
    body = r.json()
    assert len(body["session_ids"]) == 2
    assert body["skipped"] == 2


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Query and namespace mapping
# ═══════════════════════════════════════════════════════════════════════════════

async def test_query_contains_metric_name(client):
    with patch("api.routes.sessions._run_graph", side_effect=_make_run_graph_preserve_query()):
        r = await client.post("/api/v1/webhook/signal", json=_payload([_anomaly(metric_name="restart_count")]))
    session_id = r.json()["session_ids"][0]

    await _wait_for_status(client, session_id, SessionStatus.COMPLETED)
    state = (await client.get(f"/api/v1/sessions/{session_id}/state")).json()
    assert "restart_count" in state["query"]


async def test_namespace_is_extracted_from_signal_labels(client):
    with patch("api.routes.sessions._run_graph", side_effect=_make_run_graph_preserve_query()):
        r = await client.post("/api/v1/webhook/signal", json=_payload([_anomaly(namespace="payments")]))
    session_id = r.json()["session_ids"][0]

    await _wait_for_status(client, session_id, SessionStatus.COMPLETED)
    state = (await client.get(f"/api/v1/sessions/{session_id}/state")).json()
    assert "payments" in state["query"]


async def test_pod_label_included_in_query(client):
    signal = _anomaly(namespace="prod", pod="api-7c9")
    with patch("api.routes.sessions._run_graph", side_effect=_make_run_graph_preserve_query()):
        r = await client.post("/api/v1/webhook/signal", json=_payload([signal]))
    session_id = r.json()["session_ids"][0]

    await _wait_for_status(client, session_id, SessionStatus.COMPLETED)
    state = (await client.get(f"/api/v1/sessions/{session_id}/state")).json()
    assert "pod/api-7c9" in state["query"]


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Empty payload
# ═══════════════════════════════════════════════════════════════════════════════

async def test_empty_alerts_list_returns_no_sessions(client):
    with patch("api.routes.sessions._run_graph", side_effect=_make_run_graph(SessionStatus.COMPLETED)):
        r = await client.post("/api/v1/webhook/signal", json=_payload([]))
    body = r.json()
    assert r.status_code == 202
    assert body["session_ids"] == []
    assert body["skipped"] == 0
