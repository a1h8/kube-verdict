"""Unit tests for _generate_rollback()."""
import pytest
from rca.analyzer import _generate_rollback


@pytest.mark.parametrize("remediation,expected", [
    (
        ["kubectl rollout restart deployment/payment-service -n prod"],
        ["kubectl rollout undo deployment/payment-service -n prod"],
    ),
    (
        ["$ kubectl rollout restart deployment/api -n staging"],
        ["kubectl rollout undo deployment/api -n staging"],
    ),
    (
        ["kubectl set image deployment/ml-inference ml-inference=registry/img:v2 -n prod"],
        ["kubectl rollout undo deployment/ml-inference -n prod"],
    ),
    (
        ["helm upgrade myapp ./charts/myapp --set image.tag=v2 -n prod"],
        ["helm rollback myapp -n prod"],
    ),
    (
        ["helm upgrade myapp ./charts/myapp"],
        ["helm rollback myapp"],
    ),
    (
        ["kubectl apply -f manifests/deployment.yaml"],
        [],  # apply also modifies existing objects: deleting them is not a rollback (H19)
    ),
    (
        ["kubectl create clusterrolebinding my-crb --clusterrole=view --serviceaccount=default:my-sa"],
        ["kubectl delete clusterrolebinding my-crb"],
    ),
    (
        ["kubectl create configmap app-env -n prod --from-literal=A=1"],
        ["kubectl delete configmap app-env -n prod"],
    ),
    (
        ["kubectl edit networkpolicy egress-policy -n prod"],
        [],  # no auto-rollback for edit
    ),
    (
        [],
        [],
    ),
])
def test_generate_rollback(remediation, expected):
    assert _generate_rollback(remediation) == expected


# ── docs/fix-plan-001.md, H19: every action needs its own way back ───────────
from rca.analyzer import unrecoverable_commands  # noqa: E402


def test_one_inverse_does_not_cover_an_irreversible_delete():
    cmds = ["kubectl delete pvc data-db-0 -n dev", "kubectl rollout restart deployment/api -n dev"]
    assert unrecoverable_commands(cmds) == ["kubectl delete pvc data-db-0 -n dev"]


@pytest.mark.parametrize("cmd", [
    "kubectl apply -f manifests/deployment.yaml",
    "kubectl delete namespace prod",
    "kubectl delete statefulset db -n prod",
    "kubectl patch deployment api -p '{}' -n prod",
    "rm -rf /var/lib/data",
])
def test_actions_without_inverse(cmd):
    assert unrecoverable_commands([cmd]) == [cmd]


@pytest.mark.parametrize("cmd", [                      # control: these do not block
    "kubectl rollout restart deployment/api -n prod",
    "helm upgrade api ./chart -n prod",
    "kubectl delete pod api-7d9f-x -n prod",
    "kubectl get pods -n prod",
    "# check first",
    "[rule:helm_drift w=0.95] Helm drift — cluster state diverged from chart declaration",
    "Restart the payment pods once the secret exists",
])
def test_recoverable_or_not_an_action(cmd):
    assert unrecoverable_commands([cmd]) == []
