"""
Native Kubernetes integration-case loader.

Reads cases from ``tests/integration/cases/h*/``.  Each case directory has:

  kube/       — kubectl get -o yaml output (pods, deployments, events as YAML)
  helm/       — values.yaml (declared) + release.json (live deployed values)
  helmfile/   — helmfile.yaml (optional)
  policy/     — PolicyReport YAML from Kyverno/OPA (optional)
  otel/       — OTLP-derived error-trace fixtures (optional) — JSON files, each
                a list of normalized trace dicts in the same shape
                ``OtelBackend.search_error_traces()`` returns live (see
                ingestion/otel_backend.py):
                  {trace_id, service_name, status, duration_ms, span_count,
                   error_message, root_span, error_spans, started_at}
                Served to the real ``OtelCollector.collect()`` by a fixture
                backend that filters on ``service_name`` like a live one, so
                target selection (``Pod.needs_telemetry``, degraded
                workloads), service-label resolution, ``HAS_TRACE`` edges and
                annotations are the live code path. Traces for a service absent
                from the snapshot are never collected.
  prometheus/ — firing-alert fixtures (optional) — JSON files, each a list of
                raw alerts in the exact shape Prometheus's
                ``GET /api/v1/alerts`` returns (see
                ingestion/prometheus_collector.py):
                  {state, labels: {alertname, severity, namespace, pod|
                   deployment|statefulset|daemonset|service|node, ...},
                   annotations: {summary, description}, activeAt}
                Fed straight into the real ``PrometheusCollector.collect()``
                (its HTTP fetch is swapped for the fixture list) so
                label→entity correlation and annotation shape are identical
                to a live cluster — no correlation logic duplicated here.
  loki/       — log-stream fixtures (optional) — JSON files, each a list of
                streams in the shape of Loki's ``query_range`` ``data.result``:
                  {stream: {k8s_pod_name, k8s_namespace_name, ...},
                   values: [[ts_ns, line], ...]}
                Served to the real ``LokiSource.collect()`` (only its private
                ``_query`` is replaced, matching the LogQL label matchers
                against each stream like Loki does), so pod selection
                (``Pod.needs_telemetry``), LogQL, level detection, ``LokiLog``
                nodes and ``HAS_LOG`` edges are the live code path.
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
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from ingestion.helm_drift import HelmDriftDetector
from ingestion.loki_source import LokiSource
from ingestion.otel_backend import OtelBackend, in_namespace
from ingestion.otel_collector import OtelCollector
from ingestion.prometheus_collector import PrometheusCollector
from ontology.entities import (
    Deployment, HelmRelease, K8sEvent, Namespace, PersistentVolumeClaim, Pod,
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
    otel_dir     = case_dir / "otel"
    prom_dir     = case_dir / "prometheus"
    loki_dir     = case_dir / "loki"

    values_path   = helm_dir / "values.yaml"
    helmfile_path = helmfile_dir / "helmfile.yaml"

    case: dict[str, Any] = {
        "case_dir":      case_dir,
        "case_name":     case_dir.name,
        "helm_values":   yaml.safe_load(values_path.read_text()) if values_path.exists() else {},
        "helmfile":      yaml.safe_load(helmfile_path.read_text()) if helmfile_path.exists() else None,
        "observed":      _load_kube(kube_dir),
        "policy_reports": _load_policy_reports(policy_dir),
        "otel_traces":   _load_otel(otel_dir),
        "prometheus_alerts": _load_prometheus_alerts(prom_dir),
        "loki_streams":  _load_loki_streams(loki_dir),
        "expect":        json.loads((case_dir / "expect.json").read_text()),
    }

    # Live deployed values from helm/release.json
    release_path = helm_dir / "release.json"
    if release_path.exists():
        case["observed"]["helm_release_values"] = json.loads(release_path.read_text())

    return case


def build_graph(case: dict, wire_signals: bool = True) -> OntologyGraph:
    """
    Build an OntologyGraph from a loaded native case dict.
    Runs HelmDriftDetector on the result; the workflow's own nodes do the rest.

    ``wire_signals=False`` builds the Kubernetes-only graph: the Prometheus /
    Loki / OTel fixtures are left for the workflow's own collector nodes to
    fetch (see ``tests/integration/workflow_harness.py``).
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

    # ── 2. Deployments, 3. Pods ─────────────────────────────────────────────
    # Linked to the case's release by the production rule (release-name annotation, else the
    # instance label), not all of them: a decoy of another release is not its resource
    # (docs/fix-plan-001.md, H26 / D2.1).
    from ingestion.helm_collector import belongs_to_release
    for raw, build in ([(d, _deployment_from_kubectl) for d in case["observed"].get("deployments", [])]
                       + [(p, _pod_from_kubectl) for p in case["observed"].get("pods", [])]):
        entity = build(raw)
        graph.add_entity(entity)
        if belongs_to_release(entity, helm_release.name):
            graph.add_edge(Edge(entity.uid, helm_release.uid, RelationshipType.MANAGED_BY_HELM))

    if wire_signals:
        # ── 3b. OTel error traces (fixture) ────────────────────────────────
        _wire_otel_traces(graph, case.get("otel_traces", []))

        # ── 3c. Prometheus firing alerts (fixture) ─────────────────────────
        _wire_prometheus_alerts(graph, case.get("prometheus_alerts", []))

        # ── 3d. Loki log streams (fixture) ─────────────────────────────────
        _wire_loki_logs(graph, case.get("loki_streams", []), case["expect"].get("query", ""))

    # ── 3e. PersistentVolumeClaims — built as K8sCollector._collect_pvcs builds them
    for pvc_raw in case["observed"].get("pvcs", []):
        meta, spec = pvc_raw.get("metadata", {}), pvc_raw.get("spec", {})
        graph.add_entity(PersistentVolumeClaim(
            uid=meta.get("uid") or f"pvc:{meta.get('namespace', '')}/{meta.get('name', '')}",
            name=meta.get("name", ""), namespace=meta.get("namespace"), labels=meta.get("labels", {}),
            requested_storage=((spec.get("resources") or {}).get("requests") or {}).get("storage", ""),
            access_modes=spec.get("accessModes") or [],
            status_phase=(pvc_raw.get("status") or {}).get("phase", "Pending"),
            storage_class=spec.get("storageClassName") or "", volume_name=spec.get("volumeName") or "",
            raw=pvc_raw))

    # ── 4. Events ───────────────────────────────────────────────────────────
    # linked to the object they are about, as K8sCollector._collect_events does (HAS_EVENT):
    # the loader built no such edge, so the graph walk never reached an event (H14)
    from ingestion.k8s_collector import K8sCollector
    for evt_raw in case["observed"].get("events", []):
        evt = _event_from_kubectl(evt_raw)
        graph.add_entity(evt)
        involved = K8sCollector._find_by_name(graph, evt.involved_name, evt.namespace, evt.involved_kind)
        if involved:
            graph.add_edge(Edge(involved.uid, evt.uid, RelationshipType.HAS_EVENT))

    # ── 5. Namespaces — as K8sCollector does: one Namespace, IN_NAMESPACE from each object.
    # ResourceQuotas are not typed live (fix plan: "Not in this PR"), so the loader builds no
    # ResourceQuota and no QUOTA_BLOCKS edge either (H14, F6): the fixture's quota.yaml stays
    # as data, as the live discovery would carry it.
    for ns_name in sorted({e.namespace for e in graph.entities() if e.namespace}):
        ns_entity = _find_entity(graph, "Namespace", ns_name, "")
        if ns_entity is None:
            ns_entity = Namespace(uid=f"ns-{ns_name}", name=ns_name)
            graph.add_entity(ns_entity)
        for e in list(graph.entities()):
            if e.namespace == ns_name and e.kind.value in ("Pod", "Deployment", "StatefulSet",
                                                           "DaemonSet", "Service"):
                graph.add_edge(Edge(e.uid, ns_entity.uid, RelationshipType.IN_NAMESPACE))

    # ── 6. Value drift: values.yaml vs helm/release.json — not made here ───
    # Production has no path that compares a values file to the deployed values (the GitOps
    # render uses the deployed values, docs/fix-plan-001.md F11 / H48): a drift written here was
    # evidence only the test could see. The fixture's helm/release.json stays as data.

    # ── 6b. Declared → applied → live (docs/three-states.md), the production comparisons ──
    # helm/manifest.yaml is what `helm get manifest` returns; rendered/declared.yaml (h012:
    # rendered/expected.yaml) is Git's `helm template`. Both are renders of the case's chart/,
    # checked by a helm-guarded test.
    case_dir = Path(case["case_dir"])
    applied_path = case_dir / "helm" / "manifest.yaml"
    declared_path = next((p for p in (case_dir / "rendered" / "declared.yaml",
                                      case_dir / "rendered" / "expected.yaml") if p.exists()), None)
    if applied_path.exists():
        from ingestion.manifest_differ import ManifestDiffer, compare_applied_to_live
        helm_release.applied_manifest = [d for d in yaml.safe_load_all(applied_path.read_text())
                                         if isinstance(d, dict) and d.get("kind")]
        # a kind the fixture does not hold was not "listed": its objects are unknown, not missing
        listed = {k for k, key in (("Deployment", "deployments"), ("StatefulSet", "statefulsets"),
                                   ("Service", "services"), ("ConfigMap", "configmaps"))
                  if case["observed"].get(key)}
        compare_applied_to_live(graph, covered=lambda kind, _ns: kind in listed)   # B
        if declared_path is not None:                                           # A
            declared = [d for d in yaml.safe_load_all(declared_path.read_text()) if isinstance(d, dict)]
            ManifestDiffer(track_orphans=False).diff_manifests(
                declared, helm_release.applied_manifest, graph,
                release_uid=helm_release.uid, namespace=ns)

    # ── 7. Policy violations ─────────────────────────────────────────────
    # read by the production PolicyCollector's own parser, not a loader copy (H14)
    from ingestion.policy_collector import PolicyCollectResult, PolicyCollector
    policy = PolicyCollector.__new__(PolicyCollector)
    policy_result = PolicyCollectResult()
    for report in case.get("policy_reports", []):
        policy._process_report(report, graph, policy_result)

    # ── 8. Helm drift detection (pod OOMKilled, replica mismatch…) ─────────
    HelmDriftDetector().detect_all(graph)

    # ── 9. Anchors: made by the workflow's own anchor node on this graph. The loader's call
    # here was to a method that does not exist, inside `except: pass` — it never ran (H14).

    # ── 10. Missing deployment dependencies ─────────────────────────────────
    _detect_missing_deps(graph, case["observed"])

    return graph


# ---------------------------------------------------------------------------
# Kube artefact loader
# ---------------------------------------------------------------------------

def _load_kube(kube_dir: Path) -> dict[str, Any]:
    """Load kubectl YAML/JSON output from kube/ — all resource kinds."""
    out: dict[str, Any] = {
        "deployments":         [],
        "pods":                [],
        "events":              [],
        "secrets":             [],   # present secrets (names only needed)
        "configmaps":          [],
        "serviceaccounts":     [],
        "networkpolicies":     [],
        "pvcs":                [],
        "rbac":                [],   # roles, rolebindings, clusterroles, clusterrolebindings
        "resource_quotas":     [],
        "helm_release_values": None,
    }
    if not kube_dir.is_dir():
        return out

    # Also recurse one level (e.g. kube/rbac/)
    files: list[Path] = []
    for p in sorted(kube_dir.iterdir()):
        if p.is_dir():
            files.extend(sorted(p.glob("*.yaml")) + sorted(p.glob("*.yml")) + sorted(p.glob("*.json")))
        elif p.suffix in (".yaml", ".yml", ".json"):
            files.append(p)

    for fpath in files:
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
            elif kind == "secret":
                out["secrets"].append(raw)
            elif kind == "secretlist":
                out["secrets"].extend(raw.get("items", []))
            elif kind == "configmap":
                out["configmaps"].append(raw)
            elif kind == "configmaplist":
                out["configmaps"].extend(raw.get("items", []))
            elif kind == "serviceaccount":
                out["serviceaccounts"].append(raw)
            elif kind == "networkpolicy":
                out["networkpolicies"].append(raw)
            elif kind in ("persistentvolumeclaim", "pvc"):
                out["pvcs"].append(raw)
            elif kind in ("role", "rolebinding", "clusterrole", "clusterrolebinding"):
                out["rbac"].append(raw)
            elif kind == "resourcequota":
                out["resource_quotas"].append(raw)
            elif kind == "resourcequotalist":
                out["resource_quotas"].extend(raw.get("items", []))

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
            api_group = api.split("/")[0] if "/" in api else ""
            if api_group == "wgpolicyk8s.io" and "PolicyReport" in kind:
                reports.append(doc)

    return reports


# ---------------------------------------------------------------------------
# OTel trace fixture loader
# ---------------------------------------------------------------------------

def _load_otel(otel_dir: Path) -> list[dict]:
    """Load normalized OTel error-trace fixtures from otel/*.json.

    Each file is a JSON list of trace dicts in the same shape
    OtelBackend.search_error_traces() returns live. A trace is served to the
    collector when its ``service_name`` matches the service resolved for an
    entity, exactly as a live backend would answer.
    """
    traces: list[dict] = []
    if not otel_dir.is_dir():
        return traces
    for fpath in sorted(otel_dir.glob("*.json")):
        content = json.loads(fpath.read_text())
        if isinstance(content, list):
            traces.extend(t for t in content if isinstance(t, dict))
    return traces


class _FixtureOtelBackend(OtelBackend):
    """Serves fixture traces to the real OtelCollector, filtered by service and namespace like a live backend."""

    def __init__(self, traces: list[dict]) -> None:
        super().__init__(url="fixture://otel")
        self._traces = traces

    def search_error_traces(self, service, namespace, start_ts, end_ts, limit=20):
        return [
            t for t in self._traces
            if t.get("service_name") == service and in_namespace(t, namespace)
        ][:limit]

    def get_trace(self, trace_id):
        return next((t for t in self._traces if t.get("trace_id") == trace_id), None)


def _wire_otel_traces(graph: OntologyGraph, traces: list[dict]) -> None:
    """Correlate fixture traces with graph entities via the real collector.

    OtelCollector.collect() runs unchanged — target selection (Pod.needs_telemetry,
    degraded workloads), service-name resolution, OtelTrace nodes, HAS_TRACE edges
    and otel.trace.* annotations. Only the backend is a fixture, and like a live
    backend it answers by service, so a trace for a service absent from the
    snapshot is never collected.
    """
    if not traces:
        return
    OtelCollector(_FixtureOtelBackend(traces)).collect(graph)


# ---------------------------------------------------------------------------
# Prometheus alert fixture loader
# ---------------------------------------------------------------------------

def _load_prometheus_alerts(prom_dir: Path) -> list[dict]:
    """Load raw ``/api/v1/alerts``-shaped fixtures from prometheus/*.json."""
    alerts: list[dict] = []
    if not prom_dir.is_dir():
        return alerts
    for fpath in sorted(prom_dir.glob("*.json")):
        content = json.loads(fpath.read_text())
        if isinstance(content, list):
            alerts.extend(a for a in content if isinstance(a, dict))
    return alerts


def _wire_prometheus_alerts(graph: OntologyGraph, alerts: list[dict]) -> None:
    """Correlate fixture alerts with graph entities via the real collector.

    PrometheusCollector's HTTP fetch is swapped for the fixture list, so
    label→entity correlation, PrometheusAlert nodes, HAS_ALERT edges and
    alert.* annotations are exactly what a live collect() produces — no
    correlation logic is re-implemented here.
    """
    if not alerts:
        return
    collector = PrometheusCollector(url="http://fixture.invalid")
    collector._fetch_alerts = lambda: alerts
    collector.collect(graph)


# ---------------------------------------------------------------------------
# Loki log-stream fixture loader
# ---------------------------------------------------------------------------

_LOGQL_MATCHER = re.compile(r'(\w+)="([^"]*)"')


def _load_loki_streams(loki_dir: Path) -> list[dict]:
    """Load Loki ``data.result`` streams ({stream, values}) from loki/*.json."""
    streams: list[dict] = []
    if not loki_dir.is_dir():
        return streams
    for fpath in sorted(loki_dir.glob("*.json")):
        content = json.loads(fpath.read_text())
        if isinstance(content, list):
            streams.extend(s for s in content if isinstance(s, dict))
    return streams


def _wire_loki_logs(graph: OntologyGraph, streams: list[dict], query: str = "") -> None:
    """Serve fixture log streams to the real LokiSource.collect().

    Only the private _query is replaced. Like Loki it matches the LogQL label
    matchers against each stream's labels and answers newest-first
    (direction=backward), so which pods get queried (Pod.needs_telemetry), the
    LogQL built for them, level detection, LokiLog nodes and HAS_LOG edges are
    the live code path. Fixtures are frozen in time, so the query time window is
    not applied.
    """
    if not streams:
        return

    source = LokiSource(url="http://fixture.invalid")
    source._query = fixture_loki_query(streams)
    source.collect(graph, query=query)       # the workload the query names is read too (S1)


def fixture_loki_query(streams: list[dict]):
    """A stand-in for ``LokiSource._query`` answering from fixture streams.

    Like Loki it matches the LogQL label matchers against each stream's labels
    and answers newest-first; the time window is not applied (frozen fixtures).
    """
    def _query(logql: str, start_ns: int, end_ns: int) -> list[tuple[int, str]]:
        selector, _, line_filter = logql.partition(" |~ ")
        wanted = dict(_LOGQL_MATCHER.findall(selector))
        keep = re.compile(line_filter.strip("`").removeprefix("(?i)"), re.I) if line_filter else None
        rows = [
            (int(ts), line)
            for s in streams
            if all(s.get("stream", {}).get(k) == v for k, v in wanted.items())
            for ts, line in s.get("values", [])
            if keep is None or keep.search(line)
        ]
        rows.sort(key=lambda r: r[0], reverse=True)
        return rows

    return _query


# ---------------------------------------------------------------------------
# Missing deployment dependency detection
# ---------------------------------------------------------------------------

def _detect_missing_deps(graph: OntologyGraph, observed: dict) -> None:
    """
    Annotate each Pod with the dependencies it references but that are not there.

    Delegates to the production detector (``ingestion.readiness.detect_missing``),
    so the offline cases prove the code that runs live (docs/test-cases.md §
    *Live parity*). The fixture's ``kube/`` listings stand for what the account can
    list; Secrets are never listed (decision B, docs/diagnostics.md § Step 4): a
    Secret is missing only where the fixture's events say so.
    """
    from ingestion.readiness import Present, detect_missing

    def names(key: str) -> set[str]:
        return {r.get("metadata", {}).get("name", "") for r in observed.get(key, [])}

    bound: set[str] = set()
    for rb in observed.get("rbac", []):
        if rb.get("kind", "").lower() in ("rolebinding", "clusterrolebinding"):
            for subj in rb.get("subjects", []):
                if subj.get("kind", "").lower() == "serviceaccount":
                    bound.add(subj.get("name", ""))

    # RoleBindings and NetworkPolicies are not collected live (K8sCollector passes None), so
    # their checks are skipped here too: missing.rbac.* / netpol.* existed only offline (H14,
    # docs/test-cases.md § Live parity, divergence 1b)
    del bound
    present = Present(
        configmaps=names("configmaps"),
        pvcs=names("pvcs"),
        serviceaccounts=names("serviceaccounts"),
        networkpolicies=observed.get("networkpolicies", []),     # collected live since S4
        messages=[ev.get("message", "") for ev in observed.get("events", [])],
    )
    for pod_raw in observed.get("pods", []):
        meta = pod_raw.get("metadata", {})
        entity = _find_entity(graph, "Pod", meta.get("name", ""), meta.get("namespace", ""))
        if entity is not None:
            entity.annotations.update(detect_missing(pod_raw, present))


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
        k8s_annotations=dict(meta.get("annotations") or {}),
        replicas=spec.get("replicas", 0),
        ready_replicas=status.get("readyReplicas", 0),
        available_replicas=status.get("availableReplicas", 0),
        strategy=spec.get("strategy", {}).get("type", "RollingUpdate"),
        selector=spec.get("selector", {}).get("matchLabels", {}),
        conditions=[{"type": c.get("type"), "status": c.get("status"), "reason": c.get("reason")}
                    for c in status.get("conditions", [])],
        containers=list(spec.get("template", {}).get("spec", {}).get("containers", [])),
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
        k8s_annotations=dict(meta.get("annotations") or {}),
        phase=status.get("phase", "Unknown"),
        node_name=spec.get("nodeName", ""),
        restart_count=total_restarts,
        container_statuses=container_statuses,
        init_container_statuses=status.get("initContainerStatuses", []),
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
# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
