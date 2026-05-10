"""
PrometheusCollector — fetches firing Prometheus alerts and correlates them
with live OntologyGraph entities.

Correlation strategy (highest priority first):
  1. pod label        → Pod
  2. deployment label → Deployment
  3. statefulset      → StatefulSet
  4. daemonset        → DaemonSet
  5. service label    → Service
  6. node label       → Node
  If none matches, the alert is logged but not correlated.

Matched entities receive `alert.<alertname>.*` annotations. Each distinct
alert also becomes a PrometheusAlert node linked via HAS_ALERT edge.
"""
from __future__ import annotations

import logging

import requests

from ontology.entities import K8sEntity, PrometheusAlert
from ontology.graph import OntologyGraph
from ontology.relationships import Edge, RelationshipType

log = logging.getLogger(__name__)

# Label key → ResourceKind.value — checked in priority order
_LABEL_KIND_MAP = [
    ("pod",         "Pod"),
    ("deployment",  "Deployment"),
    ("statefulset", "StatefulSet"),
    ("daemonset",   "DaemonSet"),
    ("service",     "Service"),
    ("node",        "Node"),
]


class PrometheusCollector:
    """
    Queries Prometheus /api/v1/alerts and correlates firing alerts with
    OntologyGraph entities.

    Parameters
    ----------
    url:     Prometheus base URL (e.g. http://prometheus:9090)
    token:   Optional Bearer token for authenticated instances
    timeout: HTTP request timeout in seconds
    """

    def __init__(
        self,
        url: str,
        token: str | None = None,
        timeout: int = 30,
    ) -> None:
        self.url = url.rstrip("/")
        self.token = token
        self.timeout = timeout

    # ------------------------------------------------------------------

    def collect(self, graph: OntologyGraph) -> int:
        """
        Fetch firing alerts, correlate with graph entities, annotate.
        Returns number of alerts successfully correlated to an entity.
        """
        alerts = self._fetch_alerts()
        if not alerts:
            return 0

        correlated = 0
        seen_alert_uids: set[str] = set()

        for alert in alerts:
            if alert.get("state") != "firing":
                continue

            labels = alert.get("labels", {})
            ann = alert.get("annotations", {})
            alert_name = labels.get("alertname", "unknown")
            severity = labels.get("severity", "warning")
            namespace = labels.get("namespace", "")

            entity = self._correlate(labels, graph)
            if entity is None:
                log.debug("prometheus: no entity match for alert %s", alert_name)
                continue

            # Create PrometheusAlert node once per (alertname, namespace)
            alert_uid = f"prom-alert-{alert_name}-{namespace}"
            if alert_uid not in seen_alert_uids:
                seen_alert_uids.add(alert_uid)
                pa = PrometheusAlert(
                    uid=alert_uid,
                    name=alert_name,
                    namespace=namespace or None,
                    alert_name=alert_name,
                    severity=severity,
                    state="firing",
                    summary=ann.get("summary", ""),
                    description=ann.get("description", ""),
                    alert_labels=dict(labels),
                    started_at=alert.get("activeAt", ""),
                )
                graph.add_entity(pa)

            # Annotate matched entity
            prefix = f"alert.{alert_name}"
            entity.annotations[f"{prefix}.severity"] = severity
            entity.annotations[f"{prefix}.state"] = "firing"
            entity.annotations[f"{prefix}.summary"] = ann.get("summary", "")

            # Wire HAS_ALERT edge (deduplicate)
            existing_targets = {
                e.target_uid
                for e in graph._adj.get(entity.uid, [])
                if e.rel_type == RelationshipType.HAS_ALERT
            }
            if alert_uid not in existing_targets:
                graph.add_edge(Edge(entity.uid, alert_uid, RelationshipType.HAS_ALERT))

            log.info(
                "prometheus: %s/%s correlated → %s/%s",
                severity, alert_name, entity.kind.value, entity.name,
            )
            correlated += 1

        log.info("prometheus: %d alert(s) correlated to entities", correlated)
        return correlated

    def is_available(self) -> bool:
        """Return True if the Prometheus API responds with HTTP 200."""
        try:
            resp = requests.get(
                f"{self.url}/-/healthy",
                headers=self._headers(),
                timeout=self.timeout,
            )
            return resp.status_code == 200
        except requests.RequestException:
            return False

    # ------------------------------------------------------------------

    def _fetch_alerts(self) -> list[dict]:
        try:
            resp = requests.get(
                f"{self.url}/api/v1/alerts",
                headers=self._headers(),
                timeout=self.timeout,
            )
            resp.raise_for_status()
            alerts = resp.json().get("data", {}).get("alerts", [])
            log.debug("prometheus: fetched %d alert(s) total", len(alerts))
            return alerts
        except requests.Timeout:
            log.warning("prometheus: request timed out after %ds", self.timeout)
            return []
        except requests.RequestException as exc:
            log.warning("prometheus: request failed: %s", exc)
            return []

    def _correlate(self, labels: dict, graph: OntologyGraph) -> K8sEntity | None:
        namespace = labels.get("namespace", "")
        for label_key, kind_value in _LABEL_KIND_MAP:
            name = labels.get(label_key)
            if not name:
                continue
            entity = _find_entity(graph, kind_value, name, namespace)
            if entity:
                return entity
        return None

    def _headers(self) -> dict[str, str]:
        if self.token:
            return {"Authorization": f"Bearer {self.token}"}
        return {}


# ───────────────────────────────────────────────────��─────────────────────────

def _find_entity(
    graph: OntologyGraph, kind: str, name: str, namespace: str
) -> K8sEntity | None:
    for entity in graph.entities():
        if entity.kind.value != kind:
            continue
        if entity.name != name:
            continue
        if namespace and entity.namespace and entity.namespace != namespace:
            continue
        return entity
    return None
