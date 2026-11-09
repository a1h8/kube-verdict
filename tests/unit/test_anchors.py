"""The anchor engine's conventions (ontology/anchors.py, M2 step 3), on fixtures written for this test."""
from __future__ import annotations

from ontology.anchors import evaluate
from ontology.discovery import APIResourceInfo
from ontology.dynamic_entity import GenericEntity
from ontology.graph import OntologyGraph
from ontology.relationships import Edge, RelationshipType as R


def _generic(kind, group, conditions, version="v1"):
    info = APIResourceInfo(group=group, version=version, kind=kind, plural=kind.lower() + "s", namespaced=True,
                           verbs=["list"])
    return GenericEntity.from_api_object({"apiVersion": f"{group}/{version}" if group else version,
                                          "metadata": {"name": "x", "namespace": "ns", "uid": kind},
                                          "status": {"conditions": conditions}}, info)


def _edge(e):
    return Edge(e.uid, "pod", R.OWNS, {"method": "ownerReferences"})


def test_a_stalled_flux_kustomization_is_a_fault_with_no_flux_rule():
    k = _generic("Kustomization", "kustomize.toolkit.fluxcd.io",
                 [{"type": "Stalled", "status": "True", "reason": "BuildFailed", "message": "kustomize build failed"}])
    found = evaluate(OntologyGraph(), k, _edge(k), None)
    assert found and "Stalled (kstatus): BuildFailed" in found[0]


def test_stalled_on_any_group_is_a_fault_read_from_the_object_alone():
    # kstatus README: status computed "solely based on the data in the resource"; no opt-in for CRDs (2026-11-09)
    other = _generic("Widget", "example.com", [{"type": "Stalled", "status": "True", "reason": "X", "message": "m"}])
    found = evaluate(OntologyGraph(), other, _edge(other), None)
    assert found and "Stalled (kstatus): X: m" in found[0]


def test_stalled_false_or_absent_is_not_a_fault():
    for conds in ([{"type": "Stalled", "status": "False", "reason": "X", "message": ""}],
                  [{"type": "Reconciling", "status": "True", "reason": "Progressing", "message": ""}], []):
        w = _generic("Widget", "example.com", conds)
        assert evaluate(OntologyGraph(), w, _edge(w), None) is None


def test_a_documented_polarity_judges_a_condition():
    hpa = _generic("HorizontalPodAutoscaler", "autoscaling",
                   [{"type": "ScalingActive", "status": "False", "reason": "FailedGetResourceMetric", "message": "m"}],
                   version="v2")
    found = evaluate(OntologyGraph(), hpa, _edge(hpa), None)
    assert found and "ScalingActive=False" in found[0]


def test_a_condition_with_no_documented_polarity_is_not_a_fault():
    hpa = _generic("HorizontalPodAutoscaler", "autoscaling",
                   [{"type": "ScalingLimited", "status": "True", "reason": "TooManyReplicas", "message": "m"}],
                   version="v2")
    assert evaluate(OntologyGraph(), hpa, _edge(hpa), None) is None


def test_a_healthy_ready_kustomization_is_not_a_fault():
    k = _generic("Kustomization", "kustomize.toolkit.fluxcd.io",
                 [{"type": "Ready", "status": "True", "reason": "ReconciliationSucceeded", "message": "ok"}])
    assert evaluate(OntologyGraph(), k, _edge(k), None) is None


def test_a_polarity_applies_to_its_own_kind_only():
    """Node's Ready polarity must not judge another kind's Ready condition."""
    w = _generic("Widget", "example.com", [{"type": "Ready", "status": "False", "reason": "X", "message": ""}])
    assert evaluate(OntologyGraph(), w, _edge(w), None) is None
