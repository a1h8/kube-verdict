"""
graph_factory — builds a synthetic OntologyGraph from a cases/*/input.json fixture.

Mapping rules
─────────────
  input.json.events[]         → K8sEvent nodes + HAS_EVENT edges
  input.json.pod_status       → Pod node  (phase + restart_count drive is_unhealthy)
  input.json.pvc_status       → PersistentVolumeClaim node (status_phase drives is_unhealthy)
  input.json.helm_drift.diffs → drift.* annotations on Pod
  input.json.anchors[]        → anchor.* annotations on Pod
  input.json.policy_report    → PolicyViolation nodes
  input.json.metrics          → signal.* annotations on Pod
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from ontology.entities import (
    K8sEvent,
    Namespace,
    PersistentVolumeClaim,
    Pod,
    PolicyViolation,
)
from ontology.graph import OntologyGraph
from ontology.relationships import Edge, RelationshipType


def load_case(case_dir: Path) -> dict:
    """Return input and expect JSON for a case directory."""
    return {
        "input":  json.loads((case_dir / "input.json").read_text()),
        "expect": json.loads((case_dir / "expect.json").read_text()),
    }


def build_graph(input_json: dict) -> OntologyGraph:
    """
    Build a minimal but realistic OntologyGraph from an input.json fixture.
    Enough to exercise find_unhealthy(), ContextBuilder, and compute_confidence().
    """
    graph = OntologyGraph()
    namespace = input_json.get("namespace", "default")

    # ── Namespace ────────────────────────────────────────────────────────────
    ns = Namespace(uid=f"ns-{namespace}", name=namespace)
    graph.add_entity(ns)

    # ── Pod ──────────────────────────────────────────────────────────────────
    pod_name, pod = _build_pod(input_json, namespace)

    if pod is not None:
        graph.add_entity(pod)
        graph.add_edge(Edge(pod.uid, ns.uid, RelationshipType.IN_NAMESPACE))

        # Helm drift → drift.* annotations
        for diff in (input_json.get("helm_drift") or {}).get("diffs", []):
            field    = diff.get("field", "unknown")
            declared = diff.get("declared", "")
            observed = diff.get("observed", "")
            pod.annotations[f"drift.{field}"] = (
                f"declared={declared!r} observed={observed!r} severity=critical"
            )

        # Anchors → anchor.* annotations
        for anchor_text in input_json.get("anchors", []):
            field_path = _extract_anchor_field(anchor_text)
            pod.annotations[f"anchor.{field_path}"] = anchor_text

        # Metrics → signal.* annotations
        metrics = input_json.get("metrics") or {}
        if metrics:
            mem_mi   = metrics.get("memory_mi", 0)
            limit_mi = metrics.get("memory_limit_mi", 0)
            ratio    = metrics.get("usage_ratio", 0.0)
            pod.annotations["signal.memory_usage_ratio"] = (
                f"memory_mi={mem_mi} limit_mi={limit_mi} usage={ratio:.0%}"
            )

    # ── K8s Events ───────────────────────────────────────────────────────────
    for i, evt in enumerate(input_json.get("events", [])):
        obj      = evt.get("object", "")
        parts    = obj.split("/", 1)
        inv_kind = parts[0] if parts else ""
        inv_name = parts[1] if len(parts) > 1 else obj

        uid = f"event-{evt.get('reason', 'evt')}-{inv_name}-{i}"
        event = K8sEvent(
            uid=uid,
            name=f"{evt.get('reason', 'event')}-{i}",
            namespace=namespace,
            reason=evt.get("reason", ""),
            message=evt.get("message", ""),
            event_type=evt.get("type", "Normal"),
            involved_kind=inv_kind,
            involved_name=inv_name,
            count=evt.get("count", 1),
        )
        graph.add_entity(event)
        if pod is not None:
            graph.add_edge(Edge(pod.uid, uid, RelationshipType.HAS_EVENT))

    # ── PVC (case 04) ─────────────────────────────────────────────────────────
    pvc_data = input_json.get("pvc_status")
    if pvc_data:
        pvc = PersistentVolumeClaim(
            uid=f"pvc-{pvc_data['name']}-{namespace}",
            name=pvc_data["name"],
            namespace=namespace,
            status_phase=pvc_data.get("phase", "Pending"),
            storage_class=pvc_data.get("storageClassName", ""),
            requested_storage=pvc_data.get("storage", ""),
        )
        graph.add_entity(pvc)
        if pod is not None:
            graph.add_edge(Edge(pod.uid, pvc.uid, RelationshipType.USES_PVC))

    # ── Policy violations (case 05) ──────────────────────────────────────────
    policy_report = input_json.get("policy_report") or {}
    for viol in policy_report.get("violations", []):
        resource = viol.get("resource", "")
        parts    = resource.split("/")
        r_kind   = parts[0] if len(parts) > 0 else ""
        r_ns     = parts[1] if len(parts) > 1 else namespace
        r_name   = parts[2] if len(parts) > 2 else ""

        policy = policy_report.get("policy", "unknown-policy")
        rule   = viol.get("rule", "unknown-rule")
        result = viol.get("result", "fail")

        uid = f"pv-{policy}-{rule}-{r_name}"
        pv  = PolicyViolation(
            uid=uid,
            name=f"{policy}/{rule}",
            namespace=r_ns or None,
            policy=policy,
            rule=rule,
            result=result,
            message=viol.get("message", ""),
            severity="medium",
            source="kyverno",
            resource_kind=r_kind,
            resource_name=r_name,
            resource_namespace=r_ns,
        )
        graph.add_entity(pv)
        if pod is not None:
            graph.add_edge(
                Edge(pod.uid, uid, RelationshipType.HAS_POLICY_VIOLATION)
            )

    return graph


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_pod(input_json: dict, namespace: str) -> tuple[str, Pod | None]:
    """
    Extract pod name from the first event's object field, then build the Pod.
    Returns (pod_name, Pod) or ("", None) if no pod info is available.
    """
    pod_status = input_json.get("pod_status")
    events     = input_json.get("events", [])

    pod_name = ""
    for evt in events:
        obj = evt.get("object", "")
        m   = re.match(r"Pod/(.+)", obj)
        if m:
            pod_name = m.group(1)
            break

    if not pod_name and not pod_status:
        return "", None

    pod_name = pod_name or input_json.get("scenario", "pod").lower().replace(" ", "-")

    container_statuses = (pod_status or {}).get("containerStatuses", [])
    restart_count = max(
        (c.get("restartCount", 0) for c in container_statuses),
        default=0,
    )
    phase = (pod_status or {}).get("phase", "Unknown")

    pod = Pod(
        uid=f"pod-{namespace}-{pod_name}",
        name=pod_name,
        namespace=namespace,
        phase=phase,
        restart_count=restart_count,
        container_statuses=container_statuses,
    )
    return pod_name, pod


def _extract_anchor_field(anchor_text: str) -> str:
    """
    Parse 'Kind/ns/name: container.X.resources.limits.memory declared=...'
    → 'container.X.resources.limits.memory'
    """
    m = re.search(r":\s*([^\s]+)\s+declared=", anchor_text)
    if m:
        return m.group(1)
    return f"field.{abs(hash(anchor_text)) % 10000}"
