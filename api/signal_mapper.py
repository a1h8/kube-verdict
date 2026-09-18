"""
Map a pushed PatchTST signal alert (native AnomalyResult / SignalRecord shape,
not Alertmanager) to a (query, namespaces) pair suitable for a KubeVerdict
RunRequest. Mirrors api/webhook_mapper.py.
"""
from __future__ import annotations

from api.models import SignalAlert

# Labels that identify a Kubernetes resource unambiguously (standard
# Prometheus/Mimir Kubernetes service-discovery label names).
_RESOURCE_LABELS = ("deployment", "statefulset", "daemonset", "pod", "service", "job", "node")


def signal_to_query(alert: SignalAlert) -> str:
    """Build a human-readable RCA query from a single pushed signal."""
    labels = alert.labels
    parts: list[str] = [f"Anomalous {alert.metric_name}"]

    for key in _RESOURCE_LABELS:
        if key in labels:
            parts.append(f"{key}/{labels[key]}")
            break
    else:
        parts.append(alert.entity_uid)

    ns = labels.get("namespace") or labels.get("exported_namespace")
    if ns:
        parts.append(f"in namespace {ns}")

    parts.append(f"[{alert.severity.upper()}]")

    if alert.text:
        parts.append(f"— {alert.text}")

    return " ".join(parts)


def signal_to_namespaces(alert: SignalAlert) -> list[str]:
    """Extract namespace(s) from signal labels. Returns empty list if absent."""
    ns = alert.labels.get("namespace") or alert.labels.get("exported_namespace")
    return [ns] if ns else []


def anomalous_signals(alerts: list[SignalAlert]) -> list[SignalAlert]:
    """Filter to only anomalous (non-normal severity) signals."""
    return [a for a in alerts if a.severity != "normal"]
