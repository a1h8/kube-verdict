"""
Rule-based proposal generator.

Given an RCAReport, produces a ranked list of realistic follow-up user queries
that a SRE would ask to deepen the investigation or validate remediation.
No LLM call — all decisions are keyword-driven on the report text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from rca.analyzer import RCAReport


@dataclass(frozen=True)
class Proposal:
    label: str          # "A", "B", "C" — slot in the tree
    category: str       # "drift" | "remediation" | "memory" | "network" | "storage" | "generic"
    description: str    # short label shown in the ASCII tree
    follow_up_query: str


_LABELS = list("ABCDEFGH")


def generate_proposals(report: RCAReport, max_n: int = 3) -> list[Proposal]:
    """Return up to max_n ranked proposals derived from the report content."""
    text = _full_text(report)
    candidates: list[tuple[int, str, str, str]] = []  # (priority, category, desc, query)

    if _has(text, ["helm", "drift", "declared", "observed"]):
        candidates.append((10, "drift",
            "Full Helm drift detail",
            "Show me the complete helm drift with all changed fields and their previous values."))
        candidates.append((9, "drift",
            "When did drift start?",
            "When did this configuration drift occur and which helm release introduced it?"))

    if _has(text, ["helm upgrade", "helm upgrade --set"]):
        candidates.append((20, "remediation",
            "Exact helm command",
            "Give me the exact helm upgrade command with namespace and all --set parameters I need to run."))
        candidates.append((8, "remediation",
            "Rollback option",
            "Can I rollback the helm release to fix this instead of upgrading? What is the command?"))

    if _has(text, ["oom", "memory", "limit", "mi", "gb"]):
        candidates.append((15, "memory",
            "Recommended memory limits",
            "What memory limits and requests do you recommend for this workload to avoid OOMKilled?"))
        candidates.append((7, "memory",
            "Monitor memory usage",
            "How do I monitor real-time memory usage to confirm usage is below the new limit?"))

    if _has(text, ["imagepull", "image", "registry", "pull", "ecr", "gcr", "docker"]):
        candidates.append((15, "image",
            "Image pull diagnosis",
            "What is the exact image name and tag that cannot be pulled, and how do I check registry credentials?"))
        candidates.append((7, "image",
            "Secret and credentials check",
            "How do I verify that the imagePullSecret exists and is correctly referenced by the pod?"))

    if _has(text, ["pvc", "persistentvolumeclaim", "storageclass", "volume", "bound"]):
        candidates.append((15, "storage",
            "PVC binding status",
            "How do I check which PersistentVolumes are available and why the PVC cannot bind?"))
        candidates.append((7, "storage",
            "Compatible storage class",
            "Which storage class should I use and how do I patch the PVC or recreate it?"))

    if _has(text, ["503", "ingress", "service not found", "backend", "nginx", "traefik"]):
        candidates.append((15, "network",
            "Ingress backend check",
            "What kubectl command shows me which service the Ingress currently resolves to?"))

    if _has(text, ["networkpolicy", "network policy", "deny", "port", "egress", "ingress"]):
        candidates.append((14, "network",
            "Network policy diagnosis",
            "How do I test connectivity between pods and identify which network policy is blocking traffic?"))

    if _has(text, ["rbac", "forbidden", "unauthorized", "clusterrole", "serviceaccount"]):
        candidates.append((15, "rbac",
            "RBAC permission check",
            "What kubectl command shows me the exact permissions missing for this service account?"))

    if _has(text, ["dns", "resolv", "nslookup", "coredns", "lookup"]):
        candidates.append((15, "dns",
            "DNS resolution test",
            "How do I run an nslookup or dig from inside the cluster to test DNS resolution?"))

    if _has(text, ["secret", "configmap", "key", "envfrom", "env"]):
        candidates.append((14, "config",
            "Secret / ConfigMap key check",
            "How do I verify that the required key exists in the secret or configmap and is correctly referenced?"))

    if _has(text, ["quota", "limitrange", "exceeds", "exceeded"]):
        candidates.append((14, "quota",
            "Quota usage breakdown",
            "How do I see total resource quota usage for this namespace vs the defined limits?"))

    if _has(text, ["crashloop", "restart", "backoff", "exitcode"]):
        candidates.append((12, "generic",
            "Container logs",
            "How do I get the last 100 lines of logs from the crashing container including previous restarts?"))

    # Generic proposals always available
    candidates.append((5, "generic",
        "Kubectl diagnostic commands",
        "What kubectl commands should I run right now to confirm this diagnosis step by step?"))
    candidates.append((4, "generic",
        "Risk of data loss",
        "Is there any risk of data loss or service interruption when applying the suggested remediation?"))
    candidates.append((3, "generic",
        "Prevention strategy",
        "How can I prevent this failure from recurring — are there alerts or policies I should add?"))

    candidates.sort(key=lambda x: x[0], reverse=True)
    seen_categories: set[str] = set()
    proposals: list[Proposal] = []

    for priority, category, desc, query in candidates:
        if len(proposals) >= max_n:
            break
        if category in seen_categories and category != "generic":
            continue
        seen_categories.add(category)
        proposals.append(Proposal(
            label=_LABELS[len(proposals)],
            category=category,
            description=desc,
            follow_up_query=query,
        ))

    return proposals


def _full_text(report: RCAReport) -> str:
    parts = [
        report.raw_analysis,
        report.root_cause,
        " ".join(report.affected),
        " ".join(report.remediation),
        report.confidence,
    ]
    return " ".join(p for p in parts if p).lower()


def _has(text: str, keywords: list[str]) -> bool:
    return any(kw.lower() in text for kw in keywords)
