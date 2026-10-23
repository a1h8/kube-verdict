"""The API caller never picks the kubeconfig or the context — docs/fix-plan-001.md, H73."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import config as cfg
from api.routes import investigate, sessions


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(cfg, "API_TOKEN", None)
    monkeypatch.setattr(cfg, "OIDC_JWKS_URL", None)
    monkeypatch.setattr(cfg, "OIDC_REQUIRED", False)
    app = FastAPI()
    app.include_router(sessions.router)
    app.include_router(investigate.router)
    return TestClient(app)


def _new_session(client) -> str:
    return client.post("/api/v1/sessions").json()["session_id"]


@pytest.mark.parametrize("field,value", [
    ("kubeconfig", "/root/.kube/admin.conf"),
    ("kube_context", "admin@prod"),
])
def test_run_refuses_a_caller_chosen_cluster(client, field, value):
    sid = _new_session(client)
    with patch("api.routes.sessions._run_graph", new=AsyncMock()) as run:
        r = client.post(f"/api/v1/sessions/{sid}/run", json={"query": "q", field: value})
    assert r.status_code == 422
    assert "server configuration only" in r.text
    run.assert_not_called()


def test_run_without_them_uses_the_server_configuration(client):
    # control: a plain request is accepted, and nothing in the state overrides KUBECONFIG
    sid = _new_session(client)
    with patch("api.routes.sessions._run_graph", new=AsyncMock()) as run:
        r = client.post(f"/api/v1/sessions/{sid}/run", json={"query": "q", "namespaces": ["demo"]})
    assert r.status_code == 200
    initial_state = run.call_args.args[1]
    assert "kubeconfig" not in initial_state and "kube_context" not in initial_state


def test_investigate_refuses_a_caller_chosen_context(client):
    with patch("api.routes.investigate.run_investigation", new=AsyncMock(return_value={})) as run:
        refused = client.post("/api/v1/investigate", json={"query": "q", "kube_context": "admin@prod"})
        accepted = client.post("/api/v1/investigate", json={"query": "q"})
    assert refused.status_code == 422
    assert accepted.status_code == 200
    assert run.call_count == 1
    assert "kube_context" not in run.call_args.kwargs
