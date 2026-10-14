"""
Provenance — is a chart or document public (upstream) or enterprise?

Spec: docs/target/knowledge-sources.md § Step 1. The diagnosis weighs an
enterprise's own pinned expected state above upstream chart defaults, and
(later) the sLLM corpus export must never take enterprise content by default.
Classification fails closed: a source not known to be public is enterprise.
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

PUBLIC = "public"
ENTERPRISE = "enterprise"

# Well-known public chart sources. Override with PUBLIC_CHART_SOURCES
# (comma-separated hosts, or host/path prefixes for OCI registries).
_DEFAULT_PUBLIC_SOURCES = (
    "artifacthub.io",
    "charts.bitnami.com",
    "registry-1.docker.io/bitnamicharts",
    "prometheus-community.github.io",
    "grafana.github.io",
    "charts.jetstack.io",
    "kubernetes-sigs.github.io",
    "kubernetes.github.io",
    "helm.cilium.io",
    "aws.github.io",
    "argoproj.github.io",
    "open-telemetry.github.io",
    "helm.ngc.nvidia.com",
)


def _normalize(source: str) -> str:
    """``oci://Registry.acme/public/`` → ``registry.acme/public``: no scheme, no trailing /."""
    source = source.strip().lower()
    return source.split("://", 1)[-1].rstrip("/")


def public_sources() -> tuple[str, ...]:
    raw = os.environ.get("PUBLIC_CHART_SOURCES")
    if raw is None:
        return _DEFAULT_PUBLIC_SOURCES
    return tuple(_normalize(s) for s in raw.split(",") if s.strip())


def classify_url(url: str) -> str:
    """``public`` when the URL's host (or host/path for OCI) is a known public source.

    Anything else — an internal registry, a private GitHub org, an empty URL — is
    ``enterprise``: unknown is treated as confidential, never as public.
    """
    if not url:
        return ENTERPRISE
    parsed = urlparse(url if "://" in url else f"https://{url}")
    host = (parsed.hostname or "").lower()
    location = f"{host}{parsed.path}".rstrip("/").lower()
    for source in public_sources():
        if "/" in source:
            if location == source or location.startswith(source + "/"):
                return PUBLIC
        elif host == source:
            return PUBLIC
    return ENTERPRISE
