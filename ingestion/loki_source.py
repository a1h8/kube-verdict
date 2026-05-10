"""
LokiSource — queries Grafana Loki for pod logs and wires them into the
OntologyGraph via HAS_LOG edges.

LogQL strategy:
  • Pod with namespace → {k8s_pod_name="<pod>",k8s_namespace_name="<ns>"}
  • Pod without namespace → {k8s_pod_name="<pod>"}

Each log entry becomes a LokiLog node attached to the pod entity.
Log level is extracted from the structured `level` stream label or
inferred by keyword scan. OTel trace IDs are extracted from the log
line via a 32-char or 16-char hex regex.
"""
from __future__ import annotations

import logging
import re
import time

import requests

from ontology.entities import LokiLog, Pod, ResourceKind
from ontology.graph import OntologyGraph
from ontology.relationships import Edge, RelationshipType

log = logging.getLogger(__name__)

_TRACE_RE = re.compile(r'\b([0-9a-f]{32}|[0-9a-f]{16})\b', re.IGNORECASE)

_LEVEL_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("error", ("error", "err ", "fatal", "panic", "exception")),
    ("warn",  ("warn", "warning")),
    ("debug", ("debug", "trace")),
]


class LokiSource:
    """
    Queries Loki HTTP API (/loki/api/v1/query_range) for pod logs.

    Parameters
    ----------
    url:              Loki base URL (e.g. http://loki:3100)
    token:            Optional Bearer token
    timeout:          HTTP request timeout in seconds
    lookback_hours:   How far back to query
    max_logs_per_pod: Cap per entity (avoids token explosion in the LLM)
    """

    def __init__(
        self,
        url: str,
        token: str | None = None,
        timeout: int = 30,
        lookback_hours: int = 1,
        max_logs_per_pod: int = 20,
    ) -> None:
        self.url = url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.lookback_hours = lookback_hours
        self.max_logs = max_logs_per_pod

    def collect(self, graph: OntologyGraph) -> int:
        """
        Fetch error/warn logs for unhealthy pods and wire them into the graph.
        Returns number of LokiLog nodes created.
        """
        end_ns = int(time.time() * 1_000_000_000)
        start_ns = end_ns - self.lookback_hours * 3600 * 1_000_000_000

        log_count = 0
        seen_log_uids: set[str] = set()

        # Snapshot to avoid mutating the dict while iterating
        unhealthy_pods = [
            e for e in list(graph.entities(ResourceKind.POD))
            if isinstance(e, Pod) and e.is_unhealthy
        ]

        for entity in unhealthy_pods:

            namespace = entity.namespace or ""
            logql = _build_logql(entity.name, namespace)
            rows = self._query(logql, start_ns, end_ns)

            for ts_ns, line in rows[: self.max_logs]:
                uid = f"loki-log-{entity.name}-{ts_ns}"
                if uid in seen_log_uids:
                    continue
                seen_log_uids.add(uid)

                level = _detect_level(line)
                trace_id = _extract_trace_id(line)

                ll = LokiLog(
                    uid=uid,
                    name=uid,
                    namespace=namespace or None,
                    log_line=line,
                    level=level,
                    trace_id=trace_id,
                    pod_name=entity.name,
                    timestamp_ns=ts_ns,
                )
                graph.add_entity(ll)
                graph.add_edge(Edge(entity.uid, uid, RelationshipType.HAS_LOG))
                log_count += 1

            if rows:
                log.info(
                    "loki: %s/%s → %d log line(s) fetched",
                    namespace, entity.name, len(rows),
                )

        log.info("loki: %d LokiLog node(s) created", log_count)
        return log_count

    def is_available(self) -> bool:
        """Return True if Loki responds to a readiness probe."""
        try:
            resp = requests.get(
                f"{self.url}/ready",
                headers=self._headers(),
                timeout=self.timeout,
            )
            return resp.status_code == 200
        except requests.RequestException:
            return False

    # ------------------------------------------------------------------

    def _query(self, logql: str, start_ns: int, end_ns: int) -> list[tuple[int, str]]:
        """Execute a LogQL range query; return list of (timestamp_ns, line) tuples."""
        try:
            resp = requests.get(
                f"{self.url}/loki/api/v1/query_range",
                params={
                    "query":     logql,
                    "start":     str(start_ns),
                    "end":       str(end_ns),
                    "limit":     self.max_logs,
                    "direction": "backward",
                },
                headers=self._headers(),
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            results: list[tuple[int, str]] = []
            for stream in data.get("data", {}).get("result", []):
                for ts_str, line in stream.get("values", []):
                    results.append((int(ts_str), line))
            return results
        except requests.Timeout:
            log.warning("loki: request timed out for query %s", logql[:80])
            return []
        except requests.RequestException as exc:
            log.warning("loki: request failed (%s)", exc)
            return []

    def _headers(self) -> dict[str, str]:
        if self.token:
            return {"Authorization": f"Bearer {self.token}"}
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _build_logql(pod_name: str, namespace: str) -> str:
    if namespace:
        return f'{{k8s_pod_name="{pod_name}",k8s_namespace_name="{namespace}"}}'
    return f'{{k8s_pod_name="{pod_name}"}}'


def _detect_level(line: str) -> str:
    lower = line.lower()
    for level, keywords in _LEVEL_KEYWORDS:
        if any(kw in lower for kw in keywords):
            return level
    return "info"


def _extract_trace_id(line: str) -> str:
    m = _TRACE_RE.search(line)
    return m.group(1) if m else ""
