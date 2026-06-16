"""Per-identity JWT / OIDC bearer authentication.

Verifies ``Authorization: Bearer <jwt>`` against an OIDC provider's JWKS
(RS256), with optional issuer / audience checks, and returns the decoded
identity (claims). This is the per-user layer the shared-secret guard
(``api/auth.py``) was a placeholder for.

Layering (see ``require_auth``):
  * ``OIDC_JWKS_URL`` set  → verify JWTs against the provider.
  * ``OIDC_REQUIRED=1``    → a valid JWT is mandatory; no fallback.
  * otherwise              → fall back to the shared-secret gate, so existing
                             deployments and the test suite are unchanged.
"""
from __future__ import annotations

import time
import urllib.request
from typing import Any

import jwt
from fastapi import Depends, Header, HTTPException, status

import config as cfg
from api.auth import require_token

# Cache the provider's signing keys so we don't fetch JWKS on every request.
_JWKS_TTL = 3600  # seconds
_jwks_cache: dict[str, Any] = {"keys": None, "fetched_at": 0.0}


def _fetch_jwks(url: str) -> Any:
    now = time.time()
    if _jwks_cache["keys"] is not None and now - _jwks_cache["fetched_at"] < _JWKS_TTL:
        return _jwks_cache["keys"]
    with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310 (operator-configured URL)
        keys = jwt.PyJWKSet.from_json(resp.read().decode())
    _jwks_cache["keys"] = keys
    _jwks_cache["fetched_at"] = now
    return keys


def _signing_key(token: str, jwks_url: str):
    keys = _fetch_jwks(jwks_url)
    kid = jwt.get_unverified_header(token).get("kid")
    for key in keys.keys:
        if key.key_id == kid:
            return key.key
    # kid not found — force a refresh once (key rotation)
    _jwks_cache["keys"] = None
    keys = _fetch_jwks(jwks_url)
    for key in keys.keys:
        if key.key_id == kid:
            return key.key
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="token signing key not found in provider JWKS",
        headers={"WWW-Authenticate": "Bearer"},
    )


def verify_jwt(token: str) -> dict[str, Any]:
    """Verify a JWT against the configured OIDC provider; return its claims."""
    if not cfg.OIDC_JWKS_URL:
        raise HTTPException(status_code=500, detail="OIDC not configured")
    try:
        key = _signing_key(token, cfg.OIDC_JWKS_URL)
        options = {"require": ["exp"], "verify_aud": bool(cfg.OIDC_AUDIENCE)}
        return jwt.decode(
            token,
            key=key,
            algorithms=["RS256"],
            audience=cfg.OIDC_AUDIENCE or None,
            issuer=cfg.OIDC_ISSUER or None,
            options=options,
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"invalid bearer token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def require_auth(authorization: str | None = Header(default=None)) -> dict[str, Any] | None:
    """FastAPI dependency: enforce per-identity JWT auth when OIDC is configured,
    otherwise fall back to the shared-secret gate.

    Returns the decoded claims (identity) when a JWT is verified, else ``None``.
    """
    oidc_on = bool(cfg.OIDC_JWKS_URL)
    scheme, _, presented = (authorization or "").partition(" ")
    is_bearer = scheme.lower() == "bearer" and bool(presented)

    if oidc_on and is_bearer:
        # Looks like a JWT (has two dots) → verify as OIDC; otherwise treat the
        # bearer value as the shared secret (unless OIDC is mandatory).
        if presented.count(".") == 2:
            return verify_jwt(presented)
        if cfg.OIDC_REQUIRED:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="a valid OIDC bearer token is required",
                headers={"WWW-Authenticate": "Bearer"},
            )

    if cfg.OIDC_REQUIRED:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="a valid OIDC bearer token is required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # No OIDC (or non-JWT bearer): defer to the shared-secret gate.
    require_token(authorization)
    return None


# Convenience for routes that want the identity injected.
IdentityDep = Depends(require_auth)
