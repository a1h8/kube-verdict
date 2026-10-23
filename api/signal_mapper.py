"""
Map a pushed PatchTST signal alert (native AnomalyResult / SignalRecord shape,
not Alertmanager) to a (query, namespaces) pair suitable for a KubeVerdict
RunRequest. Mirrors api/webhook_mapper.py: the query comes from the labels, the pushed ``text``
is quoted alert text (docs/fix-plan-001.md, H71).
"""
from __future__ import annotations

import config as cfg
from api.models import SignalAlert
from api.webhook_mapper import _ALERT_TEXT_MAX, label_value, resource_of


def signal_to_query(alert: SignalAlert) -> str:
    """Build the RCA query from a pushed signal's metric and labels."""
    labels = alert.labels
    parts: list[str] = [f"Anomalous {label_value(alert.metric_name)}"]
    parts.append(resource_of(labels) or label_value(alert.entity_uid, 253))

    ns = labels.get("namespace") or labels.get("exported_namespace")
    if ns:
        parts.append(f"in namespace {label_value(ns)}")

    parts.append(f"[{label_value(alert.severity).upper()}]")
    return " ".join(parts)


def signal_text(alert: SignalAlert) -> str:
    """The pushed signal's own words: quoted in the prompt as data."""
    return alert.text[:_ALERT_TEXT_MAX]


def signal_to_namespaces(alert: SignalAlert) -> list[str]:
    """The signal's namespace label, else the namespace of its ``Kind/namespace/name`` entity,
    else the configured KUBE_NAMESPACES; [] = refuse it."""
    ns = alert.labels.get("namespace") or alert.labels.get("exported_namespace")
    if not ns:
        parts = alert.entity_uid.split("/")
        ns = parts[1] if len(parts) == 3 else ""
    return [label_value(ns)] if label_value(ns) else list(cfg.KUBE_NAMESPACES)


def anomalous_signals(alerts: list[SignalAlert]) -> list[SignalAlert]:
    """Filter to only anomalous (non-normal severity) signals."""
    return [a for a in alerts if a.severity != "normal"]
