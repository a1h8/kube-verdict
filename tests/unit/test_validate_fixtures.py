"""Fixture objects checked against their definitions (docs/schema-registry.md § 4)."""
from datetime import datetime, timezone

import pytest

from ontology.schema_registry import SchemaRegistry, validate


@pytest.fixture(scope="module")
def reg():
    return SchemaRegistry.from_bundle()


def deployment(**spec_extra):
    return {"apiVersion": "apps/v1", "kind": "Deployment",
            "metadata": {"name": "api", "namespace": "shop", "labels": {"app": "api"},
                         "creationTimestamp": datetime(2026, 10, 20, tzinfo=timezone.utc),
                         "ownerReferences": [{"apiVersion": "v1", "kind": "X", "name": "x", "uid": "u"}]},
            "spec": {"replicas": 2, "selector": {"matchLabels": {"app": "api"}},
                     "template": {"metadata": {"labels": {"app": "api"}}, "spec": {"containers": [
                         {"name": "api", "image": "api:1", "ports": [{"containerPort": 8080}],
                          "resources": {"limits": {"memory": "256Mi", "cpu": 1}}}]}}, **spec_extra}}


def test_a_valid_object_has_no_problem(reg):
    assert validate(reg, deployment()) == []


def test_unknown_field_wrong_type_missing_required(reg):
    d = deployment(replicaz=3)
    d["spec"]["replicas"] = "two"
    del d["metadata"]["ownerReferences"][0]["uid"]
    assert sorted(validate(reg, d)) == sorted([
        "Deployment.spec.replicaz: not in the definition",
        "Deployment.spec.replicas: str where the definition has integer",
        "Deployment.metadata.ownerReferences[0].uid: required, missing"])


def test_bool_is_not_an_integer(reg):
    d = deployment()
    d["spec"]["replicas"] = True
    assert validate(reg, d) == ["Deployment.spec.replicas: bool where the definition has integer"]


def test_enum_from_a_crd(reg):
    cert = {"apiVersion": "cert-manager.io/v1", "kind": "Certificate", "metadata": {"name": "c"},
            "spec": {"secretName": "s", "issuerRef": {"name": "i"}},
            "status": {"conditions": [{"type": "Ready", "status": "Maybe"}]}}
    assert any("'Maybe' is not one of" in p for p in validate(reg, cert))


def test_unknown_kind_and_free_form(reg):
    assert validate(reg, {"apiVersion": "nope.io/v1", "kind": "Thing"}) == ["nope.io/v1 Thing: no definition"]
    ks = {"apiVersion": "kustomize.toolkit.fluxcd.io/v1", "kind": "Kustomization", "metadata": {"name": "apps"},
          "spec": {"interval": "1m", "prune": True, "sourceRef": {"kind": "GitRepository", "name": "r"},
                   "patches": [{"patch": "x"}]}}
    assert validate(reg, ks) == []


def test_validator_tool_on_a_case(tmp_path, reg):
    from tools.validate_fixtures import check_case
    (tmp_path / "kube").mkdir()
    (tmp_path / "kube" / "d.yaml").write_text(
        "apiVersion: v1\nkind: List\nitems:\n- apiVersion: apps/v1\n  kind: Deployment\n  metadata: {name: a}\n"
        "  spec: {selector: {}, template: {}, bogus: 1}\n")
    n, problems = check_case(reg, tmp_path)
    assert n == 1 and problems == ["kube/d.yaml a: Deployment.spec.bogus: not in the definition"]


def test_preserve_unknown_with_properties_accepts_other_fields():
    # absent from the bundle's definitions, allowed by structural schemas: known properties plus any other field
    r = SchemaRegistry()
    r.add_crds([{"kind": "CustomResourceDefinition", "spec": {"group": "a.io", "names": {"kind": "T"}, "versions": [
        {"name": "v1", "schema": {"openAPIV3Schema": {"type": "object", "properties": {
            "apiVersion": {"type": "string"}, "kind": {"type": "string"}, "spec": {
            "type": "object", "x-kubernetes-preserve-unknown-fields": True,
            "properties": {"a": {"type": "string"}}}}}}}]}}])
    assert validate(r, {"apiVersion": "a.io/v1", "kind": "T", "spec": {"a": "x", "b": {"c": 1}}}) == []


# Measured 2026-11-09 (docs/schema-registry.md, step 5): S1 and S2's fixtures omit only fields a real API server always
# returns, none of which the product reads. Any other problem (an unknown field, a wrong type, a value outside an enum,
# a kind with no definition) or a new omitted field fails here.
KNOWN_OMISSIONS = {
    "Pod.status.containerStatuses[].imageID", "Pod.status.containerStatuses[].image", "Pod.status.containerStatuses[].ready",
    "Pod.status.initContainerStatuses[].imageID", "Pod.status.initContainerStatuses[].image",
    "Pod.metadata.ownerReferences[].uid", "ReplicaSet.metadata.ownerReferences[].uid",
    "ReplicaSet.metadata.ownerReferences[].apiVersion", "Job.spec.template", "Job.metadata.ownerReferences[].uid",
    "APIService.spec.versionPriority", "APIService.spec.groupPriorityMinimum"}


def test_s1_s2_fixtures_only_known_omissions(reg):
    import re
    from pathlib import Path

    from tools.validate_fixtures import check_case
    root = Path(__file__).resolve().parents[1]
    seen = set()
    for base in (root / "integration" / "cases", root / "s2" / "cases"):
        for case in sorted(p for p in base.iterdir() if (p / "kube").is_dir()):
            for p in check_case(reg, case)[1]:
                tail = p.split(": ", 1)[1]
                assert tail.endswith(": required, missing"), p
                seen.add(re.sub(r"\[\d+\]", "[]", tail.removesuffix(": required, missing")))
    assert seen <= KNOWN_OMISSIONS, seen - KNOWN_OMISSIONS
