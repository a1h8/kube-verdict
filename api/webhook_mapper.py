"""
Map an Alertmanager alert payload to a (query, namespaces) pair
suitable for a KubeVerdict RunRequest.

The query is built from labels only, each value cut to the characters of a Kubernetes name.
The alert's summary and description are written by whoever can create a PrometheusRule: they go
to the prompt as quoted alert text, never in the operator's question slot
(docs/fix-plan-001.md, H71).
"""
from __future__ import annotations

import re

import config as cfg
from api.models import AlertmanagerAlert

# Labels that identify a Kubernetes resource unambiguously.
_RESOURCE_LABELS = ("deployment", "statefulset", "daemonset", "pod", "service", "job", "node")

# Prometheus severity label → human token used in the query.
_SEVERITY_MAP = {"critical": "CRITICAL", "warning": "WARNING", "info": "INFO"}

_NOT_IN_A_NAME = re.compile(r"[^A-Za-z0-9._:/-]")
_ALERT_TEXT_MAX = 1000


def label_value(value: str, limit: int = 128) -> str:
    """A label value reduced to what a Kubernetes name or identifier can contain."""
    return _NOT_IN_A_NAME.sub("", value)[:limit]


def resource_of(labels: dict[str, str]) -> str:
    """``kind/name`` of the first resource label, or ``""``."""
    for key in _RESOURCE_LABELS:
        if labels.get(key):
            return f"{key}/{label_value(labels[key])}"
    return ""


def alert_to_query(alert: AlertmanagerAlert) -> str:
    """Build the RCA query from a firing alert's labels."""
    labels = alert.labels
    parts: list[str] = [label_value(labels.get("alertname", "")) or "UnknownAlert"]

    resource = resource_of(labels)
    if resource:
        parts.append(resource)

    ns = labels.get("namespace") or labels.get("exported_namespace")
    if ns:
        parts.append(f"in namespace {label_value(ns)}")

    severity = _SEVERITY_MAP.get(labels.get("severity", "").lower())
    if severity:
        parts.append(f"[{severity}]")

    return " ".join(parts)


def alert_text(alert: AlertmanagerAlert) -> str:
    """The alert's own words (summary, description): quoted in the prompt as data."""
    summary = alert.annotations.get("summary") or alert.annotations.get("message") or ""
    description = alert.annotations.get("description") or ""
    lines = [t for t in (summary, description if description != summary else "") if t]
    return "\n".join(lines)[:_ALERT_TEXT_MAX]


def alert_to_namespaces(alert: AlertmanagerAlert) -> list[str]:
    """The alert's namespace label, else the configured KUBE_NAMESPACES; [] = refuse the alert."""
    ns = label_value(alert.labels.get("namespace") or alert.labels.get("exported_namespace") or "")
    return [ns] if ns else list(cfg.KUBE_NAMESPACES)


def firing_alerts(alerts: list[AlertmanagerAlert]) -> list[AlertmanagerAlert]:
    """Filter to only currently firing alerts."""
    return [a for a in alerts if a.status == "firing"]
