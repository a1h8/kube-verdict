"""
Unit tests for AnchorRecord, AnchorEngine, K8sApiSchema, and the
ContextBuilder anchor section.

All tests use synthetic OntologyGraph entities — no network, no cluster,
no real helm binary needed.
"""
from __future__ import annotations

import pytest

from ingestion.anchor_engine import (
    AnchorEngine,
    AnchorRecord,
    _extract_manifest_fields,
    _find_entity,
)
from ingestion.k8s_schema import FieldMeta, K8sApiSchema, schema_for_kind
from ontology.entities import (
    Deployment,
    HelmChart,
    HelmfileEnvironment,
    HelmRelease,
    Pod,
    Service,
    StatefulSet,
    ResourceKind,
)
from ontology.graph import OntologyGraph


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _graph(*entities) -> OntologyGraph:
    g = OntologyGraph()
    for e in entities:
        g.add_entity(e)
    return g


def _deployment(name="api", ns="prod", replicas=3) -> Deployment:
    return Deployment(uid=f"dep-{name}", name=name, namespace=ns,
                      replicas=replicas, ready_replicas=replicas)


def _statefulset(name="db", ns="prod") -> StatefulSet:
    return StatefulSet(uid=f"ss-{name}", name=name, namespace=ns)


def _pod(name="api-pod", ns="prod") -> Pod:
    return Pod(uid=f"pod-{name}", name=name, namespace=ns,
               phase="Running", node_name="node1")


def _service(name="api-svc", ns="prod") -> Service:
    return Service(uid=f"svc-{name}", name=name, namespace=ns)


def _release(name="api", ns="prod", values=None, chart=None) -> HelmRelease:
    return HelmRelease(
        uid=f"helm-{name}", name=name, namespace=ns,
        chart=chart or name,
        values=values or {},
    )


def _chart(name="api", values=None) -> HelmChart:
    return HelmChart(uid=f"chart-{name}", name=name, default_values=values or {})


def _env(name="production", values=None, value_files=None) -> HelmfileEnvironment:
    return HelmfileEnvironment(
        uid=f"helmfile-env-{name}", name=name,
        values=values or {},
        value_files=value_files or [],
    )


def _helmfile_release(
    name="api", ns="prod", values=None, chart=None,
    environment="production", value_files=None,
) -> HelmRelease:
    return HelmRelease(
        uid=f"helm-{name}", name=name, namespace=ns,
        chart=chart or name,
        values=values or {},
        source="helmfile",
        environment=environment,
        value_files=value_files or [],
    )


# ─────────────────────────────────────────────────────────────────────────────
# TestAnchorRecord
# ─────────────────────────────────────────────────────────────────────────────

class TestAnchorRecord:

    def test_annotation_key_uses_field_path(self):
        r = AnchorRecord(
            entity_uid="u1", entity_kind="Deployment", entity_name="api",
            entity_namespace="prod", field_path="spec.replicas",
            declared_value="3", source="helm",
        )
        assert r.annotation_key() == "anchor.spec.replicas"

    def test_annotation_key_nested_field(self):
        r = AnchorRecord(
            entity_uid="u1", entity_kind="Deployment", entity_name="api",
            entity_namespace="prod",
            field_path="spec.strategy.rollingUpdate.maxSurge",
            declared_value="25%", source="manifest",
        )
        assert r.annotation_key() == "anchor.spec.strategy.rollingUpdate.maxSurge"

    def test_to_text_includes_declared_and_source(self):
        r = AnchorRecord(
            entity_uid="u1", entity_kind="Deployment", entity_name="api",
            entity_namespace="prod", field_path="spec.replicas",
            declared_value="3", source="helm",
        )
        text = r.to_text()
        assert "declared='3'" in text
        assert "[helm]" in text
        assert "spec.replicas:" in text

    def test_to_text_includes_k8s_default_when_source_not_k8s_defaults(self):
        r = AnchorRecord(
            entity_uid="u1", entity_kind="Deployment", entity_name="api",
            entity_namespace="prod", field_path="spec.replicas",
            declared_value="5", source="manifest",
            k8s_default="1",
        )
        text = r.to_text()
        assert "k8s_default='1'" in text

    def test_to_text_omits_k8s_default_when_source_is_k8s_defaults(self):
        r = AnchorRecord(
            entity_uid="u1", entity_kind="Deployment", entity_name="api",
            entity_namespace="prod", field_path="spec.replicas",
            declared_value="1", source="k8s_defaults",
            k8s_default="1",
        )
        text = r.to_text()
        # No redundant "k8s_default='X'" pair when the source IS the default;
        # note: "[k8s_defaults]" appears in the source tag — check for the key=value form
        assert "k8s_default='1'" not in text

    def test_to_text_includes_valid_values(self):
        r = AnchorRecord(
            entity_uid="u1", entity_kind="Deployment", entity_name="api",
            entity_namespace="prod", field_path="spec.strategy.type",
            declared_value="RollingUpdate", source="k8s_defaults",
            valid_values=["RollingUpdate", "Recreate"],
        )
        text = r.to_text()
        assert "valid=RollingUpdate|Recreate" in text

    def test_to_text_description_truncated_to_100(self):
        long_desc = "X" * 150
        r = AnchorRecord(
            entity_uid="u1", entity_kind="Deployment", entity_name="api",
            entity_namespace="prod", field_path="spec.paused",
            declared_value="false", source="k8s_defaults",
            description=long_desc,
        )
        text = r.to_text()
        assert "X" * 100 in text
        assert "X" * 101 not in text

    def test_from_schema_populates_all_fields(self):
        dep = _deployment()
        meta = FieldMeta(
            description="Desired pod count.",
            k8s_default="1",
            valid_values=("1", "2"),
            severity_on_drift="critical",
        )
        r = AnchorRecord.from_schema(dep, "spec.replicas", meta)
        assert r.entity_uid == dep.uid
        assert r.entity_kind == "Deployment"
        assert r.entity_name == "api"
        assert r.entity_namespace == "prod"
        assert r.field_path == "spec.replicas"
        assert r.declared_value == "1"  # equals k8s_default for schema records
        assert r.source == "k8s_defaults"
        assert r.k8s_default == "1"
        assert r.severity_on_drift == "critical"
        assert list(r.valid_values) == ["1", "2"]

    def test_from_schema_empty_namespace(self):
        pod = Pod(uid="p1", name="mypod", namespace=None, phase="Running", node_name="n1")
        meta = FieldMeta(k8s_default="Always")
        r = AnchorRecord.from_schema(pod, "spec.restartPolicy", meta)
        assert r.entity_namespace == ""


# ─────────────────────────────────────────────────────────────────────────────
# TestFieldMeta
# ─────────────────────────────────────────────────────────────────────────────

class TestFieldMeta:

    def test_to_text_all_fields(self):
        meta = FieldMeta(
            description="Restart policy.",
            k8s_default="Always",
            valid_values=("Always", "OnFailure", "Never"),
            severity_on_drift="critical",
        )
        text = meta.to_text()
        assert "k8s_default='Always'" in text
        assert "valid=Always|OnFailure|Never" in text
        assert "Restart policy." in text

    def test_to_text_empty_defaults(self):
        meta = FieldMeta(description="")
        assert meta.to_text() == ""

    def test_to_text_description_truncated_to_120(self):
        meta = FieldMeta(description="Y" * 130)
        assert "Y" * 120 in meta.to_text()
        assert "Y" * 121 not in meta.to_text()


# ─────────────────────────────────────────────────────────────────────────────
# TestSchemaForKind
# ─────────────────────────────────────────────────────────────────────────────

class TestSchemaForKind:

    def test_deployment_has_replicas(self):
        fields = schema_for_kind("Deployment")
        assert "spec.replicas" in fields
        assert fields["spec.replicas"].k8s_default == "1"

    def test_deployment_has_strategy(self):
        fields = schema_for_kind("Deployment")
        assert "spec.strategy.type" in fields
        assert "RollingUpdate" in fields["spec.strategy.type"].valid_values

    def test_pod_has_restart_policy(self):
        fields = schema_for_kind("Pod")
        assert "spec.restartPolicy" in fields
        assert fields["spec.restartPolicy"].severity_on_drift == "critical"

    def test_pod_has_memory_limit(self):
        fields = schema_for_kind("Pod")
        assert "container.*.resources.limits.memory" in fields

    def test_statefulset_has_replicas(self):
        fields = schema_for_kind("StatefulSet")
        assert "spec.replicas" in fields

    def test_service_has_type(self):
        fields = schema_for_kind("Service")
        assert "spec.type" in fields
        assert fields["spec.type"].k8s_default == "ClusterIP"

    def test_unknown_kind_returns_empty(self):
        assert schema_for_kind("Ingress") == {}
        assert schema_for_kind("") == {}

    def test_configmap_returns_empty(self):
        assert schema_for_kind("ConfigMap") == {}

    def test_deployment_paused_is_critical(self):
        fields = schema_for_kind("Deployment")
        assert fields["spec.paused"].severity_on_drift == "critical"

    def test_deployment_progress_deadline_is_critical(self):
        fields = schema_for_kind("Deployment")
        assert fields["spec.progressDeadlineSeconds"].severity_on_drift == "critical"


# ─────────────────────────────────────────────────────────────────────────────
# TestK8sApiSchema
# ─────────────────────────────────────────────────────────────────────────────

class TestK8sApiSchema:

    def test_get_falls_back_to_embedded_when_not_loaded(self):
        api = K8sApiSchema()
        meta = api.get("Deployment", "spec.replicas")
        assert meta is not None
        assert meta.k8s_default == "1"

    def test_get_returns_none_for_unknown_field(self):
        api = K8sApiSchema()
        assert api.get("Deployment", "spec.nonexistent") is None

    def test_get_returns_none_for_unknown_kind(self):
        api = K8sApiSchema()
        assert api.get("CronJob", "spec.schedule") is None

    def test_fields_for_kind_returns_merged_schema(self):
        api = K8sApiSchema()
        fields = api.fields_for_kind("Pod")
        assert "spec.restartPolicy" in fields
        assert "container.*.resources.limits.memory" in fields

    def test_fields_for_kind_extra_override_base(self):
        api = K8sApiSchema()
        # Inject an extra field that overrides a base one
        api._extra = {"Deployment": {
            "spec.replicas": FieldMeta(description="Overridden", k8s_default="99")
        }}
        fields = api.fields_for_kind("Deployment")
        assert fields["spec.replicas"].k8s_default == "99"
        assert fields["spec.replicas"].description == "Overridden"
        # Other base fields still present
        assert "spec.strategy.type" in fields

    def test_load_returns_false_without_api_client(self):
        api = K8sApiSchema(api_client=None)
        assert api.load() is False

    def test_load_returns_true_on_second_call_if_already_loaded(self):
        api = K8sApiSchema()
        api._loaded = True
        assert api.load() is True

    def test_extract_kind_recognises_core_v1(self):
        api = K8sApiSchema()
        assert api._extract_kind("io.k8s.api.core.v1.Pod") == "Pod"

    def test_extract_kind_recognises_apps_v1(self):
        api = K8sApiSchema()
        assert api._extract_kind("io.k8s.api.apps.v1.Deployment") == "Deployment"

    def test_extract_kind_returns_none_for_unknown_prefix(self):
        api = K8sApiSchema()
        assert api._extract_kind("io.custom.MyResource") is None

    def test_parse_populates_extra_for_known_kind(self):
        api = K8sApiSchema()
        spec = {
            "definitions": {
                "io.k8s.api.apps.v1.Deployment": {
                    "properties": {
                        "replicas": {
                            "description": "Desired replicas from API.",
                            "default": 1,
                        }
                    }
                }
            }
        }
        api._parse(spec)
        assert "Deployment" in api._extra
        assert "spec.replicas" in api._extra["Deployment"]
        assert "Desired replicas" in api._extra["Deployment"]["spec.replicas"].description

    def test_parse_ignores_unknown_kind(self):
        api = K8sApiSchema()
        spec = {
            "definitions": {
                "io.k8s.api.apps.v1.CronJob": {
                    "properties": {"schedule": {"description": "Cron expression"}}
                }
            }
        }
        api._parse(spec)
        # CronJob is not in _SCHEMA, so nothing added
        assert "CronJob" not in api._extra


# ─────────────────────────────────────────────────────────────────────────────
# TestExtractManifestFields
# ─────────────────────────────────────────────────────────────────────────────

class TestExtractManifestFields:

    def test_extracts_replicas(self):
        spec = {"replicas": 5}
        fields = _extract_manifest_fields("Deployment", spec)
        assert fields["spec.replicas"] == "5"

    def test_extracts_strategy_type(self):
        spec = {"strategy": {"type": "Recreate"}}
        fields = _extract_manifest_fields("Deployment", spec)
        assert fields["spec.strategy.type"] == "Recreate"

    def test_extracts_rolling_update(self):
        spec = {
            "strategy": {
                "type": "RollingUpdate",
                "rollingUpdate": {"maxSurge": "50%", "maxUnavailable": "0"},
            }
        }
        fields = _extract_manifest_fields("Deployment", spec)
        assert fields["spec.strategy.rollingUpdate.maxSurge"] == "50%"
        assert fields["spec.strategy.rollingUpdate.maxUnavailable"] == "0"

    def test_extracts_paused(self):
        spec = {"paused": True}
        fields = _extract_manifest_fields("Deployment", spec)
        assert fields["spec.paused"] == "true"

    def test_extracts_container_image(self):
        spec = {
            "template": {
                "spec": {
                    "containers": [
                        {"name": "main", "image": "nginx:1.25"},
                    ]
                }
            }
        }
        fields = _extract_manifest_fields("Deployment", spec)
        assert "container.main.image" in fields
        assert fields["container.main.image"] == "nginx:1.25"

    def test_extracts_container_resources(self):
        spec = {
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": "api",
                            "resources": {
                                "limits": {"memory": "512Mi", "cpu": "500m"},
                                "requests": {"memory": "256Mi", "cpu": "100m"},
                            },
                        }
                    ]
                }
            }
        }
        fields = _extract_manifest_fields("Deployment", spec)
        assert fields["container.api.resources.limits.memory"] == "512Mi"
        assert fields["container.api.resources.limits.cpu"] == "500m"
        assert fields["container.api.resources.requests.memory"] == "256Mi"

    def test_extracts_imagepullpolicy(self):
        spec = {
            "template": {
                "spec": {
                    "containers": [
                        {"name": "svc", "imagePullPolicy": "Always"},
                    ]
                }
            }
        }
        fields = _extract_manifest_fields("Deployment", spec)
        assert fields["container.svc.imagePullPolicy"] == "Always"

    def test_extracts_service_type(self):
        spec = {"type": "LoadBalancer", "port": 8080}
        fields = _extract_manifest_fields("Service", spec)
        assert fields["spec.type"] == "LoadBalancer"

    def test_extracts_pvc_access_modes(self):
        spec = {
            "accessModes": ["ReadWriteOnce"],
            "volumeMode": "Filesystem",
        }
        fields = _extract_manifest_fields("PersistentVolumeClaim", spec)
        assert "spec.accessModes" in fields
        assert "ReadWriteOnce" in fields["spec.accessModes"]
        assert fields["spec.volumeMode"] == "Filesystem"

    def test_multiple_containers(self):
        spec = {
            "template": {
                "spec": {
                    "containers": [
                        {"name": "app", "image": "app:1.0"},
                        {"name": "sidecar", "image": "proxy:latest"},
                    ]
                }
            }
        }
        fields = _extract_manifest_fields("Deployment", spec)
        assert "container.app.image" in fields
        assert "container.sidecar.image" in fields

    def test_empty_spec_returns_empty(self):
        assert _extract_manifest_fields("Deployment", {}) == {}


# ─────────────────────────────────────────────────────────────────────────────
# TestFindEntity
# ─────────────────────────────────────────────────────────────────────────────

class TestFindEntity:

    def test_finds_deployment_by_kind_name_namespace(self):
        dep = _deployment("web", "staging")
        g = _graph(dep)
        found = _find_entity(g, "Deployment", "web", "staging")
        assert found is dep

    def test_returns_none_for_wrong_namespace(self):
        dep = _deployment("web", "staging")
        g = _graph(dep)
        assert _find_entity(g, "Deployment", "web", "prod") is None

    def test_returns_none_for_wrong_name(self):
        dep = _deployment("web", "prod")
        g = _graph(dep)
        assert _find_entity(g, "Deployment", "api", "prod") is None

    def test_returns_none_for_wrong_kind(self):
        dep = _deployment("api", "prod")
        g = _graph(dep)
        assert _find_entity(g, "Service", "api", "prod") is None

    def test_finds_service(self):
        svc = _service("my-svc", "ns")
        g = _graph(svc)
        found = _find_entity(g, "Service", "my-svc", "ns")
        assert found is svc


# ─────────────────────────────────────────────────────────────────────────────
# TestAnchorEngineFromK8sSchema
# ─────────────────────────────────────────────────────────────────────────────

class TestAnchorEngineFromK8sSchema:

    def test_generates_records_for_deployment(self):
        dep = _deployment()
        g = _graph(dep)
        engine = AnchorEngine()
        records = engine._from_k8s_schema(g)
        field_paths = [r.field_path for r in records if r.entity_uid == dep.uid]
        assert "spec.replicas" in field_paths
        assert "spec.strategy.type" in field_paths

    def test_generates_records_for_pod(self):
        pod = _pod()
        g = _graph(pod)
        engine = AnchorEngine()
        records = engine._from_k8s_schema(g)
        field_paths = [r.field_path for r in records if r.entity_uid == pod.uid]
        assert "spec.restartPolicy" in field_paths

    def test_no_records_for_configmap(self):
        from ontology.entities import ConfigMap
        cm = ConfigMap(uid="cm1", name="cfg", namespace="prod")
        g = _graph(cm)
        engine = AnchorEngine()
        records = engine._from_k8s_schema(g)
        assert len(records) == 0

    def test_all_records_have_source_k8s_defaults(self):
        dep = _deployment()
        g = _graph(dep)
        engine = AnchorEngine()
        records = engine._from_k8s_schema(g)
        assert all(r.source == "k8s_defaults" for r in records)

    def test_skips_fields_without_default_or_valid_values(self):
        """Fields with only description (no default, no valid values) are skipped."""
        pod = _pod()
        g = _graph(pod)
        engine = AnchorEngine()
        records = engine._from_k8s_schema(g)
        # Verify that every record has either k8s_default or valid_values
        for r in records:
            assert r.k8s_default or r.valid_values, (
                f"Record for {r.field_path} has neither default nor valid values"
            )

    def test_multiple_entity_types_in_same_graph(self):
        dep = _deployment()
        pod = _pod()
        svc = _service()
        g = _graph(dep, pod, svc)
        engine = AnchorEngine()
        records = engine._from_k8s_schema(g)
        kinds = {r.entity_kind for r in records}
        assert "Deployment" in kinds
        assert "Pod" in kinds
        assert "Service" in kinds



# ─────────────────────────────────────────────────────────────────────────────
# TestAnchorEngineDeduplication
# ─────────────────────────────────────────────────────────────────────────────

class TestAnchorEngineDeduplication:

    def _make_record(self, uid="u1", fp="spec.replicas", value="1", source="k8s_defaults"):
        return AnchorRecord(
            entity_uid=uid, entity_kind="Deployment",
            entity_name="api", entity_namespace="prod",
            field_path=fp, declared_value=value, source=source,
        )

    def test_manifest_beats_k8s_defaults(self):
        r_schema   = self._make_record(value="1", source="k8s_defaults")
        r_manifest = self._make_record(value="7", source="manifest")
        result = AnchorEngine._deduplicate([r_schema, r_manifest])
        assert len(result) == 1
        assert result[0].source == "manifest"

    def test_different_entity_uids_not_deduped(self):
        r1 = self._make_record(uid="u1", value="1", source="k8s_defaults")
        r2 = self._make_record(uid="u2", value="1", source="k8s_defaults")
        result = AnchorEngine._deduplicate([r1, r2])
        assert len(result) == 2

    def test_different_field_paths_not_deduped(self):
        r1 = self._make_record(fp="spec.replicas", value="1", source="manifest")
        r2 = self._make_record(fp="spec.strategy.type", value="Recreate", source="manifest")
        result = AnchorEngine._deduplicate([r1, r2])
        assert len(result) == 2

    def test_empty_input(self):
        assert AnchorEngine._deduplicate([]) == []

    def test_single_record_returned_as_is(self):
        r = self._make_record()
        result = AnchorEngine._deduplicate([r])
        assert len(result) == 1
        assert result[0] is r

    def test_manifest_wins_over_schema(self):
        r_schema   = self._make_record(value="1", source="k8s_defaults")
        r_manifest = self._make_record(value="5", source="manifest")
        result = AnchorEngine._deduplicate([r_schema, r_manifest])
        assert len(result) == 1
        assert result[0].declared_value == "5"
        assert result[0].source == "manifest"


# ─────────────────────────────────────────────────────────────────────────────
# TestAnchorEngineAnnotate
# ─────────────────────────────────────────────────────────────────────────────

class TestAnchorEngineAnnotate:

    def test_writes_annotation_to_entity(self):
        dep = _deployment()
        g = _graph(dep)
        record = AnchorRecord(
            entity_uid=dep.uid, entity_kind="Deployment",
            entity_name="api", entity_namespace="prod",
            field_path="spec.replicas", declared_value="5", source="helm",
        )
        AnchorEngine._annotate(g, [record])
        assert "anchor.spec.replicas" in dep.annotations
        assert "declared='5'" in dep.annotations["anchor.spec.replicas"]

    def test_writes_multiple_annotations_to_same_entity(self):
        dep = _deployment()
        g = _graph(dep)
        records = [
            AnchorRecord(
                entity_uid=dep.uid, entity_kind="Deployment",
                entity_name="api", entity_namespace="prod",
                field_path="spec.replicas", declared_value="3", source="helm",
            ),
            AnchorRecord(
                entity_uid=dep.uid, entity_kind="Deployment",
                entity_name="api", entity_namespace="prod",
                field_path="spec.strategy.type", declared_value="Recreate", source="manifest",
            ),
        ]
        AnchorEngine._annotate(g, records)
        assert "anchor.spec.replicas" in dep.annotations
        assert "anchor.spec.strategy.type" in dep.annotations

    def test_skips_unknown_entity_uid(self):
        dep = _deployment()
        g = _graph(dep)
        ghost_record = AnchorRecord(
            entity_uid="nonexistent-uid", entity_kind="Deployment",
            entity_name="ghost", entity_namespace="prod",
            field_path="spec.replicas", declared_value="1", source="helm",
        )
        # Should not raise
        AnchorEngine._annotate(g, [ghost_record])
        assert "anchor.spec.replicas" not in dep.annotations

    def test_annotation_overwrites_previous_value(self):
        dep = _deployment()
        g = _graph(dep)
        dep.annotations["anchor.spec.replicas"] = "old value"
        record = AnchorRecord(
            entity_uid=dep.uid, entity_kind="Deployment",
            entity_name="api", entity_namespace="prod",
            field_path="spec.replicas", declared_value="99", source="manifest",
        )
        AnchorEngine._annotate(g, [record])
        assert "declared='99'" in dep.annotations["anchor.spec.replicas"]


# ─────────────────────────────────────────────────────────────────────────────
# TestAnchorEngineCollect
# ─────────────────────────────────────────────────────────────────────────────

class TestAnchorEngineCollect:

    def test_collect_returns_list_of_records(self):
        dep = _deployment()
        g = _graph(dep)
        engine = AnchorEngine()
        records = engine.collect(g)
        assert isinstance(records, list)
        assert all(isinstance(r, AnchorRecord) for r in records)

    def test_collect_annotates_entities(self):
        dep = _deployment()
        g = _graph(dep)
        engine = AnchorEngine()
        engine.collect(g)
        # Deployment schema has spec.replicas with default "1"
        assert any(k.startswith("anchor.") for k in dep.annotations)

    def test_collect_deduplicates_manifest_over_schema(self):
        """Rendered manifest wins over k8s schema default for the same field."""
        dep = _deployment(name="api", ns="prod")
        release = _release(name="api", ns="prod", chart="api")
        g = _graph(dep, release)

        class FakeRenderer:
            def render(self, chart, release_name, namespace, values, value_files=None):
                return [{
                    "kind": "Deployment",
                    "metadata": {"name": "api", "namespace": "prod"},
                    "spec": {"replicas": 7},
                }]

        import unittest.mock as mock
        engine = AnchorEngine(renderer=FakeRenderer())
        with mock.patch.object(AnchorEngine, "_resolve_chart", return_value="/charts/api"):
            records = engine.collect(g, provider=type("P", (), {"local_path": lambda s: "/r"})())

        replicas_records = [
            r for r in records
            if r.entity_uid == dep.uid and r.field_path == "spec.replicas"
        ]
        assert len(replicas_records) == 1
        assert replicas_records[0].source == "manifest"
        assert replicas_records[0].declared_value == "7"

    def test_collect_without_provider_only_k8s_schema(self):
        dep = _deployment()
        g = _graph(dep)
        engine = AnchorEngine()
        records = engine.collect(g, provider=None)
        assert all(r.source == "k8s_defaults" for r in records)

    def test_collect_schema_anchor_written_as_annotation(self):
        dep = _deployment()
        g = _graph(dep)
        engine = AnchorEngine()
        engine.collect(g)
        # Check annotation text is parseable
        annotation = dep.annotations.get("anchor.spec.replicas", "")
        assert annotation != ""

    def test_collect_empty_graph(self):
        g = OntologyGraph()
        engine = AnchorEngine()
        records = engine.collect(g)
        assert records == []

    def test_collect_with_rendered_manifests(self):
        """inject a fake renderer that returns a manifest with replicas=10."""
        dep = _deployment(name="api", ns="prod", replicas=3)
        g = _graph(dep)

        class FakeRenderer:
            def render(self, chart, release_name, namespace, values, value_files=None):
                return [{
                    "kind": "Deployment",
                    "metadata": {"name": "api", "namespace": "prod"},
                    "spec": {"replicas": 10},
                }]

        class FakeProvider:
            def local_path(self):
                return "/fake/root"

        release = _release(name="api", ns="prod", chart="api")
        g.add_entity(release)

        # Patch _resolve_chart so it returns something non-None
        engine = AnchorEngine(renderer=FakeRenderer())
        from pathlib import Path
        import unittest.mock as mock
        with mock.patch.object(AnchorEngine, "_resolve_chart", return_value="/fake/root/charts/api"):
            records = engine.collect(g, provider=FakeProvider(), charts_path="charts")

        replicas_records = [
            r for r in records
            if r.entity_uid == dep.uid and r.field_path == "spec.replicas"
        ]
        assert len(replicas_records) == 1
        assert replicas_records[0].source == "manifest"
        assert replicas_records[0].declared_value == "10"


# ─────────────────────────────────────────────────────────────────────────────
# TestContextBuilderAnchors
# ─────────────────────────────────────────────────────────────────────────────

class TestContextBuilderAnchors:
    """Tests that ContextBuilder populates ctx.anchors from anchor.* annotations."""

    @pytest.fixture
    def store_stub(self):
        class _StubStore:
            def search(self, query, top_k=10):
                return []
        return _StubStore()

    def _make_graph_with_unhealthy_deployment(self):
        dep = Deployment(
            uid="dep-api", name="api", namespace="prod",
            replicas=3, ready_replicas=0,
        )
        dep.annotations["anchor.spec.replicas"] = (
            "spec.replicas: declared='3' [helm] | k8s_default='1'"
        )
        dep.annotations["anchor.spec.strategy.type"] = (
            "spec.strategy.type: declared='RollingUpdate' [k8s_defaults] "
            "| valid=RollingUpdate|Recreate"
        )
        dep.is_unhealthy = True
        return dep

    def test_anchors_populated_from_annotations(self, store_stub):
        from rca.context_builder import ContextBuilder
        from dedup.bfs import find_unhealthy

        dep = self._make_graph_with_unhealthy_deployment()
        g = _graph(dep)

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("dedup.bfs.find_unhealthy", lambda _graph: [dep])
            builder = ContextBuilder(graph=g, store=store_stub)
            ctx = builder.build("api deployment issue")

        assert len(ctx.anchors) >= 2
        anchor_text = "\n".join(ctx.anchors)
        assert "spec.replicas" in anchor_text
        assert "spec.strategy.type" in anchor_text

    def test_anchors_appear_in_prompt_block(self, store_stub):
        from rca.context_builder import ContextBuilder

        dep = self._make_graph_with_unhealthy_deployment()
        g = _graph(dep)

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("dedup.bfs.find_unhealthy", lambda _graph: [dep])
            builder = ContextBuilder(graph=g, store=store_stub)
            ctx = builder.build("api issue")

        prompt = ctx.to_prompt_block()
        assert "ANCHORS" in prompt
        assert "Declared values" in prompt

    def test_anchors_in_total_chunks(self, store_stub):
        from rca.context_builder import ContextBuilder

        dep = self._make_graph_with_unhealthy_deployment()
        g = _graph(dep)

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("dedup.bfs.find_unhealthy", lambda _graph: [dep])
            builder = ContextBuilder(graph=g, store=store_stub)
            ctx = builder.build("api issue")

        assert ctx.total_chunks >= len(ctx.anchors)
        total = (
            len(ctx.seeds) + len(ctx.drift) + len(ctx.alerts)
            + len(ctx.traces) + len(ctx.logs) + len(ctx.events)
            + len(ctx.anchors) + len(ctx.helm) + len(ctx.related)
        )
        assert ctx.total_chunks == total

    def test_anchors_capped_at_30(self, store_stub):
        from rca.context_builder import ContextBuilder

        dep = _deployment()
        for i in range(50):
            dep.annotations[f"anchor.field.{i}"] = f"field.{i}: declared='{i}' [k8s_defaults]"
        dep.is_unhealthy = True
        g = _graph(dep)

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("dedup.bfs.find_unhealthy", lambda _graph: [dep])
            builder = ContextBuilder(graph=g, store=store_stub)
            ctx = builder.build("test")

        assert len(ctx.anchors) <= 30

    def test_unhealthy_entities_anchors_come_first(self, store_stub):
        from rca.context_builder import ContextBuilder

        healthy_dep = _deployment(name="healthy")
        healthy_dep.annotations["anchor.spec.replicas"] = "spec.replicas: declared='3' [helm]"

        unhealthy_dep = _deployment(name="broken")
        unhealthy_dep.annotations["anchor.spec.replicas"] = "spec.replicas: declared='0' [helm]"
        unhealthy_dep.is_unhealthy = True

        g = _graph(healthy_dep, unhealthy_dep)

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("dedup.bfs.find_unhealthy", lambda _graph: [unhealthy_dep])
            builder = ContextBuilder(graph=g, store=store_stub)
            ctx = builder.build("broken deployment")

        # broken entity anchors should appear before healthy ones
        broken_idx = next(
            (i for i, t in enumerate(ctx.anchors) if "broken" in t), None
        )
        healthy_idx = next(
            (i for i, t in enumerate(ctx.anchors) if "healthy" in t), None
        )
        if broken_idx is not None and healthy_idx is not None:
            assert broken_idx < healthy_idx


# ─────────────────────────────────────────────────────────────────────────────
# TestRenderedManifestWithEnv
# ─────────────────────────────────────────────────────────────────────────────

class TestRenderedManifestWithEnv:
    """
    Verifies that _from_rendered_manifests passes the Helmfile environment
    value_files to helm template in the correct order (env first = lower priority),
    and that the rendered output becomes the ground-truth anchor.
    """

    def _make_renderer(self, replicas: int):
        class _Renderer:
            def __init__(self, r):
                self._r = r
            def render(self, chart, release_name, namespace, values, value_files=None):
                return [{
                    "kind": "Deployment",
                    "metadata": {"name": release_name, "namespace": namespace},
                    "spec": {"replicas": self._r},
                }]
        return _Renderer(replicas)

    def test_rendered_manifest_is_the_anchor(self):
        """The exact value from helm template becomes the anchor."""
        dep = _deployment(name="api", ns="prod")
        release = _helmfile_release(chart="api")
        env = _env(name="production")
        g = _graph(dep, release, env)

        import unittest.mock as mock
        engine = AnchorEngine(renderer=self._make_renderer(9))
        with mock.patch.object(AnchorEngine, "_resolve_chart", return_value="/charts/api"):
            records = engine._from_rendered_manifests(
                g, provider=type("P", (), {"local_path": lambda s: "/r"})(), charts_path="charts"
            )

        replicas = [r for r in records if r.field_path == "spec.replicas"]
        assert len(replicas) == 1
        assert replicas[0].declared_value == "9"
        assert replicas[0].source == "manifest"

    def test_env_value_files_prepended_to_render_call(self, tmp_path):
        """Env value_files are resolved to absolute paths and prepended."""
        import yaml

        (tmp_path / "env-vals.yaml").write_text(
            yaml.dump({"replicaCount": "3"}), encoding="utf-8"
        )

        dep = _deployment(name="api", ns="prod")
        env = _env(name="production", value_files=["env-vals.yaml"])
        release = _helmfile_release(
            chart="api", environment="production", value_files=["release-vals.yaml"]
        )
        g = _graph(dep, env, release)

        received_value_files: list = []

        class CapturingRenderer:
            def render(self, chart, release_name, namespace, values, value_files=None):
                received_value_files.extend(value_files or [])
                return []

        import unittest.mock as mock
        engine = AnchorEngine(renderer=CapturingRenderer())
        provider = type("P", (), {"local_path": lambda s: tmp_path})()
        with mock.patch.object(AnchorEngine, "_resolve_chart", return_value="/charts/api"):
            engine._from_rendered_manifests(g, provider=provider, charts_path="charts")

        # Env value file (absolute path) comes before release value file
        assert len(received_value_files) == 2
        assert str(tmp_path / "env-vals.yaml") == received_value_files[0]
        assert "release-vals.yaml" == received_value_files[1]

    def test_no_env_value_files_when_env_file_missing(self, tmp_path):
        """Env value_file that doesn't exist on disk is not passed to renderer."""
        dep = _deployment(name="api", ns="prod")
        env = _env(name="production", value_files=["does-not-exist.yaml"])
        release = _helmfile_release(chart="api", environment="production")
        g = _graph(dep, env, release)

        received_value_files: list = []

        class CapturingRenderer:
            def render(self, chart, release_name, namespace, values, value_files=None):
                received_value_files.extend(value_files or [])
                return []

        import unittest.mock as mock
        engine = AnchorEngine(renderer=CapturingRenderer())
        provider = type("P", (), {"local_path": lambda s: tmp_path})()
        with mock.patch.object(AnchorEngine, "_resolve_chart", return_value="/charts/api"):
            engine._from_rendered_manifests(g, provider=provider, charts_path="charts")

        assert received_value_files == []

    def test_render_skipped_when_no_chart_found(self):
        """If _resolve_chart returns None, the release is silently skipped."""
        dep = _deployment(name="api", ns="prod")
        release = _helmfile_release(chart="api")
        g = _graph(dep, release)

        import unittest.mock as mock
        engine = AnchorEngine(renderer=self._make_renderer(5))
        with mock.patch.object(AnchorEngine, "_resolve_chart", return_value=None):
            records = engine._from_rendered_manifests(
                g, provider=type("P", (), {"local_path": lambda s: "/r"})(), charts_path="charts"
            )
        assert records == []

    def test_manifest_beats_k8s_schema_after_collect(self):
        """After full collect(), rendered replicas=7 wins over schema default=1."""
        dep = _deployment(name="api", ns="prod")
        release = _release(name="api", ns="prod", chart="api")
        g = _graph(dep, release)

        import unittest.mock as mock
        engine = AnchorEngine(renderer=self._make_renderer(7))
        provider = type("P", (), {"local_path": lambda s: "/r"})()
        with mock.patch.object(AnchorEngine, "_resolve_chart", return_value="/charts/api"):
            records = engine.collect(g, provider=provider)

        replicas = [r for r in records if r.field_path == "spec.replicas"]
        assert len(replicas) == 1
        assert replicas[0].source == "manifest"
        assert replicas[0].declared_value == "7"
