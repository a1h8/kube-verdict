"""ontology/schema_registry.py on small schemas written for the test, then on the committed bundle."""
import pytest

from ontology.schema_registry import SchemaRegistry

OPENAPI = {"components": {"schemas": {
    "io.x.v1.Widget": {
        "x-kubernetes-group-version-kind": [{"group": "x.io", "version": "v1", "kind": "Widget"}],
        "type": "object",
        "properties": {
            "spec": {"allOf": [{"$ref": "#/components/schemas/io.x.v1.WidgetSpec"}], "default": {}},
            "status": {"$ref": "#/components/schemas/io.x.v1.WidgetStatus"}}},
    "io.x.v1.WidgetSpec": {"type": "object", "required": ["mode"], "properties": {
        "mode": {"type": "string", "enum": ["Fast", "Slow"], "default": "Fast"},
        "ports": {"type": "array", "x-kubernetes-list-type": "map", "x-kubernetes-list-map-keys": ["port", "protocol"],
                  "items": {"$ref": "#/components/schemas/io.x.v1.Port"}},
        "limits": {"type": "object", "additionalProperties": {"$ref": "#/components/schemas/Quantity"}},
        "extra": {"type": "object", "x-kubernetes-preserve-unknown-fields": True}}},
    "io.x.v1.Port": {"type": "object", "properties": {"port": {"type": "integer"},
                                                       "target": {"x-kubernetes-int-or-string": True}}},
    "Quantity": {"type": "string"},
    "io.x.v1.WidgetStatus": {"type": "object", "properties": {
        "observedGeneration": {"type": "integer"},
        "conditions": {"type": "array", "items": {"type": "object", "properties": {"type": {"type": "string"}}}}}},
    "io.x.v1beta1.Widget": {"x-kubernetes-group-version-kind": [{"group": "x.io", "version": "v1beta1",
                                                                 "kind": "Widget"}],
                            "type": "object", "properties": {"old": {"type": "string"}}},
}}}


def crd(group="y.io", kind="Gadget", versions=(("v1", True),)):
    return {"apiVersion": "apiextensions.k8s.io/v1", "kind": "CustomResourceDefinition", "spec": {
        "group": group, "names": {"kind": kind}, "versions": [
            {"name": v, "served": served, "schema": {"openAPIV3Schema": {"type": "object", "properties": {
                "spec": {"type": "object", "properties": {"selfHeal": {"type": "boolean"},
                                                          "phase": {"type": "string", "enum": ["A", "B"]}}},
                "status": {"type": "object", "properties": {"health": {"type": "object", "properties": {
                    "status": {"type": "string"}}}}}}}}} for v, served in versions]}}


@pytest.fixture
def reg():
    r = SchemaRegistry()
    r.add_openapi_v3(OPENAPI, "test")
    r.add_crds([crd(), {"kind": "ConfigMap"}], "crd-test")
    return r


def test_ref_and_allof_followed_enum_default_required(reg):
    f = reg.field("x.io", "Widget", "spec.mode")
    assert f.enum == ("Fast", "Slow") and f.default == "Fast" and f.has_default
    assert reg.field("x.io", "Widget", "spec").required == ("mode",)


def test_list_items_and_list_map_keys(reg):
    assert reg.field("x.io", "Widget", "spec.ports").list_map_keys == ("port", "protocol")
    assert reg.field("x.io", "Widget", "spec.ports[].port").type == "integer"
    assert reg.field("x.io", "Widget", "spec.ports[type=TCP].port").type == "integer"
    assert reg.field("x.io", "Widget", "spec.ports[].target").int_or_string
    assert reg.field("x.io", "Widget", "spec.mode[]") is None           # not a list


def test_map_values_and_placeholders(reg):
    assert reg.field("x.io", "Widget", "spec.limits.memory").type == "string"
    assert reg.field("x.io", "Widget", "spec.limits.{r}").type == "string"


def test_unknown_path_is_none_free_form_is_said(reg):
    assert reg.field("x.io", "Widget", "spec.nope") is None
    assert reg.field("x.io", "Nope", "spec") is None
    assert reg.field("x.io", "Widget", "spec.extra.anything.deep").preserve_unknown


def test_preferred_version_is_ga(reg):
    assert reg.versions("x.io", "Widget") == ["v1", "v1beta1"]
    assert reg.field("x.io", "Widget", "spec.mode") is not None
    assert reg.field("x.io", "Widget", "old", version="v1beta1").type == "string"


def test_crd_and_status_shape(reg):
    assert reg.field("y.io", "Gadget", "spec.phase").enum == ("A", "B")
    assert reg.field("y.io", "Gadget", "status.health.status").enum == ()
    assert reg.has_conditions("x.io", "Widget") and reg.has_observed_generation("x.io", "Widget")
    assert reg.has_status("y.io", "Gadget") and not reg.has_conditions("y.io", "Gadget")


def test_unserved_crd_version_ignored_and_later_source_wins():
    r = SchemaRegistry()
    r.add_crds([crd(versions=(("v1", False), ("v2", True)))])
    assert r.versions("y.io", "Gadget") == ["v2"]
    live = crd(versions=(("v2", True),))
    live["spec"]["versions"][0]["schema"]["openAPIV3Schema"]["properties"]["spec"]["properties"]["phase"]["enum"] = ["C"]
    r.add_crds([live], "live")
    assert r.field("y.io", "Gadget", "spec.phase").enum == ("C",)


# ---- the committed bundle (docs/schema-registry.md § 1); facts checked against the files on 2026-11-09

@pytest.fixture(scope="module")
def bundle():
    return SchemaRegistry.from_bundle()


def test_bundle_kubernetes(bundle):
    assert bundle.field("apps", "Deployment", "spec.template.spec.containers[].resources.limits.memory") is not None
    assert bundle.field("", "Pod", "spec.containers").list_map_keys == ("name",)
    # v1.35's OpenAPI v3 publishes no enum for built-in kinds: the values are only in the description
    restart = bundle.field("", "Pod", "spec.restartPolicy")
    assert restart.enum == () and "One of Always, OnFailure, Never" in restart.description
    assert bundle.field("apps", "Deployment", "spec.nope") is None
    assert bundle.has_conditions("apps", "Deployment")


def test_bundle_gitops_crds(bundle):
    assert bundle.field("argoproj.io", "Application", "status.health.status").enum == ()
    assert bundle.field("argoproj.io", "Application", "spec.syncPolicy.automated.selfHeal").type == "boolean"
    assert bundle.field("argoproj.io", "Application", "status.resources[].kind") is not None
    assert bundle.field("kustomize.toolkit.fluxcd.io", "Kustomization", "status.inventory.entries[].id") is not None
    assert bundle.field("kustomize.toolkit.fluxcd.io", "Kustomization", "status.lastAppliedRevision") is not None
    assert bundle.field("helm.toolkit.fluxcd.io", "HelmRelease", "status.lastAppliedRevision") is None
    assert bundle.field("helm.toolkit.fluxcd.io", "HelmRelease", "status.inventory.entries[].id") is not None
    assert bundle.field("cert-manager.io", "Certificate", "status.conditions[].status").enum


def test_bundle_sources_recorded(bundle):
    assert any(s.startswith("kubernetes@v1.35.5/") for s in bundle.sources())
    assert any(s.startswith("argo-cd@") for s in bundle.sources())
