"""
Blast radius scoring for proposed remediation.

Two methods, both returning the same dict shape (with a ``method`` field):

  * ``command-heuristic`` — ``compute_blast_radius`` derives scope (namespaces,
    cluster-scope, resource count) by parsing the remediation command strings,
    without touching the cluster. A fast triage signal.

  * ``rendered-diff`` — ``compute_blast_radius_from_diff`` /
    ``render_diff_blast_radius`` classify the **actual changed objects** from a
    rendered-vs-live manifest diff (``ManifestRenderer`` + ``ManifestDiffer``):
    what would really change if the remediation were applied.

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
            "method": "command-heuristic",
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
        "method":             "command-heuristic",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Rendered-vs-live diff blast radius (the real impact, not a command heuristic)
# ─────────────────────────────────────────────────────────────────────────────

# field_path prefixes that indicate a cluster-scoped object would change.
_CLUSTER_SCOPED_KINDS = (
    "ClusterRole", "ClusterRoleBinding", "Namespace", "Node",
    "PersistentVolume", "StorageClass", "CustomResourceDefinition",
)


def compute_blast_radius_from_diff(
    drifts: list,
    rollback_cmds: list[str],
    namespaces: list[str] | None = None,
) -> dict:
    """Classify the *actual* blast radius from a rendered-vs-live manifest diff.

    Each ``drift`` is a ``DriftItem``-like object (duck-typed: ``field_path`` +
    ``severity`` in {critical, warning, info}) describing one object/field that
    would change if the remediation were applied. Risk is derived from the real
    changed set — not from parsing command strings.
    """
    if not drifts:
        return {
            "risk": "LOW",
            "summary": "No rendered changes vs live state.",
            "resources": [],
            "namespaces": list(namespaces or []),
            "cluster_scoped": False,
            "command_count": 0,
            "rollback_available": bool(rollback_cmds),
            "method": "rendered-diff",
            "changed": 0,
            "by_severity": {"critical": 0, "warning": 0, "info": 0},
        }

    by_severity = {"critical": 0, "warning": 0, "info": 0}
    resources: list[str] = []
    ns: set[str] = set(namespaces or [])
    cluster_scoped = False
    for d in drifts:
        fp = str(getattr(d, "field_path", ""))
        sev = str(getattr(d, "severity", "info"))
        by_severity[sev] = by_severity.get(sev, 0) + 1
        resources.append(fp)
        if any(k in fp for k in _CLUSTER_SCOPED_KINDS):
            cluster_scoped = True

    n_changed = len(resources)
    if by_severity["critical"] or cluster_scoped or len(ns) > 1 or n_changed >= 10:
        risk = "HIGH"
    elif by_severity["warning"] or n_changed >= 3:
        risk = "MEDIUM"
    else:
        risk = "LOW"

    rollback_available = bool(rollback_cmds)
    summary = (
        f"{n_changed} object(s) change "
        f"({by_severity['critical']} critical, {by_severity['warning']} warning)"
    )
    if cluster_scoped:
        summary += " · cluster-scoped"
    if not rollback_available and risk != "LOW":
        risk = "CRITICAL"
        summary += f" · {_NO_GO_SUMMARY}"

    return {
        "risk": risk,
        "summary": summary,
        "resources": sorted(set(resources)),
        "namespaces": sorted(ns),
        "cluster_scoped": cluster_scoped,
        "command_count": 0,
        "rollback_available": rollback_available,
        "method": "rendered-diff",
        "changed": n_changed,
        "by_severity": by_severity,
    }


def render_diff_blast_radius(
    *,
    chart: str,
    release_name: str,
    namespace: str,
    values: dict,
    graph,
    rollback_cmds: list[str],
    renderer=None,
    differ=None,
) -> dict | None:
    """End-to-end real blast radius: render the proposed change, diff it against
    the live cluster graph, and classify the changed objects.

    Returns ``None`` when rendering fails (no ``helm`` / bad chart) so the caller
    can fall back to ``compute_blast_radius`` (the command heuristic).
    """
    from ingestion.manifest_differ import ManifestDiffer
    from ingestion.manifest_renderer import ManifestRenderer

    renderer = renderer or ManifestRenderer()
    differ = differ or ManifestDiffer()

    rendered = renderer.render(chart, release_name, namespace, values=values)
    if not rendered:
        return None
    drifts = differ.diff(rendered, graph)
    return compute_blast_radius_from_diff(drifts, rollback_cmds, namespaces=[namespace])
