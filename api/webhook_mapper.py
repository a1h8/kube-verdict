"""
Map an Alertmanager alert payload to a (query, namespaces) pair
suitable for a KubeVerdict RunRequest.
"""
from __future__ import annotations

from api.models import AlertmanagerAlert

# Labels that identify a Kubernetes resource unambiguously.
_RESOURCE_LABELS = ("deployment", "statefulset", "daemonset", "pod", "service", "job", "node")

# Prometheus severity label → human token used in the query.
_SEVERITY_MAP = {"critical": "CRITICAL", "warning": "WARNING", "info": "INFO"}


def alert_to_query(alert: AlertmanagerAlert) -> str:
    """Build a human-readable RCA query from a single firing alert."""
    labels = alert.labels
    alertname = labels.get("alertname", "UnknownAlert")

    parts: list[str] = [alertname]

    for key in _RESOURCE_LABELS:
        if key in labels:
            parts.append(f"{key}/{labels[key]}")
            break

    ns = labels.get("namespace") or labels.get("exported_namespace")
    if ns:
        parts.append(f"in namespace {ns}")

    severity = _SEVERITY_MAP.get(labels.get("severity", "").lower())
    if severity:
        parts.append(f"[{severity}]")

    summary = alert.annotations.get("summary") or alert.annotations.get("message")
    if summary:
        parts.append(f"— {summary}")

    description = alert.annotations.get("description")
    if description and description != summary:
        parts.append(f"\nDetails: {description}")

    return " ".join(parts)


def alert_to_namespaces(alert: AlertmanagerAlert) -> list[str]:
    """Extract namespace(s) from alert labels. Returns empty list if absent."""
    ns = alert.labels.get("namespace") or alert.labels.get("exported_namespace")
    return [ns] if ns else []


def firing_alerts(alerts: list[AlertmanagerAlert]) -> list[AlertmanagerAlert]:
    """Filter to only currently firing alerts."""
    return [a for a in alerts if a.status == "firing"]
