"""Secret resolution — no plaintext kubeconfig / API keys baked into config.

Resolves a sensitive value from, in order:
  1. an explicit environment variable (local dev / CI),
  2. a file-mounted secret (how external-secrets.io, Vault Agent and the
     Secrets Store CSI driver deliver secrets — a file per key under a dir),
  3. HashiCorp Vault KV v2 over the API (when ``VAULT_ADDR`` + token are set).

This keeps secrets out of committed values/config: the platform (external-secret
or Vault) populates the mount or KV path out-of-band, and the app reads it here.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)

# Directory where external-secrets / Vault Agent / CSI mount secret files
# (one file per key). Configurable; default matches common conventions.
SECRETS_DIR = os.getenv("SECRETS_DIR", "/var/run/secrets/kubeverdict")


def _from_file(name: str) -> str | None:
    path = Path(SECRETS_DIR) / name
    try:
        if path.is_file():
            return path.read_text().strip() or None
    except OSError as exc:
        log.warning("secret file %s unreadable: %s", path, exc)
    return None


def _from_vault(name: str) -> str | None:
    """Read one key from a Vault KV v2 path: VAULT_ADDR + VAULT_TOKEN +
    VAULT_KV_PATH (e.g. secret/data/kubeverdict). Returns the value for `name`."""
    addr = os.getenv("VAULT_ADDR")
    token = os.getenv("VAULT_TOKEN")
    kv_path = os.getenv("VAULT_KV_PATH")
    if not (addr and token and kv_path):
        return None
    url = f"{addr.rstrip('/')}/v1/{kv_path.lstrip('/')}"
    req = urllib.request.Request(url, headers={"X-Vault-Token": token})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310 (operator-configured)
            body = json.load(resp)
        # KV v2 nests under data.data; KV v1 under data.
        data = body.get("data", {})
        data = data.get("data", data)
        val = data.get(name)
        return str(val) if val is not None else None
    except Exception as exc:  # noqa: BLE001 — Vault optional; never hard-fail
        log.warning("vault read failed for %s: %s", name, exc)
        return None


def resolve_secret(name: str, default: str | None = None) -> str | None:
    """Resolve a secret by name from env → mounted file → Vault, else `default`."""
    return (
        os.getenv(name)
        or _from_file(name)
        or _from_vault(name)
        or default
    )
