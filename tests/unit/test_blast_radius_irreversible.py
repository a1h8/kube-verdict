"""An irreversible action is not AUTO because another command has an inverse —
docs/fix-plan-001.md, H19 (proved: delete pvc + rollout restart, LLM HIGH → AUTO)."""
from __future__ import annotations

from decision import policy_gate
from workflow.nodes import blast_radius_node


def _verdict(remediation):
    state = {"report_dict": {"remediation": remediation, "affected": ["Pod/dev/db-0"]}}
    br = blast_radius_node(state, {})["blast_radius"]
    gate = policy_gate.evaluate(score=0.85, risk=br["risk"], namespace="dev",
                                rollback_available=br["rollback_available"],
                                command_count=len(remediation))
    return br, gate.verdict.value


def test_delete_pvc_with_a_restart_is_no_go():
    br, verdict = _verdict(["kubectl delete pvc data-db-0 -n dev",
                            "kubectl rollout restart deployment/api -n dev"])
    assert br["rollback_available"] is False
    assert br["unrecoverable"] == ["kubectl delete pvc data-db-0 -n dev"]
    assert verdict == "NO_GO"


def test_a_restart_alone_keeps_its_rollback():                     # control
    br, verdict = _verdict(["kubectl rollout restart deployment/api -n dev"])
    assert br["rollback_available"] is True and "unrecoverable" not in br
    assert verdict == "AUTO"
