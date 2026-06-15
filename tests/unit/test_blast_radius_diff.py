"""Rendered-vs-live diff blast radius — the real impact, not a command heuristic."""
from __future__ import annotations

from types import SimpleNamespace

from models import BlastRadius
from remediation.blast_radius import (
    compute_blast_radius_from_diff,
    render_diff_blast_radius,
)


def drift(field_path: str, severity: str):
    return SimpleNamespace(field_path=field_path, severity=severity)


def test_no_changes_is_low():
    br = compute_blast_radius_from_diff([], rollback_cmds=["helm rollback api"])
    assert br["risk"] == "LOW"
    assert br["method"] == "rendered-diff"
    assert br["changed"] == 0


def test_critical_change_is_high():
    br = compute_blast_radius_from_diff(
        [drift("Deployment.production.api", "critical")],
        rollback_cmds=["helm rollback api"],
        namespaces=["production"],
    )
    assert br["risk"] == "HIGH"
    assert br["by_severity"]["critical"] == 1
    assert br["changed"] == 1


def test_single_warning_is_medium():
    br = compute_blast_radius_from_diff(
        [drift("container.api.image", "warning")],
        rollback_cmds=["helm rollback api"],
        namespaces=["staging"],
    )
    assert br["risk"] == "MEDIUM"


def test_info_only_is_low():
    br = compute_blast_radius_from_diff(
        [drift("container.api.resources.cpu", "info")],
        rollback_cmds=["helm rollback api"],
        namespaces=["staging"],
    )
    assert br["risk"] == "LOW"


def test_cluster_scoped_change_is_high():
    br = compute_blast_radius_from_diff(
        [drift("ClusterRole.cluster.api-reader", "info")],
        rollback_cmds=["kubectl delete clusterrole api-reader"],
    )
    assert br["cluster_scoped"] is True
    assert br["risk"] == "HIGH"


def test_no_rollback_escalates_to_critical():
    br = compute_blast_radius_from_diff(
        [drift("Deployment.production.api", "critical")],
        rollback_cmds=[],
        namespaces=["production"],
    )
    assert br["risk"] == "CRITICAL"
    assert br["rollback_available"] is False


def test_blast_radius_model_from_diff():
    br = BlastRadius.from_diff(
        [drift("Deployment.production.api", "warning")],
        rollback_cmds=["helm rollback api"],
        namespaces=["production"],
    )
    d = br.to_dict()
    assert d["method"] == "rendered-diff"
    assert d["risk"] in ("MEDIUM", "HIGH")


def test_render_diff_end_to_end_with_stubs():
    class StubRenderer:
        def render(self, *a, **k):
            return [{"kind": "Deployment", "metadata": {"name": "api"}}]

    class StubDiffer:
        def diff(self, rendered, graph, release_uid=""):
            return [drift("Deployment.production.api", "critical")]

    br = render_diff_blast_radius(
        chart="./chart", release_name="api", namespace="production",
        values={"image.tag": "v2"}, graph=object(), rollback_cmds=["helm rollback api"],
        renderer=StubRenderer(), differ=StubDiffer(),
    )
    assert br is not None
    assert br["method"] == "rendered-diff"
    assert br["risk"] == "HIGH"


def test_render_diff_returns_none_when_render_fails():
    class EmptyRenderer:
        def render(self, *a, **k):
            return []

    br = render_diff_blast_radius(
        chart="./bad", release_name="api", namespace="production",
        values={}, graph=object(), rollback_cmds=[],
        renderer=EmptyRenderer(),
    )
    assert br is None  # caller falls back to the command heuristic
