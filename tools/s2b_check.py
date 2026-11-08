"""Automated checks on S2b scenario files, before any human review (docs/s2b-generation.md § Checks).

    python tools/s2b_check.py tests/s2b/scenarios/<batch>/scenarios.yaml

Prints one row per scenario, PASS or FAIL with the reasons. Always exits 0: the verdicts are data, the review reads
them.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

REQUIRED = ("id", "family", "question", "expected_outcome", "mechanism", "passes_if", "partial_if_only", "chain",
            "decoys", "timeline", "objects", "sources")
NO_INCIDENT_PASSES = "states that there is no incident and names no fault"

# What the product reads (brief v2, "What the tool can see"): typed kinds, plus any namespaced kind by discovery.
READ_TYPED = {"Namespace", "Node", "Pod", "Deployment", "ReplicaSet", "StatefulSet", "DaemonSet", "Service", "Ingress",
              "ConfigMap", "PersistentVolume", "PersistentVolumeClaim", "ServiceAccount", "Event", "NetworkPolicy",
              "MutatingWebhookConfiguration"}
READ_DISCOVERED = {"Job", "CronJob", "ResourceQuota", "LimitRange", "HorizontalPodAutoscaler", "PodDisruptionBudget",
                   "Role", "RoleBinding", "Certificate", "Issuer", "CertificateRequest", "Application", "ApplicationSet",
                   "Kustomization", "GitRepository", "HelmRepository", "OCIRepository", "ServiceMonitor",
                   "PodMonitor", "PrometheusRule", "Lease", "ControllerRevision", "PolicyReport"}
NOT_READ = {"Secret": "Secrets are never collected",
            "ValidatingWebhookConfiguration": "no ValidatingWebhookConfiguration is read",
            "HelmRelease": "a Helm release is the release itself, never the Flux HelmRelease CRD",
            "Endpoints": "Endpoints are not read", "EndpointSlice": "EndpointSlices are not read",
            "APIService": "cluster-scoped, not seen under a namespace filter",
            "StorageClass": "cluster-scoped, not seen under a namespace filter",
            "ClusterIssuer": "cluster-scoped, not seen under a namespace filter",
            "ClusterRole": "cluster-scoped, not seen under a namespace filter",
            "ClusterRoleBinding": "cluster-scoped, not seen under a namespace filter",
            "PriorityClass": "cluster-scoped, not seen under a namespace filter",
            "CustomResourceDefinition": "cluster-scoped, not seen under a namespace filter"}

_REF = re.compile(r"\b([A-Z][A-Za-z]+)/([a-z0-9][a-z0-9.\-]*)(?:/([a-z0-9][a-z0-9.\-]*))?")
_TIME = re.compile(r"T0(?:\s*[+\-−]\s*\d+\s*(?:min|d|h|m|s)(?:\s*\d+\s*(?:min|m|s))?)?")
_NAMES_A_CAUSE = re.compile(r"\b(caused by|due to|because of)\b", re.I)


def refs(text: str) -> list[tuple[str, str | None, str]]:
    """``Kind/ns/name`` or ``Kind/name`` references in a text: (kind, namespace or None, name)."""
    out = []
    for m in _REF.finditer(str(text or "")):
        kind, a, b = m.group(1), m.group(2), m.group(3)
        out.append((kind, a, b) if b else (kind, None, a))
    return out


def _times(text: str) -> set[str]:
    return {re.sub(r"\s+", "", t).replace("min", "m").replace("−", "-") for t in _TIME.findall(str(text or ""))}


def _kind_reason(kind: str) -> str | None:
    if kind in NOT_READ:
        return f"{kind}: {NOT_READ[kind]}"
    if kind not in READ_TYPED | READ_DISCOVERED:
        return f"{kind}: not a kind the product reads"
    return None


def _hops(chain: list) -> list[dict]:
    return [h for h in chain or [] if isinstance(h, dict)]


def _real_hop(hop: dict) -> bool:
    src, dst = refs(hop.get("from", "")), refs(hop.get("to", ""))
    if not src or not dst:
        return False
    (sk, sns, _sn), (dk, _dns, dn) = src[0], dst[0]
    if dk == "Namespace" and sns is not None and dn == sns:
        return False                                  # object -> its own Namespace
    if sk == "Deployment" and dk == "ReplicaSet":
        return False                                  # Deployment -> its ReplicaSet
    return True


def signature(s: dict) -> tuple:
    cause = refs(s.get("cause_object", ""))
    kinds = tuple((refs(h.get("from", ""))[:1] or [("?",)])[0][0] + ">" + (refs(h.get("to", ""))[:1] or [("?",)])[0][0]
                  for h in _hops(s.get("chain")))
    return (cause[0][0] if cause else "", kinds)


def check(s: dict) -> list[str]:
    """Reasons a scenario fails; empty when it passes."""
    why: list[str] = []
    missing = [f for f in REQUIRED if f not in s]
    if s.get("expected_outcome") == "cause" and "cause_object" not in s:
        missing.append("cause_object")
    if missing:
        why.append("missing fields: " + ", ".join(missing))
    partial = [str(p) for p in (s.get("partial_if_only") or [])]
    cause = str(s.get("cause_object", "") or "")
    passes_refs = {f"{k}/{ns}/{n}" if ns else f"{k}/{n}" for k, ns, n in refs(s.get("passes_if", ""))}
    if cause and any(cause.lower() in p.lower() for p in partial):
        why.append("A1: partial_if_only contains the cause object")
    elif any(r.lower() in p.lower() for r in passes_refs for p in partial):
        why.append("A1: partial_if_only contains what passes_if accepts")
    outcome = s.get("expected_outcome")
    if outcome == "no_incident":
        if str(s.get("passes_if", "")).strip().rstrip(".") != NO_INCIDENT_PASSES:
            why.append("A2: passes_if must be exactly '" + NO_INCIDENT_PASSES + "'")
        if s.get("chain"):
            why.append("A2: a scenario with no incident has an empty chain")
        if _NAMES_A_CAUSE.search(str(s.get("mechanism", ""))):
            why.append("A2: the mechanism names a cause")
    elif outcome == "cause":
        real = sum(_real_hop(h) for h in _hops(s.get("chain")))
        if real < 2:
            why.append(f"A3: {real} real hop(s), at least 2 needed")
    else:
        why.append(f"expected_outcome {outcome!r} is neither cause nor no_incident")
    named = refs(cause) + [r for h in _hops(s.get("chain")) for r in refs(h.get("from", "")) + refs(h.get("to", ""))]
    named += [r for d in s.get("decoys") or [] if isinstance(d, dict) for r in refs(d.get("object", ""))]
    for kind in sorted({k for k, _ns, _n in named}):
        reason = _kind_reason(kind)
        if reason:
            why.append("kinds: " + reason)
    timeline = set().union(*[_times(t) for t in s.get("timeline") or []]) if s.get("timeline") else set()
    used = set().union(*[_times(h.get("abnormal", "")) for h in _hops(s.get("chain"))] or [set()])
    used |= set().union(*[_times(d.get("why_not", "")) | _times(d.get("looks_like", ""))
                          for d in s.get("decoys") or [] if isinstance(d, dict)] or [set()])
    absent = sorted(t for t in used if t not in timeline and t != "T0")
    if absent:
        why.append("A10: times not in the timeline: " + ", ".join(absent))
    if len([d for d in s.get("decoys") or [] if isinstance(d, dict)]) < 2:
        why.append("fewer than 2 decoys")
    objects = s.get("objects") or []
    objects = objects if isinstance(objects, list) else []
    namespaces = {ns for o in objects for _k, ns, _n in refs(o)[:1] if ns}
    if len(objects) < 25 or len(namespaces) < 2:
        why.append(f"A9: {len(objects)} objects over {len(namespaces)} namespace(s), at least 25 over 2 needed")
    return why


def load(path: Path) -> tuple[list[dict], list[str]]:
    """The scenarios of a file, and the errors of the file itself (YAML, ids)."""
    errors: list[str] = []
    try:
        docs = [d for d in yaml.safe_load_all(path.read_text()) if isinstance(d, dict)]
    except yaml.YAMLError as exc:
        return [], [f"invalid YAML: {exc}"]
    ids = [str(d.get("id")) for d in docs]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        errors.append("duplicate ids: " + ", ".join(dup))
    return docs, errors


def check_file(path: Path) -> tuple[list[tuple[str, list[str]]], list[str]]:
    docs, errors = load(path)
    rows = [(str(d.get("id")), check(d)) for d in docs]
    seen: dict[tuple, str] = {}
    for (sid, why), d in zip(rows, docs):
        if d.get("expected_outcome") != "cause":
            continue
        sig = signature(d)
        if sig in seen:
            why.append(f"duplicate chain signature with {seen[sig]}")
        else:
            seen[sig] = sid
    return rows, errors


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 0
    rows, errors = check_file(Path(argv[0]))
    for e in errors:
        print(f"FILE: {e}")
    print("| scenario | verdict | reasons |")
    print("|---|---|---|")
    for sid, why in rows:
        print(f"| {sid} | {'FAIL' if why else 'PASS'} | {'; '.join(why)} |")
    passed = sum(1 for _s, w in rows if not w)
    print(f"{passed}/{len(rows)} pass")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
