"""
MetricsServerCollector — fetches live pod CPU/memory from the Kubernetes
metrics-server (metrics.k8s.io/v1beta1) and annotates OntologyGraph pods.

Queries per namespace that appears in the graph (or all namespaces when the
graph has pods without a namespace). Uses the same kubeconfig loading pattern
as K8sCollector.

Annotations written per pod
────────────────────────────
  metrics.cpu_m        total CPU across all containers (millicores, float)
  metrics.memory_mi    total memory across all containers (MiB, float)

These annotations are later consumed by SignalAnalyzer to seed PatchTST
signals with real current resource usage instead of purely synthetic data.
"""
from __future__ import annotations

import logging

from kubernetes import client as k8s_client
from kubernetes import config as k8s_config
from kubernetes.client import ApiException

import config as cfg
from ontology.entities import Pod, ResourceKind
from ontology.graph import OntologyGraph

log = logging.getLogger(__name__)


class MetricsServerCollector:
    """
    Annotates Pod entities with live CPU/memory readings from metrics-server.

    Parameters
    ----------
    kubeconfig: Path to kubeconfig file (None = in-cluster or default)
    context:    Kubeconfig context to use
    """

    def __init__(
        self,
        kubeconfig: str | None = None,
        context: str | None = None,
    ) -> None:
        kc = kubeconfig or cfg.KUBECONFIG
        ctx = context or cfg.KUBE_CONTEXT

        if kc:
            k8s_config.load_kube_config(config_file=kc, context=ctx)
        else:
            try:
                k8s_config.load_incluster_config()
            except k8s_config.ConfigException:
                k8s_config.load_kube_config(context=ctx)

        api_client = k8s_client.ApiClient()
        self._custom = k8s_client.CustomObjectsApi(api_client)

    def collect(self, graph: OntologyGraph) -> int:
        """
        Fetch pod metrics and annotate matched pods in the graph.
        Returns the number of pods annotated.
        """
        # Collect all namespaces present in the graph for scoped queries
        namespaces: set[str] = {
            e.namespace
            for e in graph.entities(ResourceKind.POD)
            if e.namespace
        }

        raw: list[dict] = []
        if namespaces:
            for ns in namespaces:
                raw.extend(self._fetch_namespace(ns))
        else:
            raw.extend(self._fetch_all())

        if not raw:
            log.info("metrics-server: no pod metrics returned")
            return 0

        # Index graph pods by (namespace, name) for O(1) lookup
        pod_index: dict[tuple[str, str], Pod] = {}
        for entity in graph.entities(ResourceKind.POD):
            if isinstance(entity, Pod):
                pod_index[(entity.namespace or "", entity.name)] = entity

        annotated = 0
        for item in raw:
            meta = item.get("metadata", {})
            pod_name = meta.get("name", "")
            pod_ns = meta.get("namespace", "")
            containers = item.get("containers", [])

            cpu_m = sum(
                _parse_cpu_millicores(c.get("usage", {}).get("cpu", "0"))
                for c in containers
            )
            memory_mi = sum(
                _parse_memory_mib(c.get("usage", {}).get("memory", "0"))
                for c in containers
            )

            pod = pod_index.get((pod_ns, pod_name))
            if pod is None:
                continue

            pod.annotations["metrics.cpu_m"]      = f"{cpu_m:.1f}"
            pod.annotations["metrics.memory_mi"]   = f"{memory_mi:.1f}"
            annotated += 1
            log.debug(
                "metrics-server: %s/%s cpu=%.1fm memory=%.1fMi",
                pod_ns, pod_name, cpu_m, memory_mi,
            )

        log.info("metrics-server: %d pod(s) annotated", annotated)
        return annotated

    def is_available(self) -> bool:
        """Return True if metrics-server is installed and responsive."""
        try:
            self._custom.list_cluster_custom_object(
                "metrics.k8s.io", "v1beta1", "pods",
                limit=1,
            )
            return True
        except ApiException as exc:
            if exc.status in (404, 503):
                return False
            log.debug("metrics-server probe: %s", exc)
            return False
        except Exception:
            return False

    # ------------------------------------------------------------------

    def _fetch_namespace(self, namespace: str) -> list[dict]:
        try:
            result = self._custom.list_namespaced_custom_object(
                "metrics.k8s.io", "v1beta1", namespace, "pods"
            )
            items = result.get("items", [])
            log.debug("metrics-server: %d pod(s) fetched for ns=%s", len(items), namespace)
            return items
        except ApiException as exc:
            log.warning("metrics-server: failed to fetch ns=%s (%s)", namespace, exc.reason)
            return []
        except Exception as exc:
            log.warning("metrics-server: unexpected error for ns=%s: %s", namespace, exc)
            return []

    def _fetch_all(self) -> list[dict]:
        try:
            result = self._custom.list_cluster_custom_object(
                "metrics.k8s.io", "v1beta1", "pods"
            )
            items = result.get("items", [])
            log.debug("metrics-server: %d pod(s) fetched (cluster-wide)", len(items))
            return items
        except ApiException as exc:
            log.warning("metrics-server: cluster-wide fetch failed (%s)", exc.reason)
            return []
        except Exception as exc:
            log.warning("metrics-server: unexpected error: %s", exc)
            return []


# ─────────────────────────────────────────────────────────────────────────────
# Resource quantity parsers
# ─────────────────────────────────────────────────────────────────────────────

def _parse_cpu_millicores(value: str) -> float:
    """Parse Kubernetes CPU quantity → millicores (e.g. '125m' → 125, '1' → 1000)."""
    value = value.strip()
    if not value or value == "0":
        return 0.0
    if value.endswith("m"):
        return float(value[:-1])
    if value.endswith("n"):       # nanocores
        return float(value[:-1]) / 1_000_000
    try:
        return float(value) * 1000
    except ValueError:
        return 0.0


def _parse_memory_mib(value: str) -> float:
    """Parse Kubernetes memory quantity → MiB (e.g. '256Mi' → 256, '1Gi' → 1024)."""
    value = value.strip()
    if not value or value == "0":
        return 0.0
    suffixes = {
        "Ki": 1 / 1024,
        "Mi": 1.0,
        "Gi": 1024.0,
        "Ti": 1024.0 * 1024,
        "Pi": 1024.0 * 1024 * 1024,
        "k":  1000 / (1024 * 1024),
        "M":  1_000_000 / (1024 * 1024),
        "G":  1_000_000_000 / (1024 * 1024),
    }
    for suffix, factor in suffixes.items():
        if value.endswith(suffix):
            try:
                return float(value[: -len(suffix)]) * factor
            except ValueError:
                return 0.0
    try:
        return float(value) / (1024 * 1024)
    except ValueError:
        return 0.0
