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
        ["kubectl delete -f manifests/deployment.yaml"],
    ),
    (
        ["kubectl create clusterrolebinding my-crb --clusterrole=view --serviceaccount=default:my-sa"],
        ["kubectl delete clusterrolebinding my-crb --clusterrole=view --serviceaccount=default:my-sa"],
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
