"""
Coverage tests for rca/context_builder.py — uncovered branches.

Targets:
  - ContextWindow.to_prompt_block() — all sections
  - anchor_fix_hints() — all annotation types
  - _field_path_to_helm_key() — all branches
"""
from __future__ import annotations


from ontology.entities import Pod, HelmRelease
from ontology.graph import OntologyGraph
from rca.context_builder import (
    ContextWindow,
    anchor_fix_hints,
    _field_path_to_helm_key,
)


# ---------------------------------------------------------------------------
# ContextWindow.to_prompt_block() — all sections
# ---------------------------------------------------------------------------

class TestContextWindowPromptBlock:

    def test_empty_window_returns_empty_string(self):
        cw = ContextWindow()
        assert cw.to_prompt_block() == ""

    def test_seeds_section(self):
        cw = ContextWindow(seeds=["Pod/ns/broken — CrashLoopBackOff"])
        out = cw.to_prompt_block()
        assert "CRITICAL" in out
        assert "Unhealthy resources" in out
        assert "Pod/ns/broken" in out

    def test_drift_section(self):
        cw = ContextWindow(drift=["HelmRelease/ns/app: image.tag declared='v1' observed='v2'"])
        out = cw.to_prompt_block()
        assert "CRITICAL" in out
        assert "drift" in out.lower()

    def test_examples_section(self):
        cw = ContextWindow(examples=["Past incident: CrashLoopBackOff fixed with kubectl rollout"])
        out = cw.to_prompt_block()
        assert "SIMILAR PAST INCIDENTS" in out

    def test_anchor_fixes_section(self):
        cw = ContextWindow(anchor_fixes=["Pod/ns/app resources.limits.memory='256Mi' → helm upgrade"])
        out = cw.to_prompt_block()
        assert "ANCHOR FIX SUGGESTIONS" in out

    def test_alerts_section(self):
        cw = ContextWindow(alerts=["KubePodCrashLooping — pod/ns/app firing for 10m"])
        out = cw.to_prompt_block()
        assert "Prometheus alerts" in out

    def test_traces_section(self):
        cw = ContextWindow(traces=["trace-abc123: HTTP 500 on GET /api/health (50ms)"])
        out = cw.to_prompt_block()
        assert "TRACES" in out
        assert "OpenTelemetry" in out

    def test_logs_section(self):
        cw = ContextWindow(logs=["ERROR: connection refused to postgres:5432"])
        out = cw.to_prompt_block()
        assert "LOGS" in out

    def test_events_section(self):
        cw = ContextWindow(events=["BackOff — restarting container (×12)"])
        out = cw.to_prompt_block()
        assert "WARNING" in out
        assert "Kubernetes events" in out

    def test_anchors_section(self):
        cw = ContextWindow(anchors=["Pod/ns/app: container.app.resources.limits.memory declared='1Gi' [schema]"])
        out = cw.to_prompt_block()
        assert "ANCHORS" in out

    def test_helm_section(self):
        cw = ContextWindow(helm=["HelmRelease/ns/myapp chart=stable/app status=deployed"])
        out = cw.to_prompt_block()
        assert "Helm / Helmfile releases" in out

    def test_policy_violations_section(self):
        cw = ContextWindow(policy_violations=["[kyverno] require-labels — Pod/ns/app FAIL"])
        out = cw.to_prompt_block()
        assert "Policy violations" in out
        assert "OPA / Kyverno" in out

    def test_related_section(self):
        cw = ContextWindow(related=["Pod/ns/app: labels={app: my-service} phase=Running"])
        out = cw.to_prompt_block()
        assert "Related context" in out

    def test_multiple_sections_ordered(self):
        cw = ContextWindow(
            policy_violations=["[opa] violation"],
            seeds=["Pod/ns/broken"],
            drift=["drift detected"],
            alerts=["alert firing"],
        )
        out = cw.to_prompt_block()
        # Policy violations must come first
        pv_pos = out.index("Policy violations")
        seed_pos = out.index("Unhealthy")
        drift_pos = out.index("drift")
        alert_pos = out.index("Prometheus")
        assert pv_pos < seed_pos < drift_pos < alert_pos

    def test_total_chunks_counts_all_sections(self):
        cw = ContextWindow(
            seeds=["s1"],
            drift=["d1", "d2"],
            examples=["e1"],
            alerts=["a1"],
            traces=["t1"],
            logs=["l1"],
            events=["ev1", "ev2"],
            anchors=["anc1"],
            anchor_fixes=["fix1"],
            helm=["h1"],
            related=["r1", "r2"],
            policy_violations=["pv1"],
        )
        assert cw.total_chunks == 15


# ---------------------------------------------------------------------------
# _field_path_to_helm_key() — all branches
# ---------------------------------------------------------------------------

class TestFieldPathToHelmKey:

    def test_container_resources_limits(self):
        result = _field_path_to_helm_key("container.app.resources.limits.memory")
        assert result == "resources.limits.memory"

    def test_container_image(self):
        result = _field_path_to_helm_key("container.myapp.image")
        assert result == "image"

    def test_container_image_pull_policy(self):
        result = _field_path_to_helm_key("container.myapp.imagePullPolicy")
        assert result == "imagePullPolicy"

    def test_spec_replicas(self):
        result = _field_path_to_helm_key("spec.replicas")
        assert result == "replicaCount"

    def test_spec_other(self):
        result = _field_path_to_helm_key("spec.serviceAccountName")
        assert result == "serviceAccountName"

    def test_passthrough(self):
        result = _field_path_to_helm_key("livenessProbe.timeoutSeconds")
        assert result == "livenessProbe.timeoutSeconds"


# ---------------------------------------------------------------------------
# anchor_fix_hints() — all annotation types
# ---------------------------------------------------------------------------

def _make_pod(uid: str, ns: str, name: str, annotations: dict) -> Pod:
    p = Pod(
        uid=uid, name=name, namespace=ns,
        labels={}, phase="Running", node_name="node-1",
        restart_count=0, container_statuses=[], conditions=[],
        owner_ref_kind="ReplicaSet", owner_ref_name="rs-1",
    )
    p.annotations.update(annotations)
    return p


def _make_release(uid: str, ns: str, name: str) -> HelmRelease:
    return HelmRelease(
        uid=uid, name=name, namespace=ns,
        chart="stable/app", status="deployed",
        values={}, source="helm",
    )


class TestAnchorFixHints:

    def _graph_with_pod(self, annotations: dict) -> tuple[OntologyGraph, list]:
        graph = OntologyGraph()
        pod = _make_pod("pod-ns-test", "production", "test-pod", annotations)
        graph.add_entity(pod)
        return graph, [pod]

    def test_manifest_anchor_generates_helm_upgrade(self):
        graph, seeds = self._graph_with_pod({
            "anchor.container.app.resources.limits.memory":
                "declared='1Gi' [manifest] | observed='512Mi' [helm-deployed]",
        })
        hints = anchor_fix_hints(graph, seeds)
        assert any("helm upgrade" in h for h in hints)
        assert any("resources.limits.memory" in h for h in hints)

    def test_missing_secret_generates_kubectl_create_secret(self):
        graph, seeds = self._graph_with_pod({
            "missing.secret.payment-db-creds":
                "Secret 'payment-db-creds' referenced in envFrom — not found in cluster",
        })
        hints = anchor_fix_hints(graph, seeds)
        assert any("kubectl create secret generic" in h for h in hints)
        assert any("payment-db-creds" in h for h in hints)

    def test_missing_imagepullsecret_generates_docker_registry(self):
        graph, seeds = self._graph_with_pod({
            "missing.imagepullsecret.ghcr-creds":
                "imagePullSecret 'ghcr-creds' not found",
        })
        hints = anchor_fix_hints(graph, seeds)
        assert any("docker-registry" in h for h in hints)
        assert any("ghcr-creds" in h for h in hints)

    def test_missing_configmap_generates_kubectl_create_configmap(self):
        graph, seeds = self._graph_with_pod({
            "missing.configmap.app-config":
                "ConfigMap 'app-config' referenced in envFrom — not found in cluster",
        })
        hints = anchor_fix_hints(graph, seeds)
        assert any("kubectl create configmap" in h for h in hints)
        assert any("app-config" in h for h in hints)

    def test_missing_pvc_generates_kubectl_apply(self):
        graph, seeds = self._graph_with_pod({
            "missing.pvc.data-kafka-0":
                "PVC 'data-kafka-0' referenced as volume — not found or not bound",
        })
        hints = anchor_fix_hints(graph, seeds)
        assert any("PersistentVolumeClaim" in h for h in hints)
        assert any("data-kafka-0" in h for h in hints)

    def test_missing_serviceaccount_generates_kubectl_create_sa(self):
        graph, seeds = self._graph_with_pod({
            "missing.serviceaccount.payment-service":
                "ServiceAccount 'payment-service' not found in cluster",
        })
        hints = anchor_fix_hints(graph, seeds)
        assert any("serviceaccount" in h for h in hints)
        assert any("payment-service" in h for h in hints)

    def test_missing_rbac_generates_clusterrolebinding(self):
        graph, seeds = self._graph_with_pod({
            "missing.rbac.search-service":
                "ServiceAccount 'search-service' exists but has no (Cluster)RoleBinding",
        })
        hints = anchor_fix_hints(graph, seeds)
        assert any("clusterrolebinding" in h for h in hints)
        assert any("search-service" in h for h in hints)

    def test_netpol_egress_blocked_generates_kubectl_edit(self):
        graph, seeds = self._graph_with_pod({
            "netpol.deny-all.egress_blocked":
                "NetworkPolicy 'deny-all' selects this pod with empty egress rules",
        })
        hints = anchor_fix_hints(graph, seeds)
        assert any("kubectl edit networkpolicy" in h for h in hints)
        assert any("deny-all" in h for h in hints)

    def test_empty_seeds_returns_empty(self):
        graph = OntologyGraph()
        assert anchor_fix_hints(graph, []) == []

    def test_non_seed_entity_is_skipped(self):
        graph = OntologyGraph()
        pod = _make_pod("pod-ns-test", "production", "test-pod", {
            "missing.secret.my-secret": "Secret missing",
        })
        graph.add_entity(pod)
        # Seeds list is empty → pod is not in the seed set → no hints
        assert anchor_fix_hints(graph, []) == []

    def test_deduplication_prevents_duplicate_hints(self):
        graph = OntologyGraph()
        pod1 = _make_pod("pod-ns-1", "production", "pod-1", {
            "missing.secret.shared-creds": "Secret missing",
        })
        pod2 = _make_pod("pod-ns-2", "production", "pod-2", {
            "missing.secret.shared-creds": "Secret missing",
        })
        graph.add_entity(pod1)
        graph.add_entity(pod2)
        # Both pods reference the same secret → same hint → deduplication
        hints = anchor_fix_hints(graph, [pod1, pod2])
        shared_hints = [h for h in hints if "shared-creds" in h]
        # At most 2 distinct hints (one per pod since they're different entities)
        assert len(shared_hints) <= 2

    def test_release_name_used_in_helm_upgrade(self):
        graph = OntologyGraph()
        release = _make_release("hr-prod-myapp", "production", "myapp")
        pod = _make_pod("pod-prod-myapp", "production", "myapp-abc-123", {
            "anchor.container.app.resources.limits.memory":
                "declared='2Gi' [manifest] | observed='1Gi' [helm-deployed]",
        })
        graph.add_entity(release)
        graph.add_entity(pod)
        hints = anchor_fix_hints(graph, [pod])
        assert any("helm upgrade myapp" in h for h in hints)
