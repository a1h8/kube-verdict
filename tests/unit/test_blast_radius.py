"""Unit tests for blast_radius_node and _parse_command_scope."""
import pytest
from remediation.blast_radius import _parse_command_scope
from workflow.nodes import blast_radius_node


@pytest.mark.parametrize("cmd,expected_ns,expected_kind,expected_cluster", [
    ("kubectl rollout restart deployment/api -n prod", "prod", "deployment", False),
    ("kubectl set image deployment/ml ml=img:v2 -n staging", "staging", "deployment", False),
    ("helm upgrade myapp ./chart -n prod --set image.tag=v2", "prod", "helm-release", False),
    ("kubectl create clusterrolebinding crb --clusterrole=view --serviceaccount=default:sa", None, None, True),
    ("kubectl rollout restart deployment/api", None, "deployment", False),
])
def test_parse_command_scope(cmd, expected_ns, expected_kind, expected_cluster):
    scope = _parse_command_scope(cmd)
    assert scope["namespace"] == expected_ns
    assert scope["kind"] == expected_kind
    assert scope["cluster_scoped"] == expected_cluster


def _state(remediation, affected):
    return {"report_dict": {"remediation": remediation, "affected": affected}}


def test_blast_radius_no_remediation():
    result = blast_radius_node({"report_dict": {}}, {})
    assert result["blast_radius"]["risk"] == "LOW"
    assert result["blast_radius"]["command_count"] == 0


def test_blast_radius_low():
    state = _state(
        ["kubectl rollout restart deployment/api -n prod"],
        ["deployment/prod/api — CrashLoopBackOff"],
    )
    result = blast_radius_node(state, {})
    br = result["blast_radius"]
    assert br["risk"] == "LOW"
    assert br["namespaces"] == ["prod"]
    assert br["command_count"] == 1


def test_blast_radius_medium():
    state = _state(
        ["kubectl rollout restart deployment/api -n prod"],
        [f"deployment/prod/svc-{i}" for i in range(5)],
    )
    result = blast_radius_node(state, {})
    assert result["blast_radius"]["risk"] == "MEDIUM"


def test_blast_radius_high_multi_namespace():
    state = _state(
        [
            "kubectl rollout restart deployment/api -n prod",
            "kubectl rollout restart deployment/worker -n staging",
        ],
        ["deployment/prod/api — CrashLoopBackOff"],
    )
    result = blast_radius_node(state, {})
    assert result["blast_radius"]["risk"] == "HIGH"
    assert set(result["blast_radius"]["namespaces"]) == {"prod", "staging"}


def test_blast_radius_high_cluster_scoped():
    state = _state(
        ["kubectl create clusterrolebinding crb --clusterrole=view --serviceaccount=default:sa"],
        ["serviceaccount/default/sa — 403 Forbidden"],
    )
    result = blast_radius_node(state, {})
    assert result["blast_radius"]["risk"] == "HIGH"
    assert result["blast_radius"]["cluster_scoped"] is True
