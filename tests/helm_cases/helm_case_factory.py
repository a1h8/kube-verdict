"""
Helm case factory.

Translates a helm_case directory (helm/values.yaml + observed/*.json)
into an OntologyGraph by running the REAL ingestion pipeline:

  1. Parse values.yaml + optional helmfile.yaml  → HelmRelease entity
  2. Parse observed/deployment.json              → Deployment entity
  3. Parse observed/pod.json (or pods.json)      → Pod entities
  4. Parse observed/events.json                  → K8sEvent entities
  5. Parse observed/helm_release.json            → compute value drift vs declared
  6. Wire MANAGED_BY_HELM edges
  7. Run HelmDriftDetector.detect_all(graph)     → drift annotations
  8. Run AnchorEngine                            → anchor annotations

This is the same pipeline that runs against a real cluster.
No synthetic data — every drift item and anchor comes from real kubectl output.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from ingestion.anchor_engine import AnchorEngine
from ingestion.helm_drift import HelmDriftDetector
from ingestion.chart_parser import flatten_values
from ontology.entities import (
    Deployment, HelmRelease, K8sEvent, Pod, ResourceKind, K8sEntity,
)
from ontology.graph import OntologyGraph
from ontology.relationships import Edge, RelationshipType


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_helm_case(case_dir: Path) -> dict:
    """Load all files from a helm_case directory."""
    helm_dir     = case_dir / "helm"
    observed_dir = case_dir / "observed"

    values_path   = helm_dir / "values.yaml"
    helmfile_path = helm_dir / "helmfile.yaml"

    case: dict[str, Any] = {
        "case_dir":    case_dir,
        "case_name":   case_dir.name,
        "helm_values": yaml.safe_load(values_path.read_text()) if values_path.exists() else {},
        "helmfile":    yaml.safe_load(helmfile_path.read_text()) if helmfile_path.exists() else None,
        "observed":    _load_observed(observed_dir),
        "expect":      json.loads((case_dir / "expect.json").read_text()),
    }
    return case


def build_helm_graph(case: dict) -> OntologyGraph:
    """
    Build an OntologyGraph from a loaded helm_case dict.
    Runs HelmDriftDetector + AnchorEngine on the result.
    """
    graph   = OntologyGraph()
    ns      = case["expect"].get("namespace", "default")
    release = case["expect"].get("release", case["case_name"])

    # ── 1. HelmRelease from values.yaml (declared intent) ─────────────────────
    helm_release = _build_helm_release(
        name=release,
        namespace=ns,
        declared_values=case["helm_values"],
        helmfile=case["helmfile"],
    )
    graph.add_entity(helm_release)

    # ── 2. Deployments ────────────────────────────────────────────────────────
    for dep_raw in case["observed"].get("deployments", []):
        dep = _deployment_from_kubectl(dep_raw)
        graph.add_entity(dep)
        graph.add_edge(Edge(dep.uid, helm_release.uid, RelationshipType.MANAGED_BY_HELM))

    # ── 3. Pods ───────────────────────────────────────────────────────────────
    for pod_raw in case["observed"].get("pods", []):
        pod = _pod_from_kubectl(pod_raw)
        graph.add_entity(pod)
        graph.add_edge(Edge(pod.uid, helm_release.uid, RelationshipType.MANAGED_BY_HELM))

    # ── 4. Events ─────────────────────────────────────────────────────────────
    for evt_raw in case["observed"].get("events", []):
        evt = _event_from_kubectl(evt_raw)
        graph.add_entity(evt)

    # ── 5. Value drift: values.yaml vs helm_release.json ─────────────────────
    live_values = case["observed"].get("helm_release_values")
    if live_values and case["helm_values"]:
        _annotate_value_drift(graph, helm_release, case["helm_values"], live_values)

    # ── 6. Helm drift detection (pod OOMKilled, replica mismatch…) ────────────
    HelmDriftDetector().detect_all(graph)

    # ── 7. Anchors from declared Helm values ──────────────────────────────────
    try:
        AnchorEngine().annotate(graph)
    except Exception:
        pass  # AnchorEngine is best-effort in test context

    return graph


# ---------------------------------------------------------------------------
# Observed-files loader
# ---------------------------------------------------------------------------

def _load_observed(observed_dir: Path) -> dict[str, Any]:
    out: dict[str, Any] = {
        "deployments":        [],
        "pods":               [],
        "events":             [],
        "helm_release_values": None,
    }
    if not observed_dir.is_dir():
        return out

    for fpath in observed_dir.glob("*.json"):
        raw = json.loads(fpath.read_text())
        stem = fpath.stem.lower()

        kind = raw.get("kind", "").lower()

        if kind == "eventlist":
            out["events"].extend(raw.get("items", []))
        elif kind == "event":
            out["events"].append(raw)
        elif kind == "deployment":
            out["deployments"].append(raw)
        elif kind == "deploymentlist":
            out["deployments"].extend(raw.get("items", []))
        elif kind == "pod":
            out["pods"].append(raw)
        elif kind == "podlist":
            out["pods"].extend(raw.get("items", []))
        elif "helm_release" in stem or "helm-release" in stem:
            # helm get values -o json output — plain dict, no "kind"
            out["helm_release_values"] = raw
        elif not kind and isinstance(raw, dict):
            # Could be helm get values output (no "kind" field)
            if "stem" not in raw and stem in ("helm_release", "helm-release", "release"):
                out["helm_release_values"] = raw

    return out


# ---------------------------------------------------------------------------
# Entity builders from kubectl JSON
# ---------------------------------------------------------------------------

def _build_helm_release(
    name: str,
    namespace: str,
    declared_values: dict,
    helmfile: dict | None,
) -> HelmRelease:
    chart = ""
    environment = ""

    if helmfile:
        for rel in helmfile.get("releases", []):
            if rel.get("name") == name:
                chart = rel.get("chart", "")
                break
        environment = helmfile.get("environments", {}) and next(
            iter(helmfile.get("environments", {})), ""
        )

    return HelmRelease(
        uid=f"helm-release:{namespace}/{name}",
        name=name,
        namespace=namespace,
        chart=chart,
        status="deployed",
        values=declared_values,
        source="helmfile" if helmfile else "helm",
        environment=environment,
    )


def _deployment_from_kubectl(raw: dict) -> Deployment:
    meta   = raw.get("metadata", {})
    spec   = raw.get("spec",   {})
    status = raw.get("status", {})

    return Deployment(
        uid=meta.get("uid") or f"dep:{meta.get('namespace','')}/{meta.get('name','')}",
        name=meta.get("name", "unknown"),
        namespace=meta.get("namespace"),
        labels=meta.get("labels", {}),
        replicas=spec.get("replicas", 0),
        ready_replicas=status.get("readyReplicas", 0),
        available_replicas=status.get("availableReplicas", 0),
        strategy=spec.get("strategy", {}).get("type", "RollingUpdate"),
        selector=spec.get("selector", {}).get("matchLabels", {}),
        raw=raw,
    )


def _pod_from_kubectl(raw: dict) -> Pod:
    meta   = raw.get("metadata", {})
    spec   = raw.get("spec",   {})
    status = raw.get("status", {})

    owner_kind = owner_name = ""
    for ref in meta.get("ownerReferences", []):
        if ref.get("controller"):
            owner_kind = ref.get("kind", "")
            owner_name = ref.get("name", "")
            break

    # Sum restart counts across all containers
    container_statuses = status.get("containerStatuses", [])
    total_restarts = sum(cs.get("restartCount", 0) for cs in container_statuses)

    return Pod(
        uid=meta.get("uid") or f"pod:{meta.get('namespace','')}/{meta.get('name','')}",
        name=meta.get("name", "unknown"),
        namespace=meta.get("namespace"),
        labels=meta.get("labels", {}),
        phase=status.get("phase", "Unknown"),
        node_name=spec.get("nodeName", ""),
        restart_count=total_restarts,
        container_statuses=container_statuses,
        conditions=status.get("conditions", []),
        owner_ref_kind=owner_kind,
        owner_ref_name=owner_name,
        raw=raw,
    )


def _event_from_kubectl(raw: dict) -> K8sEvent:
    meta    = raw.get("metadata", {})
    involved = raw.get("involvedObject", {})

    first_ts = _parse_ts(raw.get("firstTimestamp") or raw.get("eventTime"))
    last_ts  = _parse_ts(raw.get("lastTimestamp"))

    return K8sEvent(
        uid=meta.get("uid") or f"evt:{meta.get('namespace','')}/{meta.get('name',str(uuid.uuid4())[:8])}",
        name=meta.get("name", "unknown"),
        namespace=meta.get("namespace") or involved.get("namespace"),
        event_type=raw.get("type", "Normal"),
        reason=raw.get("reason", ""),
        message=raw.get("message", ""),
        involved_kind=involved.get("kind", ""),
        involved_name=involved.get("name", ""),
        count=raw.get("count", 1),
        first_time=first_ts,
        last_time=last_ts,
        raw=raw,
    )


def _parse_ts(ts_str: str | None) -> datetime | None:
    if not ts_str:
        return None
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# Value drift: declared (values.yaml) vs live (helm get values)
# ---------------------------------------------------------------------------

def _annotate_value_drift(
    graph: OntologyGraph,
    release: HelmRelease,
    declared: dict,
    live: dict,
) -> None:
    """
    Flat-compare declared values (values.yaml) vs live deployed values
    (helm get values). Annotate the HelmRelease entity with discovered diffs.
    """
    flat_declared = flatten_values(declared)
    flat_live     = flatten_values(live)

    drift_count = 0
    for key in flat_declared:
        if key not in flat_live:
            continue
        d_val = str(flat_declared[key])
        l_val = str(flat_live[key])
        if d_val != l_val:
            annotation_key = f"drift.helm.{key.replace('.', '_')}"
            release.annotations[annotation_key] = (
                f"field={key} declared='{d_val}' [values.yaml] "
                f"observed='{l_val}' [helm-deployed] severity=warning"
            )
            drift_count += 1

    if drift_count:
        release.annotations["drift.helm.summary"] = (
            f"{drift_count} value(s) differ between values.yaml and deployed release"
        )
