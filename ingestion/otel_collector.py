"""
OtelCollector — correlates OpenTelemetry error traces with OntologyGraph entities.

For each unhealthy K8s entity the collector resolves its service name, calls
the OtelBackend to fetch recent error traces, creates OtelTrace nodes, and
wires HAS_TRACE edges.

Service name resolution order (labels take priority over entity name):
  1. app.kubernetes.io/name
  2. app
  3. app.kubernetes.io/component
  4. entity.name
"""
from __future__ import annotations

import logging
import time

from ingestion.otel_backend import OtelBackend
from ontology.entities import (
    DaemonSet,
    Deployment,
    K8sEntity,
    OtelTrace,
    Pod,
    ResourceKind,
    StatefulSet,
)
from ontology.graph import OntologyGraph
from ontology.relationships import Edge, RelationshipType

log = logging.getLogger(__name__)

_UNHEALTHY_KINDS = frozenset({
    ResourceKind.POD,
    ResourceKind.DEPLOYMENT,
    ResourceKind.STATEFULSET,
    ResourceKind.DAEMONSET,
})


class OtelCollector:
    """
    Fetches error traces for unhealthy entities and wires them into the graph.

    Parameters
    ----------
    backend:        OtelBackend implementation (TempoBackend or JaegerBackend)
    lookback_hours: How far back to search for error traces
    """

    def __init__(self, backend: OtelBackend, lookback_hours: int = 1) -> None:
        self.backend = backend
        self.lookback_hours = lookback_hours

    def collect(self, graph: OntologyGraph) -> int:
        """
        Correlate error traces with graph entities.
        Returns the number of OtelTrace nodes created.
        """
        end_ts = int(time.time())
        start_ts = end_ts - self.lookback_hours * 3600

        trace_count = 0
        seen_trace_ids: set[str] = set()

        # Snapshot to avoid mutating the dict while iterating
        target_entities = [
            e for e in list(graph.entities())
            if e.kind in _UNHEALTHY_KINDS and _is_unhealthy(e)
        ]

        for entity in target_entities:
            service = _resolve_service_name(entity)
            namespace = entity.namespace or ""

            traces = self.backend.search_error_traces(
                service=service,
                namespace=namespace,
                start_ts=start_ts,
                end_ts=end_ts,
            )

            for trace in traces:
                tid = trace.get("trace_id", "")
                if not tid:
                    continue

                trace_uid = f"otel-trace-{tid}"

                if trace_uid not in seen_trace_ids:
                    seen_trace_ids.add(trace_uid)
                    ot = OtelTrace(
                        uid=trace_uid,
                        name=tid,
                        namespace=namespace or None,
                        trace_id=tid,
                        service_name=trace.get("service_name", service),
                        status=trace.get("status", ""),
                        duration_ms=float(trace.get("duration_ms", 0.0)),
                        span_count=int(trace.get("span_count", 0)),
                        error_message=trace.get("error_message", ""),
                        root_span_name=trace.get("root_span", ""),
                        error_spans=list(trace.get("error_spans", [])),
                        started_at=trace.get("started_at", ""),
                    )
                    graph.add_entity(ot)
                    trace_count += 1

                # Wire HAS_TRACE edge (dedup)
                existing = {
                    e.target_uid
                    for e in graph._adj.get(entity.uid, [])
                    if e.rel_type == RelationshipType.HAS_TRACE
                }
                if trace_uid not in existing:
                    graph.add_edge(Edge(entity.uid, trace_uid, RelationshipType.HAS_TRACE))

                # Annotate entity with trace summary
                prefix = f"otel.trace.{tid}"
                entity.annotations[f"{prefix}.status"] = trace.get("status", "")
                if trace.get("error_message"):
                    entity.annotations[f"{prefix}.error"] = trace["error_message"][:200]

            if traces:
                err_count = sum(1 for t in traces if t.get("status") == "ERROR")
                entity.annotations["otel.error_trace_count"] = str(err_count)
                log.info(
                    "otel: %s/%s → %d error trace(s)", entity.kind.value, entity.name, err_count,
                )

        log.info("otel: %d OtelTrace node(s) created", trace_count)
        return trace_count


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _is_unhealthy(entity: K8sEntity) -> bool:
    if isinstance(entity, Pod):
        return entity.is_unhealthy
    if isinstance(entity, Deployment):
        return entity.is_degraded
    if isinstance(entity, StatefulSet):
        return entity.ready_replicas < entity.replicas
    if isinstance(entity, DaemonSet):
        return entity.ready < entity.desired
    return False


def _resolve_service_name(entity: K8sEntity) -> str:
    for key in ("app.kubernetes.io/name", "app", "app.kubernetes.io/component"):
        val = entity.labels.get(key)
        if val:
            return val
    return entity.name
