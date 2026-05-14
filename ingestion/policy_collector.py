"""
PolicyCollector — ingests OPA / Kyverno policy violations into the OntologyGraph.

Supported sources
─────────────────
  PolicyReport          wgpolicyk8s.io/v1alpha2  (namespaced — Kyverno / OPA)
  ClusterPolicyReport   wgpolicyk8s.io/v1alpha2  (cluster-scoped)
  MutatingWebhookConfiguration  admissionregistration.k8s.io/v1

Graph mutations
───────────────
  • Adds PolicyViolation nodes  (one per failing/warning result)
  • Adds MutatingWebhook nodes  (one per MutatingWebhookConfiguration)
  • Adds HAS_POLICY_VIOLATION edges from the correlated K8s entity → PolicyViolation
  • Annotates correlated entities with  policy.<policyname>.result / severity / message

The collector is optional — if the wgpolicyk8s.io CRD group is not installed
(404 ApiException), it logs a debug message and returns zeros gracefully.

Usage
─────
    from ingestion.policy_collector import PolicyCollector

    collector = PolicyCollector()
    result = collector.collect(graph, namespaces=["production", "default"])
    print(f"fail={result.fail_count}  audit={result.audit_count}  webhooks={result.mutation_webhooks}")
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from kubernetes import client as k8s_client
from kubernetes.client import ApiException

from ontology.entities import MutatingWebhook, PolicyViolation, ResourceKind
from ontology.graph import OntologyGraph
from ontology.relationships import Edge, RelationshipType

log = logging.getLogger(__name__)

_GROUP   = "wgpolicyk8s.io"
_VERSION = "v1alpha2"
_POLICY_REPORT         = "policyreports"
_CLUSTER_POLICY_REPORT = "clusterpolicyreports"

# result values that are treated as FAIL (boost +0.10 each)
_FAIL_RESULTS  = frozenset({"fail", "error"})
# result values treated as audit / warn (boost +0.05 total)
_AUDIT_RESULTS = frozenset({"warn"})


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class PolicyCollectResult:
    fail_count: int = 0
    audit_count: int = 0
    mutation_webhooks: int = 0
    violations_added: int = 0
    entities_annotated: int = 0


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------

class PolicyCollector:
    """
    Queries the K8s API for PolicyReport CRDs and MutatingWebhookConfigurations,
    wires violations into the graph, and returns violation counts for the
    confidence scorer.

    Parameters
    ----------
    api_client:
        Optional pre-configured kubernetes.client.ApiClient. When None the
        collector re-uses the in-cluster or kubeconfig credentials that were
        already loaded by K8sCollector.
    timeout:
        Per-request HTTP timeout in seconds.
    """

    def __init__(
        self,
        api_client: k8s_client.ApiClient | None = None,
        timeout: int = 30,
    ) -> None:
        self._custom   = k8s_client.CustomObjectsApi(api_client)
        self._admission = k8s_client.AdmissionregistrationV1Api(api_client)
        self._timeout  = timeout

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def collect(
        self,
        graph: OntologyGraph,
        namespaces: list[str] | None = None,
    ) -> PolicyCollectResult:
        """
        Fetch violations from all available PolicyReport CRDs and wire them
        into *graph*.  Silently skips if the CRD group is not installed.

        Parameters
        ----------
        graph:
            Live OntologyGraph already populated by K8sCollector.
        namespaces:
            Restrict namespaced PolicyReport fetch to these namespaces.
            None = all namespaces.
        """
        result = PolicyCollectResult()

        # Namespaced PolicyReports
        reports = self._fetch_namespaced_reports(namespaces)
        for report in reports:
            self._process_report(report, graph, result)

        # ClusterPolicyReports
        cluster_reports = self._fetch_cluster_reports()
        for report in cluster_reports:
            self._process_report(report, graph, result, cluster=True)

        # MutatingWebhookConfigurations
        result.mutation_webhooks = self._collect_webhooks(graph)

        log.info(
            "policy: fail=%d  audit=%d  webhooks=%d  nodes_added=%d  entities_annotated=%d",
            result.fail_count,
            result.audit_count,
            result.mutation_webhooks,
            result.violations_added,
            result.entities_annotated,
        )
        return result

    def is_available(self) -> bool:
        """Return True if the wgpolicyk8s.io API group exists on the cluster."""
        try:
            self._custom.list_cluster_custom_object(
                _GROUP, _VERSION, _CLUSTER_POLICY_REPORT,
                limit=1, _request_timeout=self._timeout,
            )
            return True
        except ApiException as exc:
            return exc.status != 404
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Fetch
    # ------------------------------------------------------------------

    def _fetch_namespaced_reports(self, namespaces: list[str] | None) -> list[dict]:
        reports: list[dict] = []
        try:
            if namespaces:
                for ns in namespaces:
                    resp = self._custom.list_namespaced_custom_object(
                        _GROUP, _VERSION, ns, _POLICY_REPORT,
                        _request_timeout=self._timeout,
                    )
                    reports.extend(resp.get("items", []))
            else:
                resp = self._custom.list_cluster_custom_object(
                    _GROUP, _VERSION, _POLICY_REPORT,
                    _request_timeout=self._timeout,
                )
                reports.extend(resp.get("items", []))
        except ApiException as exc:
            if exc.status == 404:
                log.debug("policy: wgpolicyk8s.io PolicyReport CRD not installed — skipping")
            else:
                log.warning("policy: failed to fetch PolicyReports: %s", exc)
        except Exception as exc:
            log.warning("policy: unexpected error fetching PolicyReports: %s", exc)
        return reports

    def _fetch_cluster_reports(self) -> list[dict]:
        try:
            resp = self._custom.list_cluster_custom_object(
                _GROUP, _VERSION, _CLUSTER_POLICY_REPORT,
                _request_timeout=self._timeout,
            )
            return resp.get("items", [])
        except ApiException as exc:
            if exc.status == 404:
                log.debug("policy: ClusterPolicyReport CRD not installed — skipping")
            else:
                log.warning("policy: failed to fetch ClusterPolicyReports: %s", exc)
        except Exception as exc:
            log.warning("policy: unexpected error fetching ClusterPolicyReports: %s", exc)
        return []

    # ------------------------------------------------------------------
    # Processing
    # ------------------------------------------------------------------

    def _process_report(
        self,
        report: dict,
        graph: OntologyGraph,
        result: PolicyCollectResult,
        *,
        cluster: bool = False,
    ) -> None:
        meta      = report.get("metadata", {})
        report_ns = meta.get("namespace", "")
        source    = _detect_source(report)

        for item in report.get("results", []):
            r = item.get("result", "").lower()
            if r not in (_FAIL_RESULTS | _AUDIT_RESULTS):
                continue  # skip "pass" / "skip"

            policy   = item.get("policy", "")
            rule     = item.get("rule", "")
            message  = item.get("message", "")
            severity = item.get("severity", "")

            for res_ref in item.get("resources", []) or [_cluster_ref(item)]:
                if not res_ref:
                    continue
                r_kind = res_ref.get("kind", "")
                r_name = res_ref.get("name", "")
                r_ns   = res_ref.get("namespace", "") or report_ns

                uid = _violation_uid(policy, rule, r_kind, r_ns, r_name)

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
                result.violations_added += 1

                if r in _FAIL_RESULTS:
                    result.fail_count += 1
                else:
                    result.audit_count += 1

                # Correlate + annotate the affected K8s entity
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
                    result.entities_annotated += 1
                    log.info(
                        "policy: %s/%s/%s ← %s/%s [%s]",
                        r_kind, r_ns, r_name, policy, rule, r,
                    )

    def _collect_webhooks(self, graph: OntologyGraph) -> int:
        count = 0
        try:
            resp = self._admission.list_mutating_webhook_configuration(
                _request_timeout=self._timeout,
            )
            for item in resp.items:
                meta = item.metadata
                uid  = f"mutating-webhook-{meta.name}"
                fp   = getattr(item, "webhooks", None)
                failure_policy = ""
                matched: list[str] = []
                if fp:
                    for wh in fp:
                        failure_policy = getattr(wh, "failure_policy", "") or ""
                        for rule in getattr(wh, "rules", []) or []:
                            for grp in getattr(rule, "api_groups", []) or [""]:
                                for res in getattr(rule, "resources", []) or []:
                                    matched.append(f"{grp}/{res}")

                webhook = MutatingWebhook(
                    uid=uid,
                    name=meta.name,
                    failure_policy=failure_policy,
                    matched_resources=matched[:10],
                )
                graph.add_entity(webhook)
                count += 1
                log.debug("policy: MutatingWebhookConfiguration %s added", meta.name)
        except ApiException as exc:
            log.warning("policy: failed to fetch MutatingWebhookConfigurations: %s", exc)
        except Exception as exc:
            log.warning("policy: unexpected error fetching webhooks: %s", exc)
        return count


# ---------------------------------------------------------------------------
# Remediation hints
# ---------------------------------------------------------------------------

def policy_fix_hints(graph: OntologyGraph) -> list[str]:
    """
    Generate human-readable remediation commands for each policy violation.
    Called by ContextBuilder to populate anchor_fixes-style suggestions.
    """
    hints: list[str] = []
    for entity in graph.entities(ResourceKind.POLICY_VIOLATION):
        if not isinstance(entity, PolicyViolation):
            continue
        if not entity.is_fail:
            continue

        resource = f"{entity.resource_kind}/{entity.resource_namespace}/{entity.resource_name}"
        source   = entity.source

        if source == "kyverno":
            hints.append(
                f"{resource}  policy={entity.policy} rule={entity.rule}  →  "
                f"kubectl describe clusterpolicy {entity.policy} | "
                f"kyverno test ."
            )
        elif source == "gatekeeper":
            hints.append(
                f"{resource}  constraint={entity.policy}  →  "
                f"kubectl describe constraint {entity.policy}"
            )
        else:
            ns_flag = f"-n {entity.resource_namespace}" if entity.resource_namespace else ""
            hints.append(
                f"{resource}  policy={entity.policy}  →  "
                f"kubectl get policyreport {ns_flag} -o yaml"
            )

    return hints[:10]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _violation_uid(policy: str, rule: str, kind: str, ns: str, name: str) -> str:
    parts = [policy, rule, kind, ns or "cluster", name]
    slug = "-".join(p.lower().replace("/", "_").replace(".", "-") for p in parts)
    return f"policy-violation-{slug}"[:128]


def _detect_source(report: dict) -> str:
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
    # Kyverno sets this annotation
    annotations = report.get("metadata", {}).get("annotations", {})
    if any("kyverno" in k.lower() for k in annotations):
        return "kyverno"
    # OPA Gatekeeper PolicyReport source label
    source = report.get("metadata", {}).get("labels", {}).get("source", "")
    if source:
        return source.lower()
    return "unknown"


def _cluster_ref(item: dict) -> dict:
    """For ClusterPolicyReport entries that reference a cluster-scoped resource."""
    res = item.get("resource")
    if isinstance(res, dict):
        return res
    return {}


def _find_entity(
    graph: OntologyGraph,
    kind: str,
    name: str,
    namespace: str,
) -> object | None:
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
