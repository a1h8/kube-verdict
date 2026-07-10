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


# ── Render-vs-live path (h012): drift_evidence drives the risk, not commands ──

def _drift_row(kind, name, namespace, diffs):
    return {"kind": kind, "name": name, "namespace": namespace, "diffs": diffs}


def test_blast_radius_prefers_rendered_diff():
    """When render-vs-live drift exists, risk comes from the actual changed
    objects (rendered-diff), not from parsing the command strings."""
    state = _state(
        ["kubectl rollout restart deployment/api -n production"],  # would be LOW as heuristic
        ["deployment/production/api"],
    )
    state["drift_evidence"] = [
        _drift_row("Deployment", "api", "production", [
            {"field_path": "spec.replicas", "declared": "3", "observed": "1", "severity": "critical"},
            {"field_path": "container.api.resources.memory", "declared": "512Mi", "observed": "128Mi", "severity": "warning"},
        ]),
    ]
    br = blast_radius_node(state, {})["blast_radius"]
    assert br["method"] == "rendered-diff"
    assert br["risk"] == "HIGH"          # critical drift, not the LOW heuristic
    assert br["changed"] == 2
    assert br["namespaces"] == ["production"]


def test_blast_radius_falls_back_to_heuristic_without_drift():
    state = _state(
        ["kubectl rollout restart deployment/api -n prod"],
        ["deployment/prod/api"],
    )
    state["drift_evidence"] = []  # no rendered diff → command heuristic
    br = blast_radius_node(state, {})["blast_radius"]
    assert br["method"] == "command-heuristic"
    assert br["risk"] == "LOW"
