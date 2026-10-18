"""A rendered object is compared with the live object of its own namespace —
docs/fix-plan-001.md, H29."""
from __future__ import annotations

from ingestion.manifest_differ import ManifestDiffer
from ontology.entities import Deployment
from ontology.graph import OntologyGraph

def _graph(ns):
    g = OntologyGraph()
    g.add_entity(Deployment(uid="d", name="api", namespace=ns, replicas=1, ready_replicas=1))
    return g


RENDERED = [{"kind": "Deployment", "metadata": {"name": "api"}, "spec": {"replicas": 3}}]


def test_a_render_without_namespace_is_not_matched_in_another_namespace():
    drifts = ManifestDiffer(track_orphans=False).diff(RENDERED, _graph("staging"), namespace="prod")
    assert not any(d.field_path == "spec.replicas" for d in drifts)


def test_the_release_namespace_is_matched():                               # control
    drifts = ManifestDiffer(track_orphans=False).diff(RENDERED, _graph("prod"), namespace="prod")
    assert any(d.field_path == "spec.replicas" for d in drifts)
