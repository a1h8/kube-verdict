"""MANAGES from the GitOps managers' inventories and Argo CD's tracking annotation (docs/gitops-managed-by.md)."""
from __future__ import annotations

from ontology.declared_relations import build_named
from ontology.discovery import APIResourceInfo
from ontology.dynamic_entity import GenericEntity
from ontology.entities import Deployment, Namespace
from ontology.graph import OntologyGraph
from ontology.relationships import RelationshipType


def _crd(kind, group, name, ns, status):
    info = APIResourceInfo(group=group, version="v1", kind=kind, plural=kind.lower() + "s", namespaced=True,
                           verbs=["list"])
    return GenericEntity.from_api_object({"apiVersion": f"{group}/v1", "metadata": {"name": name, "namespace": ns,
                                                                                     "uid": f"{kind}-{name}"},
                                          "status": status}, info)


def _graph() -> OntologyGraph:
    g = OntologyGraph()
    for ns in ("argocd", "flux-system", "shop"):
        g.add_entity(Namespace(uid=f"ns-{ns}", name=ns))
    g.add_entity(Deployment(uid="d-api", name="api", namespace="shop", raw={"apiVersion": "apps/v1"}))
    g.add_entity(Deployment(uid="d-web", name="web", namespace="shop", raw={"apiVersion": "apps/v1"},
                            k8s_annotations={"argocd.argoproj.io/tracking-id": "storefront:apps/Deployment:shop/web"}))
    return g


def _manages(g):
    build_named(g, "MANAGES")
    return {(e.source_uid, e.target_uid, e.metadata["method"]) for e in g._edges
            if e.rel_type == RelationshipType.MANAGES}


def test_an_argo_application_manages_what_its_status_resources_list():
    g = _graph()
    g.add_entity(_crd("Application", "argoproj.io", "shopapp", "argocd",
                      {"resources": [{"group": "apps", "kind": "Deployment", "namespace": "shop", "name": "api"},
                                     {"group": "apps", "kind": "Deployment", "namespace": "shop", "name": "gone"}]}))
    assert ("Application-shopapp", "d-api", "inventory") in _manages(g)
    assert not any(t == "d-web" for s, t, m in _manages(g) if s == "Application-shopapp")


def test_the_tracking_annotation_names_its_application_in_any_namespace():
    g = _graph()
    g.add_entity(_crd("Application", "argoproj.io", "storefront", "argocd", {}))
    assert ("Application-storefront", "d-web", "marker") in _manages(g)


def test_a_flux_kustomization_manages_its_inventory_entries():
    g = _graph()
    g.add_entity(_crd("Kustomization", "kustomize.toolkit.fluxcd.io", "apps", "flux-system",
                      {"inventory": {"entries": [{"id": "shop_api_apps_Deployment", "v": "v1"},
                                                 {"id": "shop_api_apps_StatefulSet", "v": "v1"}]}}))
    assert _manages(g) == {("Kustomization-apps", "d-api", "inventory")}


def test_no_manager_no_edge():
    assert _manages(_graph()) == set()


def test_an_inventory_entry_of_another_kind_does_not_match_a_same_named_object():
    g = _graph()
    g.add_entity(_crd("Kustomization", "kustomize.toolkit.fluxcd.io", "apps", "flux-system",
                      {"inventory": {"entries": [{"id": "shop_api_apps_StatefulSet", "v": "v1"}]}}))
    assert _manages(g) == set()
