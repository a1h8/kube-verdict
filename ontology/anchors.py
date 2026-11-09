"""The anchor engine (docs/anchors-and-relations.md, step 3): is an object the correlation walk reaches abnormal?

The knowledge is data: ``ontology/spec/anchors.yaml`` (the declared semantic anchors: ``bound``, ``fits``, ``fresh``)
and ``ontology/spec/conditions.yaml`` (the documented polarity of conditions). kstatus applies to any object. This
module holds one evaluator per anchor type and the conventions; it names no kind.
"""
from __future__ import annotations

import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from ontology.entities import K8sEntity, ResourceKind
from ontology.graph import OntologyGraph
from ontology.relationships import RelationshipType as R

SPEC_DIR = Path(__file__).resolve().parent / "spec"


def _kind(e: K8sEntity) -> str:
    return str(getattr(e.kind, "value", e.kind))


def _fqn(e: K8sEntity) -> str:
    return f"{_kind(e)}/{e.namespace}/{e.name}" if e.namespace else f"{_kind(e)}/{e.name}"


def _get(d: Any, *keys: str) -> Any:
    for k in keys:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def _any(d: Any, *names: str) -> Any:
    """The first present of several spellings (client ``to_dict`` snake_case, API JSON camelCase)."""
    for n in names:
        if isinstance(d, dict) and d.get(n) is not None:
            return d[n]
    return None


_UNITS = {"Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40, "k": 1e3, "M": 1e6, "G": 1e9, "T": 1e12, "m": 1e-3}


def quantity(v: Any) -> float:
    """A Kubernetes quantity ("512Mi", "250m", "2", 40) as a number."""
    s = str(v).strip()
    m = re.fullmatch(r"([0-9.]+)([A-Za-z]*)", s)
    if not m:
        return 0.0
    return float(m[1]) * _UNITS.get(m[2], 1.0)


def _time(v: Any) -> datetime | None:
    if isinstance(v, datetime):
        return v
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")) if v else None
    except ValueError:
        return None


def _statuses(pod: K8sEntity) -> list[dict]:
    return _any(_get(pod.raw, "status") or {}, "container_statuses", "containerStatuses") or []


def oom_killed(pod: K8sEntity) -> bool:
    for cs in _statuses(pod):
        for state in (_any(cs, "last_state", "lastState"), cs.get("state")):
            if (_get(state, "terminated", "reason") or "") == "OOMKilled":
                return True
    return False


def started_at(pod: K8sEntity) -> datetime | None:
    """When the pod started (``status.startTime``, else its ``creationTimestamp``): an environment value from a ConfigMap is read when the pod is
    created and never updated in it (Kubernetes documentation, ConfigMaps). Falls back on a running container's
    start; a crash-looping container has none."""
    t = (_time(_any(_get(pod.raw, "status") or {}, "start_time", "startTime"))
         or _time(_any(_get(pod.raw, "metadata") or {}, "creation_timestamp", "creationTimestamp"))
         or getattr(pod, "created_at", None))
    if t:
        return t
    times = [_time(_any(_get(cs, "state", "running") or {}, "started_at", "startedAt")) for cs in _statuses(pod)]
    times = [t for t in times if t]
    return max(times) if times else None


def last_write(e: K8sEntity) -> datetime | None:
    meta = _get(e.raw, "metadata") or {}
    times = [_time(f.get("time")) for f in (_any(meta, "managed_fields", "managedFields") or [])]
    times = [t for t in times if t]
    return max(times) if times else None


def _root_owner(graph: OntologyGraph, e: K8sEntity) -> K8sEntity:
    seen = {e.uid}
    while True:
        up = next((graph.get(x.source_uid) for x in graph._radj.get(e.uid, []) if x.rel_type == R.OWNS), None)
        if up is None or up.uid in seen:
            return e
        seen.add(up.uid)
        e = up


def _pod_amount(pod: K8sEntity, resource: str) -> float:
    if resource in ("pods", "count/pods"):
        return 1.0
    scope, _, name = resource.rpartition(".")
    field_name = "limits" if scope == "limits" else "requests"
    return sum(quantity(_get(c, "resources", field_name, name) or 0) for c in _get(pod.raw, "spec", "containers") or [])


def quota_full(graph: OntologyGraph, quota: K8sEntity) -> tuple[str, K8sEntity] | None:
    """A quota at its hard limit for a resource, and the root owner whose non-terminal pods hold most of it."""
    hard = _get(quota.raw, "status", "hard") or _get(quota.raw, "spec", "hard") or {}
    used = _get(quota.raw, "status", "used") or {}
    full = [r for r in hard if r in used and quantity(used[r]) >= quantity(hard[r]) > 0]
    if not full:
        return None
    res = full[0]
    shares: dict[str, float] = {}
    owners: dict[str, K8sEntity] = {}
    for pod in graph.entities(ResourceKind.POD):
        if pod.namespace != quota.namespace or getattr(pod, "phase", "") in ("Succeeded", "Failed"):
            continue
        root = _root_owner(graph, pod)
        shares[root.uid] = shares.get(root.uid, 0.0) + _pod_amount(pod, res)
        owners[root.uid] = root
    total = sum(shares.values())
    if not total:
        return None
    top = max(shares, key=lambda u: shares[u])
    share = shares[top] / total
    return (f"{res} at its hard limit ({used[res]}/{hard[res]}); {_fqn(owners[top])} holds {share:.0%} of what its "
            f"pods use", owners[top])



@lru_cache(maxsize=1)
def _declared() -> tuple[list[dict], list[dict]]:
    from ontology.spec_loader import load
    anchors = [{"id": a.id, "kind": a.kind, "type": a.type, "params": a.params} for a in load()[1]]
    cond = yaml.safe_load((SPEC_DIR / "conditions.yaml").read_text()) or {}
    return anchors, list(cond.get("conditions") or [])


def _conditions(e: K8sEntity) -> list[dict]:
    return list(_get(e.raw or {}, "status", "conditions") or getattr(e, "conditions", None) or [])


def _fits(graph, node, anchor, edge, came_from):
    if (edge.rel_type == R.LIMITS and str((edge.metadata or {}).get("method", "")).startswith("default injected")
            and _kind(came_from) == "Pod" and oom_killed(came_from)):
        return f"its default limit was injected and the container is OOMKilled: {edge.metadata['evidence']}", node
    return None


def _fresh(graph, node, anchor, edge, came_from):
    if edge.rel_type.name != anchor["params"].get("through") or _kind(came_from) != "Pod":
        return None
    written, started = last_write(node), started_at(came_from)
    if written and started and written < started:
        return (f"last written {written.isoformat()}, before {came_from.name} started ({started.isoformat()}); "
                f"read through the environment at start ({(edge.metadata or {}).get('method', '')})", node)
    return None


def _bound(graph, node, anchor, edge, came_from):
    if anchor["params"].get("aggregate"):
        return quota_full(graph, node)
    return None


EVALUATORS = {"bound": _bound, "fits": _fits, "fresh": _fresh}


def _by_conventions(node: K8sEntity, edge) -> tuple[str, K8sEntity] | None:
    """kstatus (Stalled=True is a fault, on any object that reports it: the kstatus README computes status "solely based
    on the data in the resource"), documented polarities, and an admission refusal by the reached object."""
    anchors, polarities = _declared()
    conds = {str(c.get("type")): c for c in _conditions(node)}
    if str((conds.get("Stalled") or {}).get("status")) == "True":
        c = conds["Stalled"]
        return f"Stalled (kstatus): {c.get('reason', '')}: {c.get('message', '')}", node
    for p in polarities:
        if p["kind"] != _kind(node) or p["type"] not in conds:
            continue
        c = conds[p["type"]]
        if (p.get("abnormal_reason") and c.get("reason") == p["abnormal_reason"]) or (
                "normal" in p and str(c.get("status")) != str(p["normal"])):
            return f"condition {p['type']}={c.get('status')} ({c.get('reason', '')}): {c.get('message', '')}", node
    method = str((edge.metadata or {}).get("method", ""))
    if method.startswith("admission refusal") and edge.rel_type != R.QUOTA_BLOCKS:
        return f"its {method[len('admission refusal ('):-1]} refuses the pods: {edge.metadata.get('evidence', '')}", node
    return None


def evaluate(graph: OntologyGraph, node: K8sEntity, edge, came_from: K8sEntity) -> tuple[str, K8sEntity] | None:
    """(mechanism, cause) when ``node`` violates an anchor, reached over ``edge`` from ``came_from``; else None.
    Declared semantic anchors first (they can attribute further, as a quota to its main consumer), then conventions."""
    anchors, _ = _declared()
    for a in anchors:
        if a["kind"] == _kind(node) and a["type"] in EVALUATORS:
            found = EVALUATORS[a["type"]](graph, node, a, edge, came_from)
            if found:
                return found
    return _by_conventions(node, edge)
