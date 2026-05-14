"""
Native Kubernetes integration-case loader.

Reads cases from ``tests/integration/cases/h*/``.  Each case directory has:

  kube/       — kubectl get -o yaml output (pods, deployments, events as YAML)
  helm/       — values.yaml (declared) + release.json (live deployed values)
  helmfile/   — helmfile.yaml (optional)
  policy/     — PolicyReport YAML from Kyverno/OPA (optional)
  expect.json — test expectations

Usage::

    from pathlib import Path
    from tests.integration.cases.case_loader import load_case, build_graph, list_cases

    root  = Path("tests/integration/cases")
    cases = list_cases(root)
    for case_dir in cases:
        case  = load_case(case_dir)
        graph = build_graph(case)
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
    Deployment, HelmRelease, K8sEvent, Pod, PolicyViolation, ResourceKind,
)
from ontology.graph import OntologyGraph
from ontology.relationships import Edge, RelationshipType


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def list_cases(cases_root: Path) -> list[Path]:
    """Return sorted subdirectories matching ``h[0-9]*/``."""
    return sorted(d for d in cases_root.glob("h[0-9]*/") if d.is_dir())


def load_case(case_dir: Path) -> dict:
    """
    Load all artefacts from a native integration-case directory.

    Returns a dict with keys:
      case_dir, case_name, helm_values, helmfile, observed, policy_reports, expect
    """
    helm_dir     = case_dir / "helm"
    helmfile_dir = case_dir / "helmfile"
    kube_dir     = case_dir / "kube"
    policy_dir   = case_dir / "policy"

    values_path   = helm_dir / "values.yaml"
    helmfile_path = helmfile_dir / "helmfile.yaml"

    case: dict[str, Any] = {
        "case_dir":      case_dir,
        "case_name":     case_dir.name,
        "helm_values":   yaml.safe_load(values_path.read_text()) if values_path.exists() else {},
        "helmfile":      yaml.safe_load(helmfile_path.read_text()) if helmfile_path.exists() else None,
        "observed":      _load_kube(kube_dir),
        "policy_reports": _load_policy_reports(policy_dir),
        "expect":        json.loads((case_dir / "expect.json").read_text()),
    }

    # Live deployed values from helm/release.json
    release_path = helm_dir / "release.json"
    if release_path.exists():
        case["observed"]["helm_release_values"] = json.loads(release_path.read_text())

    return case


def build_graph(case: dict) -> OntologyGraph:
    """
    Build an OntologyGraph from a loaded native case dict.
    Runs HelmDriftDetector + AnchorEngine on the result.
    """
    graph  = OntologyGraph()
    ns     = case["expect"].get("namespace", "default")
    release_name = case["expect"].get("release", case["case_name"])

    # ── 1. HelmRelease from values.yaml (declared intent) ──────────────────
    helm_release = _build_helm_release(
        name=release_name,
        namespace=ns,
        declared_values=case["helm_values"],
        helmfile=case["helmfile"],
    )
    graph.add_entity(helm_release)

    # ── 2. Deployments ──────────────────────────────────────────────────────
    for dep_raw in case["observed"].get("deployments", []):
        dep = _deployment_from_kubectl(dep_raw)
        graph.add_entity(dep)
        graph.add_edge(Edge(dep.uid, helm_release.uid, RelationshipType.MANAGED_BY_HELM))

    # ── 3. Pods ─────────────────────────────────────────────────────────────
    for pod_raw in case["observed"].get("pods", []):
        pod = _pod_from_kubectl(pod_raw)
        graph.add_entity(pod)
        graph.add_edge(Edge(pod.uid, helm_release.uid, RelationshipType.MANAGED_BY_HELM))

    # ── 4. Events ───────────────────────────────────────────────────────────
    for evt_raw in case["observed"].get("events", []):
        evt = _event_from_kubectl(evt_raw)
        graph.add_entity(evt)

    # ── 5. Value drift: values.yaml vs helm/release.json ───────────────────
    live_values = case["observed"].get("helm_release_values")
    if live_values and case["helm_values"]:
        _annotate_value_drift(graph, helm_release, case["helm_values"], live_values)

    # ── 6. Policy violations ─────────────────────────────────────────────
    for report in case.get("policy_reports", []):
        _ingest_policy_report(report, graph)

    # ── 7. Helm drift detection (pod OOMKilled, replica mismatch…) ─────────
    HelmDriftDetector().detect_all(graph)

    # ── 8. Anchors from declared Helm values ────────────────────────────────
    try:
        AnchorEngine().annotate(graph)
    except Exception:
        pass  # AnchorEngine is best-effort in test context

    return graph


# ---------------------------------------------------------------------------
# Kube artefact loader
# ---------------------------------------------------------------------------

def _load_kube(kube_dir: Path) -> dict[str, Any]:
    """Load kubectl YAML/JSON output from kube/."""
    out: dict[str, Any] = {
        "deployments":         [],
        "pods":                [],
        "events":              [],
        "helm_release_values": None,
    }
    if not kube_dir.is_dir():
        return out

    for fpath in sorted(kube_dir.iterdir()):
        if fpath.suffix not in (".yaml", ".yml", ".json"):
            continue

        if fpath.suffix in (".yaml", ".yml"):
            docs = list(yaml.safe_load_all(fpath.read_text()))
        else:
            content = json.loads(fpath.read_text())
            docs = [content] if isinstance(content, dict) else content

        for raw in docs:
            if not isinstance(raw, dict):
                continue
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

    return out


# ---------------------------------------------------------------------------
# Policy report loader
# ---------------------------------------------------------------------------

def _load_policy_reports(policy_dir: Path) -> list[dict]:
    """Load PolicyReport YAML documents from policy/."""
    reports: list[dict] = []
    if not policy_dir.is_dir():
        return reports

    for fpath in sorted(policy_dir.iterdir()):
        if fpath.suffix not in (".yaml", ".yml"):
            continue
        for doc in yaml.safe_load_all(fpath.read_text()):
            if not isinstance(doc, dict):
                continue
            api = doc.get("apiVersion", "")
            kind = doc.get("kind", "")
            if "wgpolicyk8s.io" in api and "PolicyReport" in kind:
                reports.append(doc)

    return reports


def _ingest_policy_report(report: dict, graph: OntologyGraph) -> None:
    """Parse a PolicyReport and add PolicyViolation entities to the graph."""
    meta      = report.get("metadata", {})
    report_ns = meta.get("namespace", "")
    source    = _detect_policy_source(report)

    for item in report.get("results", []):
        r = item.get("result", "").lower()
        if r not in ("fail", "warn", "error"):
            continue

        policy   = item.get("policy", "")
        rule     = item.get("rule", "")
        message  = item.get("message", "")
        severity = item.get("severity", "")

        resources = item.get("resources") or []
        if not resources:
            # Cluster-scoped or single resource reference
            res_ref = item.get("resource")
            if isinstance(res_ref, dict):
                resources = [res_ref]

        for res_ref in resources:
            r_kind = res_ref.get("kind", "")
            r_name = res_ref.get("name", "")
            r_ns   = res_ref.get("namespace", "") or report_ns

            slug = "-".join(
                p.lower().replace("/", "_").replace(".", "-")
                for p in [policy, rule, r_kind, r_ns or "cluster", r_name]
            )
            uid = f"policy-violation-{slug}"[:128]

            violation = PolicyViolation(
                uid=uid,
                name=f"{policy}/{rule}",
                namespace=r_ns or None,
                policy=policy,
                rule=rule,
                result=r,
                message=message,
                severity=severity,
                source=source,
                resource_kind=r_kind,
                resource_name=r_name,
                resource_namespace=r_ns,
            )
            graph.add_entity(violation)

            # Correlate with existing graph entity
            entity = _find_entity(graph, r_kind, r_name, r_ns)
            if entity is not None:
                ann_prefix = f"policy.{policy}"
                entity.annotations[f"{ann_prefix}.result"]   = r
                entity.annotations[f"{ann_prefix}.severity"] = severity
                entity.annotations[f"{ann_prefix}.rule"]     = rule
                entity.annotations[f"{ann_prefix}.message"]  = message[:200]
                entity.annotations[f"{ann_prefix}.source"]   = source
                existing = {
                    e.target_uid
                    for e in graph._adj.get(entity.uid, [])
                    if e.rel_type == RelationshipType.HAS_POLICY_VIOLATION
                }
                if uid not in existing:
                    graph.add_edge(
                        Edge(entity.uid, uid, RelationshipType.HAS_POLICY_VIOLATION)
                    )


# ---------------------------------------------------------------------------
# Entity builders — copied from helm_case_factory.py for self-containment
# ---------------------------------------------------------------------------

def _build_helm_release(
    name: str,
    namespace: str,
    declared_values: dict,
    helmfile: dict | None,
) -> HelmRelease:
    chart       = ""
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
    meta     = raw.get("metadata", {})
    involved = raw.get("involvedObject", {})

    first_ts = _parse_ts(raw.get("firstTimestamp") or raw.get("eventTime"))
    last_ts  = _parse_ts(raw.get("lastTimestamp"))

    return K8sEvent(
        uid=meta.get("uid") or f"evt:{meta.get('namespace','')}/{meta.get('name', str(uuid.uuid4())[:8])}",
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _detect_policy_source(report: dict) -> str:
    labels = report.get("metadata", {}).get("labels", {})
    if "app.kubernetes.io/managed-by" in labels:
        mgr = labels["app.kubernetes.io/managed-by"].lower()
        if "kyverno" in mgr:
            return "kyverno"
    for k in labels:
        if "kyverno" in k.lower():
            return "kyverno"
        if "gatekeeper" in k.lower():
            return "gatekeeper"
    annotations = report.get("metadata", {}).get("annotations", {})
    if any("kyverno" in k.lower() for k in annotations):
        return "kyverno"
    source = labels.get("source", "")
    if source:
        return source.lower()
    return "unknown"


def _find_entity(graph: OntologyGraph, kind: str, name: str, namespace: str):
    for entity in graph.entities():
        ek = entity.kind.value if hasattr(entity.kind, "value") else str(entity.kind)
        if ek != kind:
            continue
        if entity.name != name:
            continue
        if namespace and entity.namespace and entity.namespace != namespace:
            continue
        return entity
    return None
