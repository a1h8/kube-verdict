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



# ── Step 2: section 7 "Checks" proposed by the LLM (docs/diagnostics.md) ────────
_ANSWER_V2 = _ANSWER + """
### 7. Checks
{checks}
"""


def _report_v2(remediation: str, checks: str) -> RCAReport:
    ctx = ContextWindow(seeds=[], drift=[], events=[], helm=[], related=[], traces=[], logs=[])
    return RCAReport(query="q", kube_version="1.30", context=ctx,
                     raw_analysis=_ANSWER_V2.format(remediation=remediation, checks=checks))


def test_full_check_line_is_structured():
    r = _report_v2("- helm upgrade api ./chart -n prod",
                   "- kubectl describe pod api-0 -n prod | verifies: why the container stopped"
                   " | confirms if: OOMKilled, exit 137 | refutes if: exit 0")
    assert r.diagnostics == [Diagnostic(
        command="kubectl describe pod api-0 -n prod", source="llm",
        purpose="why the container stopped", confirms_if="OOMKilled, exit 137",
        refutes_if="exit 0")]


def test_bare_command_and_backticks_are_accepted():
    r = _report_v2("- helm upgrade api ./chart -n prod", "- `kubectl top pod api-0 -n prod`")
    assert [(d.command, d.purpose) for d in r.diagnostics] == [("kubectl top pod api-0 -n prod", "")]


@pytest.mark.parametrize("line", [
    "- kubectl delete pod api-0 -n prod | verifies: nothing",
    "- kubectl get secret db -o yaml -n prod | verifies: the password",
    "- kubectl get secrets -ojson | verifies: all of them",
    "- kubectl get secret db --output=jsonpath='{.data}'",
])
def test_actions_and_secret_reads_never_enter_through_section_7(line):
    r = _report_v2("- helm upgrade api ./chart -n prod", line)
    assert r.diagnostics == []
    assert r.remediation == ["helm upgrade api ./chart -n prod"]


def test_listing_secret_names_is_allowed():
    r = _report_v2("", "- kubectl get secrets -n prod | verifies: which secrets exist")
    assert [d.command for d in r.diagnostics] == ["kubectl get secrets -n prod"]


def test_at_most_five_checks_deduplicated():
    lines = "\n".join(f"- kubectl get pods -n ns{i}" for i in range(8))
    r = _report_v2("", "- kubectl get pods -n ns0\n" + lines)
    assert [d.command for d in r.diagnostics] == [f"kubectl get pods -n ns{i}" for i in range(5)]


def test_confidence_does_not_swallow_section_7():
    r = _report_v2("", "- kubectl get pods -n prod")
    assert r.confidence == "LOW"


def test_proposed_checks_come_before_recovered_ones_without_duplicates():
    r = _report_v2("- kubectl get events -n prod\n- kubectl describe pod api-0 -n prod",
                   "- kubectl describe pod api-0 -n prod | verifies: the last state")
    assert [(d.command, d.purpose) for d in r.diagnostics] == [
        ("kubectl describe pod api-0 -n prod", "the last state"),
        ("kubectl get events -n prod", ""),
    ]


def test_answer_without_section_7_parses_as_before():
    r = _report("- kubectl get events -n prod")
    assert r.confidence == "LOW"
    assert [d.command for d in r.diagnostics] == ["kubectl get events -n prod"]
