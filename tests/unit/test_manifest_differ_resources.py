"""GitOps resource diff reads the live containers — docs/case-audit-001.md § Correction F9."""
from __future__ import annotations

from ingestion.manifest_differ import _diff_resources
from ontology.entities import Deployment

RENDERED = {"limits": {"memory": "512Mi", "cpu": "1"}, "requests": {"memory": "256Mi", "cpu": "200m"}}


def _dep(limits: dict, requests: dict) -> Deployment:
    return Deployment(uid="d", name="api", namespace="prod",
                      containers=[{"name": "api", "resources": {"limits": limits, "requests": requests}}])


def test_limits_and_requests_are_compared_separately():
    drifts = _diff_resources("api", RENDERED, _dep({"memory": "128Mi", "cpu": "1"}, {"memory": "256Mi", "cpu": "200m"}))
    assert [(d.field_path, d.declared, d.observed) for d in drifts] == [
        ("container.api.resources.limits.memory", "512Mi", "128Mi")]


def test_a_request_drift_is_not_reported_as_a_limit():
    drifts = _diff_resources("api", RENDERED, _dep({"memory": "512Mi", "cpu": "1"}, {"memory": "64Mi", "cpu": "200m"}))
    assert [d.field_path for d in drifts] == ["container.api.resources.requests.memory"]


def test_an_annotation_no_longer_feeds_the_observed_side():
    dep = _dep({"memory": "512Mi", "cpu": "1"}, {"memory": "256Mi", "cpu": "200m"})
    dep.annotations["gitops.resources.api.memory"] = "128Mi"      # what the old test injected
    assert _diff_resources("api", RENDERED, dep) == []


def test_a_container_missing_from_the_live_object_gives_nothing():
    assert _diff_resources("sidecar", RENDERED, _dep({"memory": "128Mi"}, {})) == []
