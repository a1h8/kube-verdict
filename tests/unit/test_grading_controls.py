"""Per-case grading controls — docs/case-audit-001.md § Correction 1.

For every native case with a cause: a correct answer worded differently from the
keywords must PASS (positive control), and an answer that only restates the symptom
must FAIL (negative control). The symptom is the workload state as `kubectl get pods`
and the alert show it, without the messages that name the faulty object.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.veracity import cause_concepts, grade
from tools.veracity_replay import grading_expect

CASES = Path(__file__).resolve().parents[1] / "integration" / "cases"

CONTROLS: dict[str, tuple[str, str]] = {
    # case: (correct answer, symptom-only answer)
    "h001": ("payment-service cannot start because the Secret payment-db-secret it references does not exist.",
             "payment-service is in CrashLoopBackOff and keeps restarting."),
    "h002": ("The image tag deployed differs from the tag the Helm chart declares, so that image cannot be pulled.",
             "The ml-inference pods are in ImagePullBackOff."),
    "h003": ("The api memory limit was lowered out of band: the chart declares 512Mi.",
             "The api pod is OOMKilled: memory limit exceeded, CrashLoopBackOff."),
    "h004": ("The ConfigMap api-gateway-config was never created.",
             "The api-gateway pods are stuck in CreateContainerConfigError."),
    "h005": ("The service account audit-exporter-sa has no ClusterRoleBinding granting it list and watch.",
             "audit-exporter-sa is forbidden from listing pods."),
    "h006": ("A NetworkPolicy denies all egress from order-service.",
             "The order-service readiness probe times out connecting to the database."),
    "h007": ("metrics-server returns no pod metrics, so the HPA cannot compute a replica count.",
             "The HPA stays at minReplicas and does not scale under load."),
    "h008": ("The db-migrate init container fails because its migration script errors out.",
             "The backend pod is stuck in Init:CrashLoopBackOff."),
    "h009": ("The deployed liveness probe timeoutSeconds is shorter than the declared one.",
             "search-service keeps restarting after liveness probes that time out."),
    "h010": ("The staging namespace ResourceQuota is exhausted.",
             "The worker-service pod stays Pending."),
    "h012": ("The live Deployment differs from the rendered expected state, which declares 512Mi of memory.",
             "The api container is OOMKilled: memory limit 128Mi exceeded."),
    "h013": ("Calls to payments-gateway are slowed by a network problem on that path.",
             "checkout-api p99 latency breaches its SLO and burns the error budget."),
    "h014": ("The TLS certificate for payments-backend expired and cert-manager could not renew it.",
             "billing-gateway pods are Running but not Ready: the readiness probe returns 503."),
    "h015": ("etcd Range calls became slow after a compaction.",
             "The inventory-api readiness probe times out: context deadline exceeded."),
    "h016": ("Scaling orders-api to 8 replicas made the connection pools exceed postgres max_connections.",
             "Three orders-api replicas are not ready: the readiness probe returns 503."),
    "h024": ("The worker's Secret queue-credentials does not exist, and the api memory limit drifted "
             "from the declared 1Gi to 256Mi.",
             "The worker cannot start and the api is OOMKilled in a loop."),
}


def _expect(key: str) -> dict:
    (case_dir,) = CASES.glob(f"{key}_*/")
    return json.loads((case_dir / "expect.json").read_text())


def test_every_case_with_concepts_has_controls():
    with_concepts = sorted(d.name.split("_")[0] for d in CASES.glob("h0*/")
                           if cause_concepts(json.loads((d / "expect.json").read_text())))
    assert with_concepts == sorted(CONTROLS)


# Wordings found in recorded answers (docs/case-audit-001.md § Amendments A1–A3).
FOUND_CORRECT: dict[str, str] = {
    "h003": "The Helm drift between the declared and deployed memory resources is causing the pods to run out of memory.",
    "h008": "The root cause is a failed database migration for the db-migrate container in the backend pod.",
    "h013": "There is a network issue between the checkout-api and its dependencies.",
}


@pytest.mark.parametrize("key", sorted(FOUND_CORRECT))
def test_found_correct_wording_passes(key):
    result = grade(FOUND_CORRECT[key], grading_expect(_expect(key)))
    assert result["veracity"] == "PASS", result["reason"]


@pytest.mark.parametrize("key", sorted(CONTROLS))
def test_correct_answer_passes(key):
    result = grade(CONTROLS[key][0], grading_expect(_expect(key)))
    assert result["veracity"] == "PASS", result["reason"]


@pytest.mark.parametrize("key", sorted(CONTROLS))
def test_symptom_only_answer_fails(key):
    result = grade(CONTROLS[key][1], grading_expect(_expect(key)))
    assert result["veracity"] == "FAIL", f"{key}: the symptom alone passed ({result['reason']})"
