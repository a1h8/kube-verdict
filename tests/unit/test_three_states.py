"""Declared → applied → live, the two comparisons and their provenance — docs/three-states.md
(the six tests of its table, each with a control that can fail)."""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

from ingestion.gitops_collector import GitopsCollector
from ingestion.helm_collector import HelmCollector
from ingestion.manifest_differ import compare_applied_to_live
from ontology.entities import Deployment, HelmRelease
from ontology.graph import OntologyGraph
from ontology.relationships import Edge, RelationshipType
from rca.signals import extract_signals


def _manifest(memory="512Mi", image="ghcr.io/acme/api:1", replicas=2):
    return {"kind": "Deployment", "metadata": {"name": "api", "namespace": "prod"},
            "spec": {"replicas": replicas, "template": {"spec": {"containers": [
                {"name": "api", "image": image, "resources": {"limits": {"memory": memory}}}]}}}}


def _graph(applied, live_memory="512Mi", live_image="ghcr.io/acme/api:1", live_replicas=2):
    g = OntologyGraph()
    g.add_entity(HelmRelease(uid="r", name="api", namespace="prod", source="helm",
                             applied_manifest=[applied] if applied else []))
    dep = Deployment(uid="d", name="api", namespace="prod", replicas=live_replicas,
                     ready_replicas=live_replicas,
                     containers=[{"name": "api", "image": live_image, "imagePullPolicy": "IfNotPresent",
                                  "terminationMessagePath": "/dev/termination-log",
                                  "resources": {"limits": {"memory": live_memory}}}])
    dep.annotations["spec.container.api.image"] = live_image
    g.add_entity(dep)
    return g


def _run_a(g, declared):
    renderer = MagicMock(); renderer.render.return_value = [declared]
    c = GitopsCollector(MagicMock(), renderer=renderer)
    c._resolve_chart = lambda release: ("/repo/charts/api", None)
    c.collect(g)
    return c


def _drifts(g):
    ss = extract_signals(g)
    return sorted((s.attr("field"), s.attr("provenance")) for s in ss.of("drift.config"))


def test_a_live_edit_is_edited_outside_helm():
    g = _graph(_manifest("512Mi"), live_memory="128Mi")
    compare_applied_to_live(g)
    assert _drifts(g) == [("container.api.resources.limits.memory", "edited-outside-helm")]
    control = _graph(_manifest("512Mi"))
    compare_applied_to_live(control)
    assert _drifts(control) == []


def test_deployed_not_git_is_git_vs_deployed():
    g = _graph(_manifest("1Gi"), live_memory="1Gi")
    compare_applied_to_live(g)
    _run_a(g, _manifest("512Mi"))
    assert _drifts(g) == [("container.api.resources.limits.memory", "git-vs-deployed")]
    from rca.diagnosis import diagnose
    assert diagnose(extract_signals(g)).outcome == "no_incident"     # a drift alone, nothing failing
    control = _graph(_manifest("1Gi"), live_memory="1Gi")
    _run_a(control, _manifest("1Gi"))
    assert _drifts(control) == []


def test_both_comparisons_report_their_own_drift():
    g = _graph(_manifest("1Gi"), live_memory="128Mi")
    compare_applied_to_live(g)
    _run_a(g, _manifest("512Mi"))
    assert {p for _, p in _drifts(g)} == {"edited-outside-helm", "git-vs-deployed"}
    assert len(_drifts(g)) == 2


def test_fields_kubernetes_fills_and_hpa_replicas_are_not_drifts():
    g = _graph(_manifest(replicas=2), live_replicas=7)          # live adds defaults, HPA scaled
    g.add_entity(Deployment(uid="hpa", name="api-hpa", namespace="prod"))
    g.add_edge(Edge("hpa", "d", RelationshipType.SCALES))
    compare_applied_to_live(g)
    assert _drifts(g) == []
    control = _graph(_manifest(image="ghcr.io/acme/api:1"), live_image="ghcr.io/acme/api:2")
    compare_applied_to_live(control)
    assert _drifts(control) == [("container.api.image", "edited-outside-helm")]


def test_no_access_to_release_secrets_is_reported():
    h = HelmCollector()
    err = subprocess.CalledProcessError(1, "helm", stderr='secrets is forbidden: cannot list')
    with patch("subprocess.run", side_effect=err):
        assert h._get_manifest("api", "prod") == []
    assert h.failures and h.failures[0].startswith("helm get manifest api")
    g = _graph(None)
    c = _run_a(g, _manifest("512Mi"))
    assert c.missing_applied == ["prod/api"] and _drifts(g) == []       # never Git vs live
    control = _graph(_manifest("512Mi"))
    assert _run_a(control, _manifest("512Mi")).missing_applied == []


def test_a_chart_change_shows_in_a_not_in_b():
    g = _graph(_manifest(image="ghcr.io/acme/api:1.2"), live_image="ghcr.io/acme/api:1.2")
    compare_applied_to_live(g)
    _run_a(g, _manifest(image="ghcr.io/acme/api:1.3"))        # Git's chart moved on, not deployed
    assert _drifts(g) == [("container.api.image", "git-vs-deployed")]
