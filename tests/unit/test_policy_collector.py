"""
Unit tests for ingestion/policy_collector.py.

All K8s API calls are mocked — no cluster required.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from ingestion.policy_collector import (
    PolicyCollector,
    _detect_source,
    _violation_uid,
    policy_fix_hints,
)
from ontology.entities import MutatingWebhook, PolicyViolation, ResourceKind
from ontology.graph import OntologyGraph
from ontology.relationships import RelationshipType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _graph() -> OntologyGraph:
    return OntologyGraph()


def _collector(custom=None, admission=None) -> PolicyCollector:
    c = PolicyCollector.__new__(PolicyCollector)
    c._custom    = custom    or MagicMock()
    c._admission = admission or MagicMock()
    c._timeout   = 30
    return c


def _policy_report(
    *,
    ns: str = "production",
    policy: str = "disallow-latest-tag",
    rule: str = "require-image-tag",
    result: str = "fail",
    severity: str = "medium",
    resource_kind: str = "Pod",
    resource_name: str = "web-pod",
    resource_ns: str = "production",
    source_label: str = "kyverno",
) -> dict:
    return {
        "metadata": {
            "name": f"cpol-{policy}",
            "namespace": ns,
            "labels": {"app.kubernetes.io/managed-by": source_label},
        },
        "results": [
            {
                "policy": policy,
                "rule": rule,
                "result": result,
                "message": f"validation error for {rule}",
                "severity": severity,
                "resources": [
                    {
                        "kind": resource_kind,
                        "name": resource_name,
                        "namespace": resource_ns,
                    }
                ],
            }
        ],
        "summary": {"fail": 1, "pass": 0, "warn": 0, "error": 0, "skip": 0},
    }


def _webhook_item(name: str, failure_policy: str = "Ignore") -> MagicMock:
    item = MagicMock()
    item.metadata.name = name
    wh = MagicMock()
    wh.failure_policy = failure_policy
    rule = MagicMock()
    rule.api_groups = ["apps"]
    rule.resources  = ["deployments", "pods"]
    wh.rules = [rule]
    item.webhooks = [wh]
    return item


# ---------------------------------------------------------------------------
# _detect_source
# ---------------------------------------------------------------------------

class TestDetectSource:
    def test_kyverno_managed_by_label(self):
        report = {"metadata": {"labels": {"app.kubernetes.io/managed-by": "kyverno"}, "annotations": {}}}
        assert _detect_source(report) == "kyverno"

    def test_gatekeeper_label(self):
        report = {"metadata": {"labels": {"gatekeeper.sh/constraint": "true"}, "annotations": {}}}
        assert _detect_source(report) == "gatekeeper"

    def test_source_label(self):
        report = {"metadata": {"labels": {"source": "my-engine"}, "annotations": {}}}
        assert _detect_source(report) == "my-engine"

    def test_unknown_falls_back(self):
        report = {"metadata": {"labels": {}, "annotations": {}}}
        assert _detect_source(report) == "unknown"


# ---------------------------------------------------------------------------
# _violation_uid
# ---------------------------------------------------------------------------

class TestViolationUid:
    def test_deterministic(self):
        u1 = _violation_uid("pol", "rule", "Pod", "ns", "name")
        u2 = _violation_uid("pol", "rule", "Pod", "ns", "name")
        assert u1 == u2

    def test_prefix(self):
        uid = _violation_uid("pol", "rule", "Pod", "ns", "name")
        assert uid.startswith("policy-violation-")

    def test_truncated_at_128(self):
        long = "a" * 60
        uid = _violation_uid(long, long, long, long, long)
        assert len(uid) <= 128


# ---------------------------------------------------------------------------
# collect() — PolicyReport ingestion
# ---------------------------------------------------------------------------

class TestCollectFail:
    def test_fail_result_increments_fail_count(self):
        g = _graph()
        custom = MagicMock()
        # First call = namespaced PolicyReports, second = ClusterPolicyReports (empty)
        custom.list_cluster_custom_object.side_effect = [
            {"items": [_policy_report(result="fail")]},
            {"items": []},
        ]
        c = _collector(custom=custom)
        c._admission.list_mutating_webhook_configuration.return_value = MagicMock(items=[])

        result = c.collect(g)

        assert result.fail_count == 1
        assert result.audit_count == 0

    def test_warn_result_increments_audit_count(self):
        g = _graph()
        custom = MagicMock()
        custom.list_cluster_custom_object.side_effect = [
            {"items": [_policy_report(result="warn")]},
            {"items": []},
        ]
        c = _collector(custom=custom)
        c._admission.list_mutating_webhook_configuration.return_value = MagicMock(items=[])

        result = c.collect(g)

        assert result.fail_count == 0
        assert result.audit_count == 1

    def test_pass_result_not_counted(self):
        g = _graph()
        custom = MagicMock()
        custom.list_cluster_custom_object.return_value = {
            "items": [_policy_report(result="pass")]
        }
        c = _collector(custom=custom)
        c._admission.list_mutating_webhook_configuration.return_value = MagicMock(items=[])

        result = c.collect(g)

        assert result.fail_count == 0
        assert result.audit_count == 0
        assert result.violations_added == 0

    def test_violation_node_added_to_graph(self):
        g = _graph()
        custom = MagicMock()
        custom.list_cluster_custom_object.return_value = {
            "items": [_policy_report(result="fail", policy="my-policy", rule="my-rule")]
        }
        c = _collector(custom=custom)
        c._admission.list_mutating_webhook_configuration.return_value = MagicMock(items=[])

        c.collect(g)

        violations = [e for e in g.entities(ResourceKind.POLICY_VIOLATION)]
        assert len(violations) == 1
        v = violations[0]
        assert isinstance(v, PolicyViolation)
        assert v.policy == "my-policy"
        assert v.rule == "my-rule"
        assert v.result == "fail"

    def test_correlated_entity_annotated(self):
        from ontology.entities import Pod
        g = _graph()
        pod = Pod(uid="pod-web", name="web-pod", namespace="production")
        g.add_entity(pod)

        custom = MagicMock()
        custom.list_cluster_custom_object.return_value = {
            "items": [_policy_report(result="fail", resource_kind="Pod", resource_name="web-pod", resource_ns="production")]
        }
        c = _collector(custom=custom)
        c._admission.list_mutating_webhook_configuration.return_value = MagicMock(items=[])
        c.collect(g)

        assert any(k.startswith("policy.") for k in pod.annotations)
        assert pod.annotations.get("policy.disallow-latest-tag.result") == "fail"

    def test_has_policy_violation_edge_added(self):
        from ontology.entities import Pod
        g = _graph()
        pod = Pod(uid="pod-web", name="web-pod", namespace="production")
        g.add_entity(pod)

        custom = MagicMock()
        custom.list_cluster_custom_object.return_value = {
            "items": [_policy_report(result="fail", resource_kind="Pod", resource_name="web-pod", resource_ns="production")]
        }
        c = _collector(custom=custom)
        c._admission.list_mutating_webhook_configuration.return_value = MagicMock(items=[])
        c.collect(g)

        edges = g._adj.get(pod.uid, [])
        assert any(e.rel_type == RelationshipType.HAS_POLICY_VIOLATION for e in edges)

    def test_multiple_violations_multiple_nodes(self):
        g = _graph()
        custom = MagicMock()
        custom.list_cluster_custom_object.side_effect = [
            {"items": [
                _policy_report(result="fail", policy="pol-a", rule="rule-1"),
                _policy_report(result="fail", policy="pol-b", rule="rule-2"),
            ]},
            {"items": []},  # ClusterPolicyReport
        ]
        c = _collector(custom=custom)
        c._admission.list_mutating_webhook_configuration.return_value = MagicMock(items=[])
        result = c.collect(g)

        assert result.fail_count == 2
        assert result.violations_added == 2

    def test_no_crd_404_is_graceful(self):
        from kubernetes.client import ApiException
        g = _graph()
        custom = MagicMock()
        custom.list_cluster_custom_object.side_effect = ApiException(status=404)
        c = _collector(custom=custom)
        c._admission.list_mutating_webhook_configuration.return_value = MagicMock(items=[])

        result = c.collect(g)  # must not raise

        assert result.fail_count == 0
        assert result.violations_added == 0


# ---------------------------------------------------------------------------
# collect() — MutatingWebhookConfiguration
# ---------------------------------------------------------------------------

class TestCollectWebhooks:
    def test_webhook_count_returned(self):
        g = _graph()
        custom = MagicMock()
        custom.list_cluster_custom_object.return_value = {"items": []}
        admission = MagicMock()
        admission.list_mutating_webhook_configuration.return_value = MagicMock(
            items=[_webhook_item("istio-sidecar"), _webhook_item("kyverno-policy")]
        )
        c = _collector(custom=custom, admission=admission)

        result = c.collect(g)

        assert result.mutation_webhooks == 2

    def test_webhook_nodes_added(self):
        g = _graph()
        custom = MagicMock()
        custom.list_cluster_custom_object.return_value = {"items": []}
        admission = MagicMock()
        admission.list_mutating_webhook_configuration.return_value = MagicMock(
            items=[_webhook_item("istio-sidecar")]
        )
        c = _collector(custom=custom, admission=admission)
        c.collect(g)

        webhooks = [e for e in g.entities(ResourceKind.MUTATING_WEBHOOK)]
        assert len(webhooks) == 1
        assert isinstance(webhooks[0], MutatingWebhook)
        assert webhooks[0].name == "istio-sidecar"

    def test_webhook_api_error_graceful(self):
        from kubernetes.client import ApiException
        g = _graph()
        custom = MagicMock()
        custom.list_cluster_custom_object.return_value = {"items": []}
        admission = MagicMock()
        admission.list_mutating_webhook_configuration.side_effect = ApiException(status=403)
        c = _collector(custom=custom, admission=admission)

        result = c.collect(g)  # must not raise
        assert result.mutation_webhooks == 0


# ---------------------------------------------------------------------------
# policy_fix_hints()
# ---------------------------------------------------------------------------

class TestPolicyFixHints:
    def _graph_with_kyverno_fail(self) -> OntologyGraph:
        g = _graph()
        v = PolicyViolation(
            uid="pv-1",
            name="disallow-latest-tag/require-image-tag",
            namespace="production",
            policy="disallow-latest-tag",
            rule="require-image-tag",
            result="fail",
            source="kyverno",
            resource_kind="Pod",
            resource_name="web-pod",
            resource_namespace="production",
        )
        g.add_entity(v)
        return g

    def test_returns_list(self):
        g = self._graph_with_kyverno_fail()
        hints = policy_fix_hints(g)
        assert isinstance(hints, list)

    def test_kyverno_hint_contains_policy_name(self):
        g = self._graph_with_kyverno_fail()
        hints = policy_fix_hints(g)
        assert len(hints) == 1
        assert "disallow-latest-tag" in hints[0]

    def test_kyverno_hint_suggests_kubectl_describe(self):
        g = self._graph_with_kyverno_fail()
        hints = policy_fix_hints(g)
        assert "kubectl describe clusterpolicy" in hints[0]

    def test_gatekeeper_hint_suggests_constraint(self):
        g = _graph()
        v = PolicyViolation(
            uid="pv-2",
            name="require-labels/check-labels",
            namespace=None,
            policy="require-labels",
            rule="check-labels",
            result="fail",
            source="gatekeeper",
            resource_kind="Namespace",
            resource_name="production",
            resource_namespace="",
        )
        g.add_entity(v)
        hints = policy_fix_hints(g)
        assert "kubectl describe constraint" in hints[0]

    def test_no_fail_no_hints(self):
        g = _graph()
        v = PolicyViolation(
            uid="pv-3", name="pol/rule", result="warn", source="kyverno",
            policy="pol", rule="rule",
            resource_kind="Pod", resource_name="x", resource_namespace="ns",
        )
        g.add_entity(v)
        hints = policy_fix_hints(g)
        assert len(hints) == 0  # warn not counted as fail

    def test_capped_at_10(self):
        g = _graph()
        for i in range(15):
            v = PolicyViolation(
                uid=f"pv-{i}", name=f"pol-{i}/rule", result="fail", source="kyverno",
                policy=f"pol-{i}", rule="rule",
                resource_kind="Pod", resource_name=f"pod-{i}", resource_namespace="ns",
            )
            g.add_entity(v)
        hints = policy_fix_hints(g)
        assert len(hints) <= 10


# ---------------------------------------------------------------------------
# PolicyViolation entity
# ---------------------------------------------------------------------------

class TestPolicyViolationEntity:
    def test_kind_set(self):
        v = PolicyViolation(uid="x", name="p/r")
        assert v.kind == ResourceKind.POLICY_VIOLATION

    def test_is_fail(self):
        v = PolicyViolation(uid="x", name="p/r", result="fail")
        assert v.is_fail
        v2 = PolicyViolation(uid="x2", name="p/r", result="warn")
        assert not v2.is_fail

    def test_is_audit(self):
        v = PolicyViolation(uid="x", name="p/r", result="warn")
        assert v.is_audit
        v2 = PolicyViolation(uid="x2", name="p/r", result="fail")
        assert not v2.is_audit

    def test_to_text_contains_key_fields(self):
        v = PolicyViolation(
            uid="x", name="disallow-latest/tag",
            policy="disallow-latest", rule="tag",
            result="fail", severity="high", source="kyverno",
            resource_kind="Pod", resource_name="web", resource_namespace="prod",
            message="image tag must not be latest",
        )
        text = v.to_text()
        assert "disallow-latest" in text
        assert "fail" in text
        assert "kyverno" in text
        assert "Pod/prod/web" in text
        assert "image tag must not be latest" in text


# ---------------------------------------------------------------------------
# MutatingWebhook entity
# ---------------------------------------------------------------------------

class TestMutatingWebhookEntity:
    def test_kind_set(self):
        w = MutatingWebhook(uid="x", name="istio")
        assert w.kind == ResourceKind.MUTATING_WEBHOOK

    def test_to_text(self):
        w = MutatingWebhook(
            uid="x", name="istio-sidecar",
            failure_policy="Ignore",
            matched_resources=["apps/deployments"],
        )
        text = w.to_text()
        assert "istio-sidecar" in text
        assert "Ignore" in text


# ---------------------------------------------------------------------------
# Integration: ContextBuilder reads violations from graph
# ---------------------------------------------------------------------------

class TestContextBuilderIntegration:
    def test_context_window_policy_counts(self, synthetic_graph):
        """ContextBuilder.build() must populate policy counts from graph violations."""
        from vectorstore.embedder import Embedder
        from vectorstore.store import FAISSStore
        from rca.context_builder import ContextBuilder

        graph = synthetic_graph

        # Inject 2 fail + 1 warn violations directly into graph
        for i, res in enumerate(["fail", "fail", "warn"]):
            v = PolicyViolation(
                uid=f"pv-test-{i}", name=f"pol-{i}/rule", result=res,
                source="kyverno", policy=f"pol-{i}", rule="rule",
                resource_kind="Pod", resource_name=f"pod-{i}", resource_namespace="test",
            )
            graph.add_entity(v)

        store = FAISSStore(embedder=Embedder())
        store.index_graph(graph)
        cb = ContextBuilder(graph, store)
        ctx = cb.build("policy violations")

        assert ctx.policy_fail_count == 2
        assert ctx.policy_audit_count == 1
        assert len(ctx.policy_violations) == 3
        assert ctx.pre_llm_confidence is not None
        assert ctx.pre_llm_confidence.score > 0
