"""
Shared-secret bearer-token guard for mutating / IDP endpoints.

This is intentionally minimal: a single static token (``KUBEVERDICT_API_TOKEN``)
compared in constant time. It is **not** OIDC/JWT and grants no per-user identity
or scopes — that remains tracked under Production Hardening. When the token is
unset the guard is a no-op (open), so local dev and the test suite are unchanged.
"""
from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, status

import config as cfg


def require_token(authorization: str | None = Header(default=None)) -> None:
    """FastAPI dependency: require ``Authorization: Bearer <token>`` when a token
    is configured. No-op when ``KUBEVERDICT_API_TOKEN`` is unset."""
    expected = cfg.API_TOKEN
    if not expected:
        return  # auth disabled

    scheme, _, presented = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(presented, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
