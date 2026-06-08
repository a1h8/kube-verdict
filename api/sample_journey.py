"""
A recorded sample investigation, so the Decision Journey UI can be demoed
without a live cluster or Ollama. It mirrors the shape produced by a real run
(edge_log, reasoning_history, verdict, blast_radius, report_dict) — purely
illustrative data, clearly labelled as a sample.
"""
from __future__ import annotations

from typing import Any


def sample_state() -> dict[str, Any]:
    """A realistic completed investigation: two eliminated hypothesis paths
    (one early-switch, one retries-exhausted), then a HUMAN_REVIEW verdict."""
    return {
        "query": "[SAMPLE] payment-api pods crashlooping in production",
        "kube_version": "v1.28.3+k3s1",
        "confidence": "HIGH",
        "verdict": "HUMAN_REVIEW",
        "verdict_reasons": [
            "namespace 'production' is production — always HUMAN_REVIEW minimum",
            "blast radius MEDIUM — review before applying",
        ],
        "current_hypothesis": "PVC payment-data is Pending — no PersistentVolume matches storageClass 'standard'",
        "blast_radius": {
            "risk": "MEDIUM",
            "summary": "1 namespace, 2 resources, rollback available",
            "resources": ["PersistentVolumeClaim/payment-data", "Deployment/payment-api"],
            "namespaces": ["production"],
            "cluster_scoped": False,
            "command_count": 2,
            "rollback_available": True,
        },
        "reasoning_history": [
            {
                "step": 1,
                "hypothesis": "OOMKilled — memory limit too low on payment-api",
                "confidence": "LOW",
                "retry_count": 1,
                "summary": "No OOM events and metrics show memory well under limit — probability declining, switched.",
            },
            {
                "step": 2,
                "hypothesis": "ImagePullBackOff — registry auth drift",
                "confidence": "LOW",
                "retry_count": 2,
                "summary": "Image pulls succeed; events show FailedMount, not pull errors — retries exhausted, switched.",
            },
        ],
        "edge_log": [
            {
                "router": "confidence", "edge_taken": "retry",
                "reason": "confidence=LOW — retrying (1/2) on OOM hypothesis",
                "snapshot": {"confidence": "LOW", "retry_count": 1, "max_retries": 2,
                             "candidates_remaining": 2, "declining": True,
                             "path_conf_history": ["LOW"]},
                "ts": "2026-06-08T09:00:01+00:00",
            },
            {
                "router": "confidence", "edge_taken": "next_path",
                "reason": "probability declining (LOW×2) — early switch to next hypothesis",
                "snapshot": {"confidence": "LOW", "retry_count": 2, "max_retries": 2,
                             "candidates_remaining": 1, "declining": True,
                             "path_conf_history": ["LOW", "LOW"]},
                "ts": "2026-06-08T09:00:04+00:00",
            },
            {
                "router": "policy", "edge_taken": "review",
                "reason": "HUMAN_REVIEW: namespace 'production' is production — always HUMAN_REVIEW minimum",
                "snapshot": {"confidence": "HIGH", "score": 0.85, "risk": "MEDIUM",
                             "rollback_available": True, "namespace": "production",
                             "verdict": "HUMAN_REVIEW"},
                "ts": "2026-06-08T09:00:09+00:00",
            },
        ],
        "report_dict": {
            "summary": "payment-api is CrashLoopBackOff because PVC payment-data is unbound.",
            "root_cause": "No PersistentVolume matches storageClass 'standard' for the 10Gi payment-data PVC, so the pod cannot mount its volume.",
            "confidence": "HIGH",
            "causal_chain": [
                "PVC payment-data is Pending",
                "pod cannot mount volume",
                "container fails to start → CrashLoopBackOff",
            ],
            "remediation": [
                "kubectl describe pvc payment-data -n production",
                "kubectl apply -f pv-standard-10gi.yaml",
            ],
            "rollback": ["kubectl delete -f pv-standard-10gi.yaml"],
            "events": ["Warning FailedMount pod/payment-api-xyz: Unable to attach or mount volumes: payment-data"],
            "alerts": ["FIRING KubePodCrashLooping: payment-api severity=critical"],
            "policy_violations": ["FAIL require-limits: container payment-api has no memory limit"],
        },
        "paths_explored": 3,
    }


def sample_review_payload() -> dict[str, Any]:
    s = sample_state()["report_dict"]
    return {
        "summary": s["summary"],
        "root_cause": s["root_cause"],
        "remediation": s["remediation"],
        "confidence": "HIGH",
        "no_solution": False,
    }
