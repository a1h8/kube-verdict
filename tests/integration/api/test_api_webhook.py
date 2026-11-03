"""
Integration tests — Alertmanager webhook endpoint.

POST /api/v1/webhook/alertmanager
  → firing alerts → sessions created + RCA started
  → resolved alerts → skipped

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
    return {
        "version": "4",
        "status": "firing",
        "receiver": "kubeverdict",
        "groupLabels": {"alertname": "PodCrashLooping"},
        "commonLabels": {},
        "commonAnnotations": {},
        "externalURL": "http://alertmanager:9093",
        "groupKey": "{}:{alertname='PodCrashLooping'}",
        "alerts": alerts,
    }


def _firing(alertname="PodCrashLooping", namespace="production", **extra) -> dict:
    return {
        "status": "firing",
        "labels": {"alertname": alertname, "namespace": namespace, **extra},
        "annotations": {},
        "startsAt": "2026-05-22T10:00:00Z",
        "endsAt": "0001-01-01T00:00:00Z",
        "generatorURL": "",
        "fingerprint": "abc123",
    }


def _resolved(**labels) -> dict:
    return {**_firing(**labels), "status": "resolved"}


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Basic response shape
# ═══════════════════════════════════════════════════════════════════════════════

async def test_webhook_returns_202(client):
    with patch("api.routes.sessions._run_graph", side_effect=_make_run_graph(SessionStatus.COMPLETED)):
        r = await client.post("/api/v1/webhook/alertmanager", json=_payload([_firing()]))
    assert r.status_code == 202


async def test_webhook_response_has_session_ids_and_skipped(client):
    with patch("api.routes.sessions._run_graph", side_effect=_make_run_graph(SessionStatus.COMPLETED)):
        r = await client.post("/api/v1/webhook/alertmanager", json=_payload([_firing()]))
    body = r.json()
    assert "session_ids" in body
    assert "skipped" in body


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Firing alerts → sessions created
# ═══════════════════════════════════════════════════════════════════════════════

async def test_single_firing_alert_creates_one_session(client):
    with patch("api.routes.sessions._run_graph", side_effect=_make_run_graph(SessionStatus.COMPLETED)):
        r = await client.post("/api/v1/webhook/alertmanager", json=_payload([_firing()]))
    body = r.json()
    assert len(body["session_ids"]) == 1
    assert body["skipped"] == 0


async def test_multiple_firing_alerts_create_multiple_sessions(client):
    alerts = [_firing("PodCrashLooping"), _firing("ImagePullBackOff"), _firing("OOMKilled")]
    with patch("api.routes.sessions._run_graph", side_effect=_make_run_graph(SessionStatus.COMPLETED)):
        r = await client.post("/api/v1/webhook/alertmanager", json=_payload(alerts))
    body = r.json()
    assert len(body["session_ids"]) == 3
    assert len(set(body["session_ids"])) == 3   # all unique


async def test_session_is_retrievable_after_webhook(client):
    with patch("api.routes.sessions._run_graph", side_effect=_make_run_graph(SessionStatus.COMPLETED)):
        r = await client.post("/api/v1/webhook/alertmanager", json=_payload([_firing()]))
    session_id = r.json()["session_ids"][0]

    await _wait_for_status(client, session_id, SessionStatus.COMPLETED)
    state = (await client.get(f"/api/v1/sessions/{session_id}/state")).json()
    assert state["session_id"] == session_id
    assert state["status"] == SessionStatus.COMPLETED


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Resolved alerts → skipped
# ═══════════════════════════════════════════════════════════════════════════════

async def test_resolved_only_payload_creates_no_sessions(client):
    with patch("api.routes.sessions._run_graph", side_effect=_make_run_graph(SessionStatus.COMPLETED)):
        r = await client.post("/api/v1/webhook/alertmanager",
                              json=_payload([_resolved(), _resolved()]))
    body = r.json()
    assert body["session_ids"] == []
    assert body["skipped"] == 2


async def test_mixed_payload_only_fires_on_firing_alerts(client):
    alerts = [_firing("PodCrashLooping"), _resolved(), _firing("OOMKilled"), _resolved()]
    with patch("api.routes.sessions._run_graph", side_effect=_make_run_graph(SessionStatus.COMPLETED)):
        r = await client.post("/api/v1/webhook/alertmanager", json=_payload(alerts))
    body = r.json()
    assert len(body["session_ids"]) == 2
    assert body["skipped"] == 2


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Query and namespace mapping
# ═══════════════════════════════════════════════════════════════════════════════

async def test_query_contains_alertname(client):
    with patch("api.routes.sessions._run_graph", side_effect=_make_run_graph_preserve_query()):
        r = await client.post("/api/v1/webhook/alertmanager",
                              json=_payload([_firing("KubePodCrashLooping", "checkout")]))
    session_id = r.json()["session_ids"][0]

    await _wait_for_status(client, session_id, SessionStatus.COMPLETED)
    state = (await client.get(f"/api/v1/sessions/{session_id}/state")).json()
    assert "KubePodCrashLooping" in state["query"]


async def test_namespace_is_extracted_from_alert_labels(client):
    with patch("api.routes.sessions._run_graph", side_effect=_make_run_graph_preserve_query()):
        r = await client.post("/api/v1/webhook/alertmanager",
                              json=_payload([_firing(namespace="payments")]))
    session_id = r.json()["session_ids"][0]

    await _wait_for_status(client, session_id, SessionStatus.COMPLETED)
    state = (await client.get(f"/api/v1/sessions/{session_id}/state")).json()
    assert "payments" in state["query"]


async def test_deployment_label_included_in_query(client):
    alert = {**_firing("PodCrashLooping", "prod"), "labels": {
        "alertname": "PodCrashLooping", "namespace": "prod", "deployment": "payment-service",
    }}
    with patch("api.routes.sessions._run_graph", side_effect=_make_run_graph_preserve_query()):
        r = await client.post("/api/v1/webhook/alertmanager", json=_payload([alert]))
    session_id = r.json()["session_ids"][0]

    await _wait_for_status(client, session_id, SessionStatus.COMPLETED)
    state = (await client.get(f"/api/v1/sessions/{session_id}/state")).json()
    assert "payment-service" in state["query"]


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Empty payload
# ═══════════════════════════════════════════════════════════════════════════════

async def test_empty_alerts_list_returns_no_sessions(client):
    with patch("api.routes.sessions._run_graph", side_effect=_make_run_graph(SessionStatus.COMPLETED)):
        r = await client.post("/api/v1/webhook/alertmanager", json=_payload([]))
    body = r.json()
    assert r.status_code == 202
    assert body["session_ids"] == []
    assert body["skipped"] == 0


# ── H53: one running investigation per alert, and a global cap ────────────────

def _blocking_run_graph():
    """A run that stays in flight until the test releases it."""
    release = asyncio.Event()

    async def _fake(session: Session, initial_state: dict, resume_cmd=None):
        await release.wait()

    return _fake, release


async def test_a_repeated_alert_starts_one_investigation(client):
    fake, release = _blocking_run_graph()
    with patch("api.routes.sessions._run_graph", fake):
        r1 = await client.post("/api/v1/webhook/alertmanager", json=_payload([_firing(), _firing()]))
        r2 = await client.post("/api/v1/webhook/alertmanager", json=_payload([_firing()]))
        release.set()
    assert len(r1.json()["session_ids"]) == 1 and r1.json()["skipped"] == 1
    assert r2.json()["session_ids"] == [] and r2.json()["skipped"] == 1


async def test_the_same_alert_in_another_namespace_is_its_own_investigation(client):
    fake, release = _blocking_run_graph()
    with patch("api.routes.sessions._run_graph", fake):
        r = await client.post("/api/v1/webhook/alertmanager",
                              json=_payload([_firing(namespace="a"), _firing(namespace="b")]))
        release.set()
    assert len(r.json()["session_ids"]) == 2


async def test_a_finished_investigation_frees_its_alert(client):
    with patch("api.routes.sessions._run_graph", _make_run_graph_preserve_query()):
        r1 = await client.post("/api/v1/webhook/alertmanager", json=_payload([_firing()]))
        await _wait_for_status(client, r1.json()["session_ids"][0], SessionStatus.COMPLETED)
        await asyncio.sleep(0)
        r2 = await client.post("/api/v1/webhook/alertmanager", json=_payload([_firing()]))
    assert len(r2.json()["session_ids"]) == 1


async def test_an_alert_storm_is_capped(client, monkeypatch):
    import config
    monkeypatch.setattr(config, "WEBHOOK_MAX_RUNNING", 2)
    fake, release = _blocking_run_graph()
    with patch("api.routes.sessions._run_graph", fake):
        r = await client.post("/api/v1/webhook/alertmanager",
                              json=_payload([_firing(f"Alert{i}") for i in range(5)]))
        release.set()
    assert len(r.json()["session_ids"]) == 2 and r.json()["skipped"] == 3
