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
    _oidc_on(monkeypatch)
    _stub_jwks(monkeypatch, rsa_key)
    claims = oidc.verify_jwt(_make_token(rsa_key, claims={"iss": "https://idp/", "aud": "kv"}))
    assert claims["sub"] == "user-123"


def test_verify_jwt_expired_rejected(monkeypatch, rsa_key):
    _oidc_on(monkeypatch)
    _stub_jwks(monkeypatch, rsa_key)
    token = _make_token(rsa_key, claims={"exp": int(time.time()) - 10,
                                         "iss": "https://idp/", "aud": "kv"})
    with pytest.raises(HTTPException) as e:
        oidc.verify_jwt(token)
    assert e.value.status_code == 401


def test_verify_jwt_bad_audience_rejected(monkeypatch, rsa_key):
    _oidc_on(monkeypatch)
    _stub_jwks(monkeypatch, rsa_key)
    token = _make_token(rsa_key, claims={"iss": "https://idp/", "aud": "someone-else"})
    with pytest.raises(HTTPException) as e:
        oidc.verify_jwt(token)
    assert e.value.status_code == 401


# ── require_auth layering ─────────────────────────────────────────────────────
def test_require_auth_jwt_returns_identity(monkeypatch, rsa_key):
    _oidc_on(monkeypatch)
    _stub_jwks(monkeypatch, rsa_key)
    claims = oidc.require_auth(
        f"Bearer {_make_token(rsa_key, claims={'iss': 'https://idp/', 'aud': 'kv'})}")
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


# ── docs/fix-plan-001.md, H44 ─────────────────────────────────────────────────
@pytest.mark.parametrize("issuer,audience", [(None, None), ("https://idp/", None), (None, "kv")])
def test_a_jwt_is_refused_without_issuer_and_audience(monkeypatch, rsa_key, issuer, audience):
    monkeypatch.setattr(cfg, "OIDC_JWKS_URL", "https://idp/jwks")
    monkeypatch.setattr(cfg, "OIDC_ISSUER", issuer)
    monkeypatch.setattr(cfg, "OIDC_AUDIENCE", audience)
    _stub_jwks(monkeypatch, rsa_key)
    token = _make_token(rsa_key, claims={"iss": "https://idp/", "aud": "other-app"})
    with pytest.raises(HTTPException) as e:
        oidc.verify_jwt(token)
    assert e.value.status_code == 500


def test_a_token_for_another_application_is_refused(monkeypatch, rsa_key):
    _oidc_on(monkeypatch)
    _stub_jwks(monkeypatch, rsa_key)
    with pytest.raises(HTTPException) as e:
        oidc.verify_jwt(_make_token(rsa_key, claims={"iss": "https://idp/", "aud": "other-app"}))
    assert e.value.status_code == 401
    # control: the same provider, our audience
    assert oidc.verify_jwt(_make_token(rsa_key, claims={"iss": "https://idp/", "aud": "kv"}))["sub"]


def test_an_unknown_kid_does_not_refetch_on_every_request(monkeypatch):
    fetches = []

    class _Keys:
        keys = []

    def fake_fetch(url):                      # the real cache logic, counting network fetches
        if oidc._jwks_cache["keys"] is not None:
            return oidc._jwks_cache["keys"]
        fetches.append(url)
        oidc._jwks_cache.update(keys=_Keys(), fetched_at=time.time())
        return oidc._jwks_cache["keys"]

    monkeypatch.setattr(oidc, "_fetch_jwks", fake_fetch)
    monkeypatch.setattr(oidc.jwt, "get_unverified_header", lambda t: {"kid": "unknown"})
    for _ in range(5):
        with pytest.raises(HTTPException):
            oidc._signing_key("x.y.z", "https://idp/jwks")
    assert len(fetches) <= 2


def _oidc_on(monkeypatch):
    monkeypatch.setattr(cfg, "OIDC_JWKS_URL", "https://idp/jwks")
    monkeypatch.setattr(cfg, "OIDC_ISSUER", "https://idp/")
    monkeypatch.setattr(cfg, "OIDC_AUDIENCE", "kv")
