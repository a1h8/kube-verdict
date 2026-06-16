"""Per-identity JWT / OIDC bearer auth (api/oidc.py)."""
from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

import config as cfg
from api import oidc

KID = "test-key-1"


@pytest.fixture
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    # default: OIDC off
    monkeypatch.setattr(cfg, "OIDC_JWKS_URL", None)
    monkeypatch.setattr(cfg, "OIDC_ISSUER", None)
    monkeypatch.setattr(cfg, "OIDC_AUDIENCE", None)
    monkeypatch.setattr(cfg, "OIDC_REQUIRED", False)
    monkeypatch.setattr(cfg, "API_TOKEN", None)
    oidc._jwks_cache["keys"] = None
    oidc._jwks_cache["fetched_at"] = 0.0


def _make_token(key, *, claims=None, kid=KID, alg="RS256"):
    payload = {"sub": "user-123", "exp": int(time.time()) + 300, **(claims or {})}
    return jwt.encode(payload, key, algorithm=alg, headers={"kid": kid})


def _stub_jwks(monkeypatch, key):
    """Make _signing_key return our public key for KID without network."""
    pub = key.public_key()

    def fake_signing_key(token, jwks_url):
        if jwt.get_unverified_header(token).get("kid") == KID:
            return pub
        raise HTTPException(status_code=401, detail="kid not found")

    monkeypatch.setattr(oidc, "_signing_key", fake_signing_key)


# ── verify_jwt ────────────────────────────────────────────────────────────────
def test_verify_jwt_valid(monkeypatch, rsa_key):
    monkeypatch.setattr(cfg, "OIDC_JWKS_URL", "https://idp/.well-known/jwks.json")
    _stub_jwks(monkeypatch, rsa_key)
    claims = oidc.verify_jwt(_make_token(rsa_key))
    assert claims["sub"] == "user-123"


def test_verify_jwt_expired_rejected(monkeypatch, rsa_key):
    monkeypatch.setattr(cfg, "OIDC_JWKS_URL", "https://idp/jwks")
    _stub_jwks(monkeypatch, rsa_key)
    token = _make_token(rsa_key, claims={"exp": int(time.time()) - 10})
    with pytest.raises(HTTPException) as e:
        oidc.verify_jwt(token)
    assert e.value.status_code == 401


def test_verify_jwt_bad_audience_rejected(monkeypatch, rsa_key):
    monkeypatch.setattr(cfg, "OIDC_JWKS_URL", "https://idp/jwks")
    monkeypatch.setattr(cfg, "OIDC_AUDIENCE", "kube-verdict")
    _stub_jwks(monkeypatch, rsa_key)
    token = _make_token(rsa_key, claims={"aud": "someone-else"})
    with pytest.raises(HTTPException) as e:
        oidc.verify_jwt(token)
    assert e.value.status_code == 401


# ── require_auth layering ─────────────────────────────────────────────────────
def test_require_auth_jwt_returns_identity(monkeypatch, rsa_key):
    monkeypatch.setattr(cfg, "OIDC_JWKS_URL", "https://idp/jwks")
    _stub_jwks(monkeypatch, rsa_key)
    claims = oidc.require_auth(f"Bearer {_make_token(rsa_key)}")
    assert claims["sub"] == "user-123"


def test_require_auth_open_when_nothing_configured():
    # no OIDC, no shared secret → open (returns None)
    assert oidc.require_auth(None) is None


def test_require_auth_falls_back_to_shared_secret(monkeypatch):
    monkeypatch.setattr(cfg, "API_TOKEN", "s3cret")
    # valid shared secret passes
    assert oidc.require_auth("Bearer s3cret") is None
    # wrong secret rejected
    with pytest.raises(HTTPException) as e:
        oidc.require_auth("Bearer nope")
    assert e.value.status_code == 401


def test_require_auth_oidc_required_rejects_without_jwt(monkeypatch):
    monkeypatch.setattr(cfg, "OIDC_JWKS_URL", "https://idp/jwks")
    monkeypatch.setattr(cfg, "OIDC_REQUIRED", True)
    with pytest.raises(HTTPException) as e:
        oidc.require_auth(None)
    assert e.value.status_code == 401


def test_require_auth_oidc_required_rejects_shared_secret(monkeypatch):
    monkeypatch.setattr(cfg, "OIDC_JWKS_URL", "https://idp/jwks")
    monkeypatch.setattr(cfg, "OIDC_REQUIRED", True)
    monkeypatch.setattr(cfg, "API_TOKEN", "s3cret")
    # a non-JWT bearer is not accepted when OIDC is mandatory
    with pytest.raises(HTTPException) as e:
        oidc.require_auth("Bearer s3cret")
    assert e.value.status_code == 401
