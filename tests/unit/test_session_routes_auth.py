"""Every session route is behind the auth gate — docs/fix-plan-001.md, H16."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import config as cfg
from api.oidc import require_auth
from api.routes import sessions


def _guarded(route) -> bool:
    return any(d.call is require_auth for d in route.dependant.dependencies)


def test_every_session_route_depends_on_require_auth():
    unguarded = [f"{sorted(r.methods)} {r.path}" for r in sessions.router.routes if not _guarded(r)]
    assert unguarded == []


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(cfg, "API_TOKEN", "s3cret")
    monkeypatch.setattr(cfg, "OIDC_JWKS_URL", None)
    monkeypatch.setattr(cfg, "OIDC_REQUIRED", False)
    app = FastAPI()
    app.include_router(sessions.router)
    return TestClient(app)


@pytest.mark.parametrize("method,path", [
    ("get", "/api/v1/sessions/x/state"),
    ("get", "/api/v1/sessions/x/stream"),
    ("delete", "/api/v1/sessions/x"),
])
def test_without_the_token_the_route_answers_401(client, method, path):
    assert getattr(client, method)(path).status_code == 401
    # control: with the token the request reaches the route (unknown session → 404)
    assert getattr(client, method)(path, headers={"Authorization": "Bearer s3cret"}).status_code == 404


def test_the_approver_names_the_oidc_subject_or_the_gate(monkeypatch):
    monkeypatch.setattr(cfg, "API_TOKEN", None)
    assert sessions._approver({"iss": "https://idp/", "sub": "alice"}) == "oidc:https://idp/#alice"
    assert sessions._approver(None) == "anonymous"
    monkeypatch.setattr(cfg, "API_TOKEN", "s3cret")
    assert sessions._approver(None) == "shared-token"
