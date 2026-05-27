"""
Blast radius scoring for proposed remediation commands.

Derives scope (namespaces, cluster-scoped flag, resource count) from the
remediation list and affected resources without touching the live cluster.

Risk levels: LOW → MEDIUM → HIGH → CRITICAL
  CRITICAL is emitted when rollback_available=False → NO_GO: the operator
  must have a recovery path before any action is approved.
"""
from __future__ import annotations

import re

# Sentinel string embedded in blast_radius summary when no rollback exists.
# rollback_available=False → NO_GO: upgrade/patch without a recovery path.
_NO_GO_SUMMARY = "rollback_available=False → NO_GO: no safe recovery path"


def _parse_command_scope(cmd: str) -> dict:
    """Extract namespace, resource kind and cluster-scope flag from a kubectl/helm command."""
    cmd = cmd.strip().lstrip("$ ")
    ns_match = re.search(r'(?:^|\s)-n\s+(\S+)', cmd)
    namespace = ns_match.group(1) if ns_match else None
    cluster_scoped = any(k in cmd for k in (
        "clusterrole", "clusterrolebinding", "namespace", "node", "persistentvolume",
        "storageclass", "customresourcedefinition",
    )) and namespace is None
    kind = None
    m = re.search(r'(deployment|daemonset|statefulset|deploy|ds|sts)/(\S+)', cmd)
    if m:
        kind = m.group(1)
    elif cmd.startswith("helm upgrade") or cmd.startswith("helm rollback"):
        kind = "helm-release"
    return {"namespace": namespace, "kind": kind, "cluster_scoped": cluster_scoped}


def compute_blast_radius(
    remediation: list[str],
    affected: list[str],
    rollback_cmds: list[str],
) -> dict:
    """
    Return a blast-radius assessment dict:
      risk            — LOW | MEDIUM | HIGH | CRITICAL
      summary         — human-readable scope description
      resources       — affected resource strings
      namespaces      — sorted list of impacted namespaces
      cluster_scoped  — True if any command touches cluster-scoped resources
      command_count   — number of remediation commands
      rollback_available — False triggers CRITICAL escalation
    """
    if not remediation:
        return {
            "risk": "LOW",
            "summary": "No remediation commands.",
            "resources": [],
            "namespaces": [],
            "cluster_scoped": False,
            "command_count": 0,
            "rollback_available": True,
        }

    namespaces: set[str] = set()
    cluster_scoped = False
    for cmd in remediation:
        scope = _parse_command_scope(cmd)
        if scope["namespace"]:
            namespaces.add(scope["namespace"])
        if scope["cluster_scoped"]:
            cluster_scoped = True

    n_affected   = len(affected)
    n_namespaces = len(namespaces)

    if cluster_scoped or n_namespaces > 1 or n_affected >= 10:
        risk = "HIGH"
    elif n_namespaces == 1 and n_affected >= 3:
        risk = "MEDIUM"
    else:
        risk = "LOW"

    # rollback_available=False → NO_GO: hard CRITICAL when recovery path is absent
    rollback_available = bool(rollback_cmds)
    parts: list[str] = []
    if n_affected:
        parts.append(f"{n_affected} resource(s)")
    if namespaces:
        parts.append(f"ns: {', '.join(sorted(namespaces))}")
    if cluster_scoped:
        parts.append("cluster-scoped")

    if not rollback_available and risk != "LOW":
        risk = "CRITICAL"
        parts.append(_NO_GO_SUMMARY)

    return {
        "risk":               risk,
        "summary":            " — ".join(parts) or "Scope undetermined",
        "resources":          affected,
        "namespaces":         sorted(namespaces),
        "cluster_scoped":     cluster_scoped,
        "command_count":      len(remediation),
        "rollback_available": rollback_available,
    }
