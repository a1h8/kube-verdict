"""Integration tests for POST /api/v1/investigate and the bearer-token guard."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

import config as cfg
from tests.integration.api.conftest import COMPLETED_STATE


_FINAL_STATE = {
    **COMPLETED_STATE,
    "verdict": "HUMAN_REVIEW",
    "verdict_reasons": ["namespace 'prod' is production — always HUMAN_REVIEW minimum"],
    "blast_radius": {"risk": "MEDIUM", "rollback_available": True, "namespaces": ["prod"]},
    "edge_log": [{"router": "policy", "snapshot": {"score": 0.85}}],
}


@pytest.mark.asyncio
async def test_investigate_returns_verdict_envelope(client):
    with patch(
        "api.routes.investigate.run_investigation",
        new=AsyncMock(return_value=_FINAL_STATE),
    ):
        r = await client.post(
            "/api/v1/investigate",
            json={"service": "payment-api", "namespace": "prod",
                  "environment": "prod", "signal": "CrashLoopBackOff"},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["service"] == "payment-api"
    assert body["policy"] == "HUMAN_REVIEW"
    assert body["blast_radius"] == "MEDIUM"
    assert body["confidence_score"] == 0.85
    assert body["session_id"]
    assert body["next_steps"]


@pytest.mark.asyncio
async def test_investigate_synthesises_query_from_service_and_signal(client):
    captured = {}

    async def _capture(**kwargs):
        captured.update(kwargs)
        return _FINAL_STATE

    with patch("api.routes.investigate.run_investigation", new=_capture):
        r = await client.post(
            "/api/v1/investigate",
            json={"service": "payment-api", "namespace": "prod", "signal": "CrashLoopBackOff"},
        )
    assert r.status_code == 200
    assert "payment-api" in captured["query"]
    assert captured["namespaces"] == ["prod"]


# ── auth guard ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_auth_disabled_when_token_unset(client, monkeypatch):
    monkeypatch.setattr(cfg, "API_TOKEN", None)
    with patch("api.routes.investigate.run_investigation", new=AsyncMock(return_value=_FINAL_STATE)):
        r = await client.post("/api/v1/investigate", json={"query": "x"})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_missing_token_rejected_when_configured(client, monkeypatch):
    monkeypatch.setattr(cfg, "API_TOKEN", "s3cret")
    r = await client.post("/api/v1/investigate", json={"query": "x"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_wrong_token_rejected(client, monkeypatch):
    monkeypatch.setattr(cfg, "API_TOKEN", "s3cret")
    r = await client.post(
        "/api/v1/investigate", json={"query": "x"},
        headers={"Authorization": "Bearer nope"},
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_correct_token_accepted(client, monkeypatch):
    monkeypatch.setattr(cfg, "API_TOKEN", "s3cret")
    with patch("api.routes.investigate.run_investigation", new=AsyncMock(return_value=_FINAL_STATE)):
        r = await client.post(
            "/api/v1/investigate", json={"query": "x"},
            headers={"Authorization": "Bearer s3cret"},
        )
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_session_create_guarded_by_token(client, monkeypatch):
    monkeypatch.setattr(cfg, "API_TOKEN", "s3cret")
    assert (await client.post("/api/v1/sessions")).status_code == 401
    r = await client.post("/api/v1/sessions", headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 201
