"""The dry-run only runs what kubectl can simulate server-side — docs/fix-plan-001.md, H15."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from workflow.nodes import _exec_dry_run

NOT_RUN = [
    'kubectl exec -n prod db-0 -- sh -c "echo ran"',      # exec has no dry-run
    "kubectl exec db-0 -n prod -it -- psql",
    "kubectl cp prod/db-0:/data ./data",
    "kubectl port-forward svc/api 8080:80 -n prod",
    "kubectl edit deployment api -n prod",
    "kubectl debug -it db-0 --image=busybox -n prod",
    "kubectl rollout restart deployment/api -n prod",     # no --dry-run in kubectl rollout restart
    "kubectl apply --dry-run=none -f fix.yaml",           # an explicit real run
    "kubectl apply --dry-run=client -f fix.yaml",
    "kubectl run tmp --image=busybox -n dev -- sh -c id",  # arguments for another command
    "kubectl --context other-cluster delete pod x -n dev",
    "kubectl delete pod x -n dev --kubeconfig=/tmp/other",
    "kubectl delete pod x -n dev --as=system:admin",
    "kubectl some-plugin do-something",
]

RUN = [
    ("kubectl delete pod x -n dev", ["kubectl", "delete", "pod", "x", "-n", "dev", "--dry-run=server"]),
    ("kubectl -n dev scale deployment api --replicas=2",
     ["kubectl", "-n", "dev", "scale", "deployment", "api", "--replicas=2", "--dry-run=server"]),
    ("kubectl rollout undo deployment/api -n dev",
     ["kubectl", "rollout", "undo", "deployment/api", "-n", "dev", "--dry-run=server"]),
    ("kubectl set image deployment/api api=api:1.2 -n dev",
     ["kubectl", "set", "image", "deployment/api", "api=api:1.2", "-n", "dev", "--dry-run=server"]),
    ("kubectl apply -f fix.yaml --dry-run=server",
     ["kubectl", "apply", "-f", "fix.yaml", "--dry-run=server"]),     # not doubled
]


@pytest.mark.parametrize("cmd", NOT_RUN)
def test_a_command_without_a_real_dry_run_is_never_run(cmd):
    with patch("subprocess.run") as run:
        _, out, rc = _exec_dry_run(cmd)
    run.assert_not_called()
    assert "dry-run not run" in out and "review manually" in out
    assert rc == 0


@pytest.mark.parametrize("cmd,argv", RUN)
def test_a_command_kubectl_can_simulate_is_run_server_side(cmd, argv):
    # control: the refusals above are not a dry-run that never runs anything
    with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="ok", stderr="")) as run:
        dry_cmd, out, rc = _exec_dry_run(cmd)
    assert run.call_args.args[0] == argv
    assert out == "ok" and rc == 0
