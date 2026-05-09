"""
Shared pytest fixtures — synthetic cluster graph used across all test suites.
No real K8s cluster or Ollama instance required.
"""
import pytest

from ontology.entities import (
    Namespace, Node, Pod, Deployment, Service,
    ConfigMap, Secret, PersistentVolumeClaim, K8sEvent,
    HelmRelease, HelmChart,
)
from ontology.graph import OntologyGraph
from ontology.relationships import Edge, RelationshipType
from ontology.version import KubeVersion


# ---------------------------------------------------------------------------
# KubeVersion fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def kube_v128() -> KubeVersion:
    return KubeVersion(major=1, minor=28, git_version="v1.28.3+k3s1")

@pytest.fixture
def kube_v118() -> KubeVersion:
    return KubeVersion(major=1, minor=18, git_version="v1.18.20")

@pytest.fixture
def kube_v121() -> KubeVersion:
    return KubeVersion(major=1, minor=21, git_version="v1.21.0")


# ---------------------------------------------------------------------------
# Synthetic graph — simulates a degraded production namespace
# ---------------------------------------------------------------------------

@pytest.fixture
def synthetic_graph() -> OntologyGraph:
    """
    Builds a graph with:
    - namespace 'production'
    - 1 healthy node
    - deployment 'api' with replicas=3 but readyReplicas=1 (degraded)
    - pod 'api-abc' Running (healthy)
    - pod 'api-xyz' Failed + restarts=15 + CrashLoopBackOff annotation (unhealthy)
    - service 'api-svc' selecting label app=api
    - configmap 'api-config'
    - secret 'api-secret'
    - pvc 'api-data' Pending (unhealthy)
    - Warning events for the failed pod and the pvc
    - HelmRelease 'api' with drift annotations
    """
    graph = OntologyGraph(server_version=KubeVersion(1, 28, "v1.28.3+k3s1"))

    # Namespace
    ns = Namespace(uid="ns-production", name="production", namespace=None)
    graph.add_entity(ns)

    # Node
    node = Node(uid="node-1", name="worker-1", namespace=None, ready=True,
                allocatable_cpu="4", allocatable_memory="8Gi")
    graph.add_entity(node)

    # Deployment — degraded
    deploy = Deployment(
        uid="deploy-api", name="api", namespace="production",
        labels={"app": "api"}, replicas=3, ready_replicas=1, available_replicas=1,
    )
    graph.add_entity(deploy)

    # Pod — healthy
    pod_ok = Pod(
        uid="pod-api-abc", name="api-abc", namespace="production",
        labels={"app": "api"}, phase="Running", node_name="worker-1",
        restart_count=0, owner_ref_kind="ReplicaSet", owner_ref_name="api-rs",
    )
    graph.add_entity(pod_ok)

    # Pod — crashed, with drift annotation
    pod_bad = Pod(
        uid="pod-api-xyz", name="api-xyz", namespace="production",
        labels={"app": "api"}, phase="Failed", node_name="worker-1",
        restart_count=15,
        container_statuses=[{"name": "api", "ready": False,
                              "restart_count": 15, "state": "CrashLoopBackOff"}],
        owner_ref_kind="ReplicaSet", owner_ref_name="api-rs",
        annotations={"drift.container.api.state": "drift field=container.api.state declared=Running observed=CrashLoopBackOff severity=critical"},
    )
    graph.add_entity(pod_bad)

    # Service
    svc = Service(
        uid="svc-api", name="api-svc", namespace="production",
        labels={"app": "api"}, selector={"app": "api"},
        service_type="ClusterIP", cluster_ip="10.96.0.1",
        ports=[{"port": 80, "targetPort": "8080", "protocol": "TCP"}],
    )
    graph.add_entity(svc)

    # ConfigMap
    cm = ConfigMap(uid="cm-api-config", name="api-config", namespace="production",
                   data_keys=["DATABASE_URL", "REDIS_URL", "LOG_LEVEL"])
    graph.add_entity(cm)

    # Secret
    secret = Secret(uid="secret-api", name="api-secret", namespace="production",
                    secret_type="Opaque", data_keys=["DB_PASSWORD", "API_KEY"])
    graph.add_entity(secret)

    # PVC — Pending (unhealthy)
    pvc = PersistentVolumeClaim(
        uid="pvc-api-data", name="api-data", namespace="production",
        requested_storage="10Gi", status_phase="Pending", storage_class="standard",
        annotations={"drift.status.phase": "drift field=status.phase declared=Bound observed=Pending severity=critical"},
    )
    graph.add_entity(pvc)

    # Warning events
    ev_crash = K8sEvent(
        uid="ev-crashloop", name="api-xyz.crashloop", namespace="production",
        event_type="Warning", reason="BackOff",
        message="Back-off restarting failed container api in pod api-xyz",
        involved_kind="Pod", involved_name="api-xyz", count=42,
    )
    graph.add_entity(ev_crash)

    ev_pvc = K8sEvent(
        uid="ev-pvc", name="api-data.pending", namespace="production",
        event_type="Warning", reason="FailedMount",
        message="Unable to attach or mount volumes: api-data — no PersistentVolumes available",
        involved_kind="PersistentVolumeClaim", involved_name="api-data", count=8,
    )
    graph.add_entity(ev_pvc)

    # HelmRelease with drift
    helm_rel = HelmRelease(
        uid="helm-production-api", name="api", namespace="production",
        chart="api", chart_version="1.2.3", status="deployed",
        values={"replicaCount": 3, "image": {"tag": "1.2.3"},
                "persistence": {"enabled": True, "size": "10Gi"}},
        source="helm",
        annotations={
            "drift.spec.replicas": "drift field=spec.replicas declared=3 observed=1 severity=critical",
        },
    )
    graph.add_entity(helm_rel)

    # HelmChart (umbrella with postgresql dep)
    helm_chart = HelmChart(
        uid="chart-api-1.2.3", name="api", namespace=None,
        chart_version="1.2.3", description="API service chart", is_umbrella=False,
        dependencies=[],
        default_values={"replicaCount": 3, "image": {"tag": "latest"},
                        "persistence": {"enabled": True, "size": "10Gi"}},
    )
    graph.add_entity(helm_chart)

    # Edges
    graph.add_edge(Edge("pod-api-abc", "ns-production", RelationshipType.IN_NAMESPACE))
    graph.add_edge(Edge("pod-api-xyz", "ns-production", RelationshipType.IN_NAMESPACE))
    graph.add_edge(Edge("pod-api-abc", "node-1",        RelationshipType.RUNS_ON))
    graph.add_edge(Edge("pod-api-xyz", "node-1",        RelationshipType.RUNS_ON))
    graph.add_edge(Edge("deploy-api",  "ns-production", RelationshipType.IN_NAMESPACE))
    graph.add_edge(Edge("svc-api",     "pod-api-abc",   RelationshipType.EXPOSES))
    graph.add_edge(Edge("svc-api",     "pod-api-xyz",   RelationshipType.EXPOSES))
    graph.add_edge(Edge("pod-api-xyz", "cm-api-config", RelationshipType.MOUNTS_CONFIGMAP))
    graph.add_edge(Edge("pod-api-xyz", "secret-api",    RelationshipType.MOUNTS_SECRET))
    graph.add_edge(Edge("pod-api-xyz", "pvc-api-data",  RelationshipType.USES_PVC))
    graph.add_edge(Edge("pod-api-abc", "helm-production-api", RelationshipType.MANAGED_BY_HELM))
    graph.add_edge(Edge("pod-api-xyz", "helm-production-api", RelationshipType.MANAGED_BY_HELM))
    graph.add_edge(Edge("deploy-api",  "helm-production-api", RelationshipType.MANAGED_BY_HELM))
    graph.add_edge(Edge("pvc-api-data","helm-production-api", RelationshipType.MANAGED_BY_HELM))
    graph.add_edge(Edge("pod-api-xyz", "helm-production-api", RelationshipType.DRIFTS_FROM))
    graph.add_edge(Edge("pvc-api-data","helm-production-api", RelationshipType.DRIFTS_FROM))
    graph.add_edge(Edge("deploy-api",  "helm-production-api", RelationshipType.DRIFTS_FROM))
    graph.add_edge(Edge("helm-production-api", "chart-api-1.2.3", RelationshipType.DEPLOYED_FROM))
    graph.add_edge(Edge("pod-api-xyz", "ev-crashloop",  RelationshipType.HAS_EVENT))
    graph.add_edge(Edge("pvc-api-data","ev-pvc",        RelationshipType.HAS_EVENT))

    return graph
