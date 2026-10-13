"""Diagnostics are not remediation — docs/test-cases.md § h022 (prerequisite)."""
from __future__ import annotations

import pytest

from rca.analyzer import RCAReport
from rca.diagnostics import Diagnostic, is_read_only, split_diagnostics
from rca.context_builder import ContextWindow


@pytest.mark.parametrize("cmd", [
    "kubectl get events -n production",
    "kubectl describe pod api-0 -n prod",
    "kubectl logs api-0 -n prod --previous",
    "kubectl top pod api-0 -n prod",
    "kubectl explain deployment.spec",
    "kubectl events -n prod",
    "kubectl api-resources",
    "kubectl version",
    "kubectl diff -f deploy.yaml",
    "kubectl auth can-i list pods -n prod",
    "helm status api -n prod",
    "helm get values api -n prod",
    "helm history api -n prod",
    "helm list -n prod",
    "helm ls -A",
    "helm show values ./chart",
    "helm template api ./chart",
    "helm diff upgrade api ./chart -n prod",
    "helm lint ./chart",
    "kubectl rollout status deployment/api -n prod",
    "kubectl rollout history deployment/api -n prod",
    "$ kubectl get pods",
    "kubectl describe nodes | grep -A5 Taints",
    "kubectl get pods -o json | jq '.items[].metadata.name' | sort | head",
])
def test_read_only(cmd):
    assert is_read_only(cmd)


@pytest.mark.parametrize("cmd", [
    "kubectl apply -f fix.yaml",
    "kubectl create secret generic queue-credentials -n prod",
    "kubectl delete pod api-0 -n prod",
    "kubectl rollout restart deployment/api -n prod",
    "kubectl set image deployment/api api=img:2 -n prod",
    "kubectl edit networkpolicy deny -n prod",
    "kubectl patch deployment api -p '{}'",
    "kubectl scale deployment api --replicas=3",
    "kubectl auth reconcile -f rbac.yaml",
    "kubectl rollout undo deployment/api -n prod",
    "helm upgrade api ./chart -n prod",
    "helm rollback api -n prod",
    "helm uninstall api -n prod",
    "kubectl get pods; kubectl delete pod api-0",
    "kubectl get pods && kubectl delete pod api-0",
    "kubectl get pods || kubectl delete pod api-0",
    "kubectl get secret db -o yaml > backup.yaml",
    "kubectl get pods | xargs kubectl delete pod",
    "kubectl",
    "",
    "Increase the memory limit to 1Gi",
    "[rule:oom_kill w=0.88] Container OOMKilled (Pod/prod/worker-0)",
    "docker ps",
])
def test_not_read_only(cmd):
    assert not is_read_only(cmd)


def test_split_keeps_order_and_comments_follow_their_command():
    actions, checks = split_diagnostics([
        "# Check first:",
        "kubectl get pods -n prod",
        "# Then fix:",
        "helm upgrade api ./chart -n prod",
        "kubectl describe pod api-0 -n prod",
        "# trailing note",
    ])
    assert checks == ["# Check first:", "kubectl get pods -n prod",
                      "kubectl describe pod api-0 -n prod"]
    assert actions == ["# Then fix:", "helm upgrade api ./chart -n prod", "# trailing note"]


_ANSWER = """\
### 1. Summary
s
### 2. Affected resources
- Pod/prod/api-0 — OOMKilled
### 3. Root cause
r
### 4. Causal chain
1. a
### 5. Remediation
{remediation}
### 6. Confidence
LOW
"""


def _report(remediation: str) -> RCAReport:
    ctx = ContextWindow(seeds=[], drift=[], events=[], helm=[], related=[], traces=[], logs=[])
    return RCAReport(query="q", kube_version="1.30", context=ctx,
                     raw_analysis=_ANSWER.format(remediation=remediation))


def test_parser_moves_diagnostics_out_of_remediation():
    r = _report("- kubectl get events -n prod\n- helm upgrade api ./chart -n prod")
    assert r.remediation == ["helm upgrade api ./chart -n prod"]
    assert r.diagnostics == [Diagnostic(command="kubectl get events -n prod", source="llm")]
    assert r.rollback == ["helm rollback api -n prod"]


def test_only_diagnostics_leaves_remediation_empty():
    r = _report("kubectl get events -n prod --field-selector involvedObject.name=api-0")
    assert r.remediation == []
    assert r.rollback == []
    assert [d.command for d in r.diagnostics] == [
        "kubectl get events -n prod --field-selector involvedObject.name=api-0"]


def test_to_dict_exposes_diagnostics():
    d = _report("- kubectl get events -n prod").to_dict()
    assert d["diagnostics"] == [{
        "command": "kubectl get events -n prod", "source": "llm", "hypothesis": "",
        "affected": "", "purpose": "", "confirms_if": "", "refutes_if": "",
    }]
    assert d["remediation"] == []

