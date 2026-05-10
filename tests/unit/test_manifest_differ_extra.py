"""
Additional ManifestDiffer tests — image drift, service ports, env vars, orphans.
"""
import pytest

from ingestion.manifest_differ import ManifestDiffer, _diff_service_ports, _diff_env
from ontology.entities import Deployment, Pod, Service
from ontology.graph import OntologyGraph


@pytest.fixture
def graph():
    g = OntologyGraph()
    g.add_entity(Deployment(
        uid="d-api", name="api", namespace="prod",
        replicas=3, ready_replicas=3,
        labels={"app.kubernetes.io/managed-by": "Helm"},
    ))
    g.add_entity(Pod(
        uid="p-api", name="api-0", namespace="prod",
        container_statuses=[
            {"name": "api", "image": "myapp:1.0.0", "state": "Running"}
        ],
    ))
    g.add_entity(Service(
        uid="svc-api", name="api", namespace="prod",
        ports=[{"port": 80, "protocol": "TCP"}],
    ))
    return g


class TestImageDrift:
    def test_image_drift_detected(self, graph):
        differ = ManifestDiffer()
        rendered = [{
            "kind": "Pod",
            "metadata": {"name": "api-0", "namespace": "prod"},
            "spec": {
                "containers": [{"name": "api", "image": "myapp:2.0.0"}]
            },
        }]
        drifts = differ.diff(rendered, graph)
        img_drifts = [d for d in drifts if "image" in d.field_path]
        assert len(img_drifts) == 1
        assert img_drifts[0].declared == "myapp:2.0.0"
        assert img_drifts[0].observed == "myapp:1.0.0"
        assert img_drifts[0].severity == "warning"

    def test_no_image_drift_when_same(self, graph):
        differ = ManifestDiffer()
        rendered = [{
            "kind": "Pod",
            "metadata": {"name": "api-0", "namespace": "prod"},
            "spec": {
                "containers": [{"name": "api", "image": "myapp:1.0.0"}]
            },
        }]
        drifts = differ.diff(rendered, graph)
        img_drifts = [d for d in drifts if "image" in d.field_path]
        assert img_drifts == []


class TestServicePortDrift:
    def test_missing_port_detected(self):
        svc = Service(uid="svc-1", name="api", namespace="prod",
                      ports=[{"port": 80, "protocol": "TCP"}])
        spec = {"ports": [{"containerPort": 8080, "protocol": "TCP"}]}
        drifts = _diff_service_ports(spec, svc)
        assert len(drifts) == 1
        assert "8080" in drifts[0].field_path

    def test_no_drift_when_ports_match(self):
        svc = Service(uid="svc-1", name="api", namespace="prod",
                      ports=[{"port": 80, "protocol": "TCP"}])
        spec = {"ports": [{"containerPort": 80, "port": 80, "protocol": "TCP"}]}
        drifts = _diff_service_ports(spec, svc)
        assert drifts == []

    def test_no_ports_on_entity_skipped(self):
        dep = Deployment(uid="d1", name="api", namespace="prod")
        drifts = _diff_service_ports({"ports": [{"containerPort": 80}]}, dep)
        assert drifts == []


class TestEnvDiff:
    def test_sensitive_env_drift_detected(self):
        pod = Pod(uid="p1", name="api", namespace="prod",
                  annotations={"env.api.DATABASE_URL": "postgres://old/db"})
        rendered_env = [{"name": "DATABASE_URL", "value": "postgres://new/db"}]
        drifts = _diff_env("api", rendered_env, pod)
        assert len(drifts) == 1
        assert "DATABASE_URL" in drifts[0].field_path

    def test_non_sensitive_env_skipped(self):
        pod = Pod(uid="p1", name="api", namespace="prod",
                  annotations={"env.api.SOME_VAR": "old"})
        rendered_env = [{"name": "SOME_VAR", "value": "new"}]
        drifts = _diff_env("api", rendered_env, pod)
        assert drifts == []

    def test_no_drift_when_values_match(self):
        pod = Pod(uid="p1", name="api", namespace="prod",
                  annotations={"env.api.DATABASE_URL": "postgres://db"})
        rendered_env = [{"name": "DATABASE_URL", "value": "postgres://db"}]
        drifts = _diff_env("api", rendered_env, pod)
        assert drifts == []

    def test_no_observed_annotation_skipped(self):
        pod = Pod(uid="p1", name="api", namespace="prod")
        rendered_env = [{"name": "DATABASE_URL", "value": "postgres://db"}]
        drifts = _diff_env("api", rendered_env, pod)
        assert drifts == []


class TestOrphanDetection:
    def test_orphan_flagged_when_track_orphans_enabled(self):
        g = OntologyGraph()
        g.add_entity(Deployment(
            uid="d1", name="legacy", namespace="prod",
            labels={"app.kubernetes.io/managed-by": "Helm"},
        ))
        differ = ManifestDiffer(track_orphans=True)
        # Pass no rendered manifests — all Helm-managed entities are "orphaned"
        drifts = differ.diff([], g)
        orphan_drifts = [d for d in drifts if d.observed == "present"]
        assert len(orphan_drifts) == 1

    def test_orphan_not_flagged_when_disabled(self):
        g = OntologyGraph()
        g.add_entity(Deployment(
            uid="d1", name="legacy", namespace="prod",
            labels={"app.kubernetes.io/managed-by": "Helm"},
        ))
        differ = ManifestDiffer(track_orphans=False)
        drifts = differ.diff([], g)
        assert drifts == []

    def test_non_helm_managed_not_orphaned(self):
        g = OntologyGraph()
        g.add_entity(Deployment(uid="d1", name="manual", namespace="prod"))
        differ = ManifestDiffer(track_orphans=True)
        drifts = differ.diff([], g)
        assert drifts == []
