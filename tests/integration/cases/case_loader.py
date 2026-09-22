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

from ingestion.anchor_engine import AnchorEngine
from ingestion.helm_drift import HelmDriftDetector
from ingestion.chart_parser import flatten_values
from ingestion.loki_source import LokiSource
from ingestion.otel_backend import OtelBackend
from ingestion.otel_collector import OtelCollector
from ingestion.prometheus_collector import PrometheusCollector
from ontology.entities import (
    Deployment, HelmRelease, K8sEvent, Namespace, Pod,
    PolicyViolation, ResourceQuota,
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

    # ── 3b. OTel error traces (fixture) ────────────────────────────────────
    _wire_otel_traces(graph, case.get("otel_traces", []))

    # ── 3c. Prometheus firing alerts (fixture) ─────────────────────────────
    _wire_prometheus_alerts(graph, case.get("prometheus_alerts", []))

    # ── 3d. Loki log streams (fixture) ─────────────────────────────────────
    _wire_loki_logs(graph, case.get("loki_streams", []))

    # ── 4. Events ───────────────────────────────────────────────────────────
    for evt_raw in case["observed"].get("events", []):
        evt = _event_from_kubectl(evt_raw)
        graph.add_entity(evt)

    # ── 5. ResourceQuotas ──────────────────────────────────────────────────
    for rq_raw in case["observed"].get("resource_quotas", []):
        rq = _resource_quota_from_kubectl(rq_raw)
        if rq is None:
            continue
        graph.add_entity(rq)
        if rq.namespace:
            ns_entity = _find_entity(graph, "Namespace", rq.namespace, "")
            if ns_entity is None:
                ns_entity = Namespace(uid=f"ns-{rq.namespace}", name=rq.namespace)
                graph.add_entity(ns_entity)
            if rq.exhausted_resources or rq.near_limit_resources:
                graph.add_edge(Edge(rq.uid, ns_entity.uid, RelationshipType.QUOTA_BLOCKS))

    # ── 6. Value drift: values.yaml vs helm/release.json ───────────────────
    live_values = case["observed"].get("helm_release_values")
    if live_values and case["helm_values"]:
        _annotate_value_drift(graph, helm_release, case["helm_values"], live_values)

    # ── 7. Policy violations ─────────────────────────────────────────────
    for report in case.get("policy_reports", []):
        _ingest_policy_report(report, graph)

    # ── 8. Helm drift detection (pod OOMKilled, replica mismatch…) ─────────
    HelmDriftDetector().detect_all(graph)

    # ── 9. Anchors from declared Helm values ────────────────────────────────
    try:
        AnchorEngine().annotate(graph)
    except Exception:
        pass  # AnchorEngine is best-effort in test context

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
    """Serves fixture traces to the real OtelCollector, filtered by service like a live backend."""

    def __init__(self, traces: list[dict]) -> None:
        super().__init__(url="fixture://otel")
        self._traces = traces

    def search_error_traces(self, service, namespace, start_ts, end_ts, limit=20):
        return [t for t in self._traces if t.get("service_name") == service][:limit]

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


def _wire_loki_logs(graph: OntologyGraph, streams: list[dict]) -> None:
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

    def _query(logql: str, start_ns: int, end_ns: int) -> list[tuple[int, str]]:
        wanted = dict(_LOGQL_MATCHER.findall(logql))
        rows = [
            (int(ts), line)
            for s in streams
            if all(s.get("stream", {}).get(k) == v for k, v in wanted.items())
            for ts, line in s.get("values", [])
        ]
        rows.sort(key=lambda r: r[0], reverse=True)
        return rows

    source = LokiSource(url="http://fixture.invalid")
    source._query = _query
    source.collect(graph)


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
# Missing deployment dependency detection
# ---------------------------------------------------------------------------

def _detect_missing_deps(graph: OntologyGraph, observed: dict) -> None:
    """
    Scan every pod spec for referenced Kubernetes resources.
    For each reference that has NO corresponding object in observed/kube/,
    annotate the Pod entity with ``missing.<type>.<name>`` so the
    ContextBuilder surfaces it and anchor_fix_hints() can generate a
    concrete ``kubectl create`` command.

    Detects:
      - envFrom  secretRef / configMapRef
      - env[].valueFrom  secretKeyRef / configMapKeyRef
      - volumes[]  secret / configMap / persistentVolumeClaim
      - imagePullSecrets
      - serviceAccountName (non-default)
      - NetworkPolicy entities → annotated on ALL pods in the namespace
      - Missing RBAC binding for a ServiceAccount
    """
    present_secrets = {
        r.get("metadata", {}).get("name", "")
        for r in observed.get("secrets", [])
    }
    present_cms = {
        r.get("metadata", {}).get("name", "")
        for r in observed.get("configmaps", [])
    }
    present_sas = {
        r.get("metadata", {}).get("name", "")
        for r in observed.get("serviceaccounts", [])
    }
    present_pvcs = {
        r.get("metadata", {}).get("name", "")
        for r in observed.get("pvcs", [])
    }
    # SA names that have at least one (Cluster)RoleBinding
    bound_sas: set[str] = set()
    for rb in observed.get("rbac", []):
        if rb.get("kind", "").lower() in ("rolebinding", "clusterrolebinding"):
            for subj in rb.get("subjects", []):
                if subj.get("kind", "").lower() == "serviceaccount":
                    bound_sas.add(subj.get("name", ""))

    netpols = observed.get("networkpolicies", [])

    for pod_raw in observed.get("pods", []):
        meta = pod_raw.get("metadata", {})
        spec = pod_raw.get("spec", {})
        pod_name = meta.get("name", "")
        ns = meta.get("namespace", "")

        entity = _find_entity(graph, "Pod", pod_name, ns)
        if entity is None:
            continue

        all_containers = (
            spec.get("containers", [])
            + spec.get("initContainers", [])
            + spec.get("ephemeralContainers", [])
        )

        for container in all_containers:
            # envFrom
            for ef in container.get("envFrom", []):
                sr = ef.get("secretRef", {})
                cr = ef.get("configMapRef", {})
                if sr.get("name") and sr["name"] not in present_secrets:
                    entity.annotations[f"missing.secret.{sr['name']}"] = (
                        f"Secret '{sr['name']}' referenced in envFrom "
                        f"(container {container.get('name','?')}) — not found in cluster"
                    )
                if cr.get("name") and cr["name"] not in present_cms:
                    entity.annotations[f"missing.configmap.{cr['name']}"] = (
                        f"ConfigMap '{cr['name']}' referenced in envFrom "
                        f"(container {container.get('name','?')}) — not found in cluster"
                    )
            # env valueFrom
            for env in container.get("env", []):
                vf = env.get("valueFrom", {})
                skr = vf.get("secretKeyRef", {})
                ckr = vf.get("configMapKeyRef", {})
                if skr.get("name") and skr["name"] not in present_secrets:
                    entity.annotations[f"missing.secret.{skr['name']}"] = (
                        f"Secret '{skr['name']}' referenced in env.valueFrom.secretKeyRef "
                        f"(key {skr.get('key','?')}) — not found in cluster"
                    )
                if ckr.get("name") and ckr["name"] not in present_cms:
                    entity.annotations[f"missing.configmap.{ckr['name']}"] = (
                        f"ConfigMap '{ckr['name']}' referenced in env.valueFrom.configMapKeyRef "
                        f"(key {ckr.get('key','?')}) — not found in cluster"
                    )

        # volumes
        for vol in spec.get("volumes", []):
            sec = vol.get("secret", {})
            cm  = vol.get("configMap", {})
            pvc = vol.get("persistentVolumeClaim", {})
            if sec.get("secretName") and sec["secretName"] not in present_secrets:
                entity.annotations[f"missing.secret.{sec['secretName']}"] = (
                    f"Secret '{sec['secretName']}' mounted as volume '{vol.get('name','')}' — not found"
                )
            if cm.get("name") and cm["name"] not in present_cms:
                entity.annotations[f"missing.configmap.{cm['name']}"] = (
                    f"ConfigMap '{cm['name']}' mounted as volume '{vol.get('name','')}' — not found"
                )
            if pvc.get("claimName") and pvc["claimName"] not in present_pvcs:
                entity.annotations[f"missing.pvc.{pvc['claimName']}"] = (
                    f"PVC '{pvc['claimName']}' referenced as volume '{vol.get('name','')}' — not found or not bound"
                )

        # imagePullSecrets
        for ips in spec.get("imagePullSecrets", []):
            sn = ips.get("name", "")
            if sn and sn not in present_secrets:
                entity.annotations[f"missing.imagepullsecret.{sn}"] = (
                    f"imagePullSecret '{sn}' not found — image pull will fail (401/403)"
                )

        # serviceAccount
        sa = spec.get("serviceAccountName", "")
        if sa and sa not in ("default", ""):
            if sa not in present_sas:
                entity.annotations[f"missing.serviceaccount.{sa}"] = (
                    f"ServiceAccount '{sa}' not found in cluster (namespace {ns})"
                )
            elif sa not in bound_sas:
                entity.annotations[f"missing.rbac.{sa}"] = (
                    f"ServiceAccount '{sa}' exists but has no (Cluster)RoleBinding — "
                    f"API calls will be Forbidden"
                )

        # NetworkPolicy — flag if any netpol targets pods in this namespace
        for np in netpols:
            np_ns = np.get("metadata", {}).get("namespace", "")
            if np_ns and np_ns != ns:
                continue
            np_name = np.get("metadata", {}).get("name", "np-unknown")
            pod_sel = np.get("spec", {}).get("podSelector", {})
            # Empty podSelector = applies to all pods in namespace
            match_labels = pod_sel.get("matchLabels", {})
            pod_labels = meta.get("labels", {})
            matches = not match_labels or all(
                pod_labels.get(k) == v for k, v in match_labels.items()
            )
            if matches:
                spec_np = np.get("spec", {})
                if "egress" in spec_np and not spec_np.get("egress"):
                    entity.annotations[f"netpol.{np_name}.egress_blocked"] = (
                        f"NetworkPolicy '{np_name}' selects this pod with empty egress rules "
                        f"— ALL outbound traffic is blocked"
                    )
                elif "egress" not in spec_np and "ingress" in spec_np:
                    pass  # ingress-only policy, egress unaffected
                else:
                    entity.annotations[f"netpol.{np_name}.applied"] = (
                        f"NetworkPolicy '{np_name}' applies to this pod — "
                        f"verify egress/ingress rules allow required traffic"
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


def _resource_quota_from_kubectl(raw: dict) -> ResourceQuota | None:
    meta = raw.get("metadata", {})
    name = meta.get("name", "")
    ns = meta.get("namespace", "")
    if not name:
        return None
    spec = raw.get("spec", {})
    status = raw.get("status", {})
    uid = meta.get("uid") or f"resourcequota-{ns}-{name}"
    return ResourceQuota(
        uid=uid,
        name=name,
        namespace=ns or None,
        labels=meta.get("labels", {}),
        hard=spec.get("hard", {}),
        used=status.get("used", {}),
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
