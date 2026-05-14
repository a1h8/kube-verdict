"""
Rule-based proposal generator.

Given an RCAReport, produces a ranked list of realistic follow-up user queries
that a SRE would ask to deepen the investigation or validate remediation.
No LLM call — all decisions are keyword-driven on the report text.
"""
from __future__ import annotations

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
    """Return up to max_n ranked proposals derived from the report content.

    Signals are sourced from two tiers:
      ctx_text — ContextWindow structured data (seeds, events, drift, anchors).
                 Factual cluster state; used for high-confidence signal matching.
      llm_text — full LLM response text. May contain hallucinations; used only
                 when the ctx_text ALSO contains a corroborating keyword, except
                 for categories where the LLM analysis is the primary signal.
    """
    llm_text = _full_text(report)
    ctx_text = _context_text(report)

    # Combined text used for generic keyword checks (both sources).
    combined = ctx_text + " " + llm_text

    candidates: list[tuple[int, str, str, str]] = []  # (priority, category, desc, query)

    # ── Helm drift: require signal in ctx (drift annotations are factual) ──────
    if _has(ctx_text, ["drift", "declared", "observed"]):
        candidates.append((10, "drift",
            "Full Helm drift detail",
            "Show me the complete helm drift with all changed fields and their previous values."))
        candidates.append((9, "drift",
            "When did drift start?",
            "When did this configuration drift occur and which helm release introduced it?"))

    # ── Helm upgrade: require ctx drift OR LLM + ctx helm mention ─────────────
    if _has(ctx_text, ["drift", "helm"]) and _has(llm_text, ["helm upgrade"]):
        candidates.append((20, "remediation",
            "Exact helm command",
            "Give me the exact helm upgrade command with namespace and all --set parameters I need to run."))
        candidates.append((8, "remediation",
            "Rollback option",
            "Can I rollback the helm release to fix this instead of upgrading? What is the command?"))

    # ── OOM / memory: require ctx confirmation (OOMKilled in events/seeds) ────
    if _has(ctx_text, ["oomkilled", "memory"]):
        candidates.append((15, "memory",
            "Recommended memory limits",
            "What memory limits and requests do you recommend for this workload to avoid OOMKilled?"))
        candidates.append((7, "memory",
            "Monitor memory usage",
            "How do I monitor real-time memory usage to confirm usage is below the new limit?"))

    # ── Image pull: require ctx confirmation (event reason or message) ────────
    if _has(ctx_text, ["imagepullbackoff", "errimagepull", "pull"]):
        candidates.append((15, "image",
            "Image pull diagnosis",
            "What is the exact image name and tag that cannot be pulled, and how do I check registry credentials?"))
        candidates.append((7, "image",
            "Secret and credentials check",
            "How do I verify that the imagePullSecret exists and is correctly referenced by the pod?"))

    # ── PVC / storage: ctx-driven ─────────────────────────────────────────────
    if _has(ctx_text, ["pvc", "persistentvolumeclaim", "storageclass"]):
        candidates.append((15, "storage",
            "PVC binding status",
            "How do I check which PersistentVolumes are available and why the PVC cannot bind?"))
        candidates.append((7, "storage",
            "Compatible storage class",
            "Which storage class should I use and how do I patch the PVC or recreate it?"))

    # ── Ingress / network: combined ───────────────────────────────────────────
    if _has(combined, ["503", "ingress", "service not found", "backend", "nginx", "traefik"]):
        candidates.append((15, "network",
            "Ingress backend check",
            "What kubectl command shows me which service the Ingress currently resolves to?"))

    if _has(combined, ["networkpolicy", "network policy", "deny"]):
        candidates.append((14, "network",
            "Network policy diagnosis",
            "How do I test connectivity between pods and identify which network policy is blocking traffic?"))

    # ── RBAC: combined ────────────────────────────────────────────────────────
    if _has(combined, ["rbac", "forbidden", "clusterrole", "serviceaccount"]):
        candidates.append((15, "rbac",
            "RBAC permission check",
            "What kubectl command shows me the exact permissions missing for this service account?"))

    # ── DNS: combined ─────────────────────────────────────────────────────────
    if _has(combined, ["dns resolution", "nslookup", "coredns", "i/o timeout"]):
        candidates.append((15, "dns",
            "DNS resolution test",
            "How do I run an nslookup or dig from inside the cluster to test DNS resolution?"))

    # ── Secret / ConfigMap: ctx seeds/events OR LLM root_cause ───────────────
    # Use ctx_text OR the structured root_cause (not raw LLM hallucinations)
    rc_text = (report.root_cause or "").lower()
    if _has(ctx_text, ["secret", "configmap", "not found"]) or \
       _has(rc_text, ["secret", "configmap", "missing"]):
        candidates.append((18, "config",
            "Secret / ConfigMap key check",
            "How do I verify that the required key exists in the secret or configmap and is correctly referenced?"))
        candidates.append((16, "config",
            "Create missing secret",
            "What is the exact kubectl command to create the missing secret with the correct keys?"))

    # ── Quota: combined ───────────────────────────────────────────────────────
    if _has(combined, ["quota", "limitrange", "exceeds", "exceeded"]):
        candidates.append((14, "quota",
            "Quota usage breakdown",
            "How do I see total resource quota usage for this namespace vs the defined limits?"))

    # ── CrashLoop / restarts: ctx-driven ─────────────────────────────────────
    if _has(ctx_text, ["crashloopbackoff", "backoff", "restart"]):
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


def _context_text(report: RCAReport) -> str:
    """Factual cluster signals from the ContextWindow — not LLM-generated."""
    ctx = report.context
    if ctx is None:
        return ""
    parts = (
        ctx.seeds
        + ctx.events
        + ctx.drift
        + ctx.anchors
        + ctx.policy_violations
    )
    return " ".join(parts).lower()


def _has(text: str, keywords: list[str]) -> bool:
    return any(kw.lower() in text for kw in keywords)
