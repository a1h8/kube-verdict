"""The correlation engine, minimal version (docs/correlation-engine.md § 4; rule: docs/s2-context-reach.md, M4-min).

From each symptom, walk back over the typed relations (OWNS, LIMITS, QUOTA_BLOCKS, CONFIGURES) built by
``ontology/relations.py``, at most ``MAX_HOPS``, and apply a fixed predicate to each object reached. A chain is
kept only when an object on it is abnormal by its kind's predicate; nothing is guessed from names. Each chain says
its cause, its mechanism, and every hop with the evidence its edge carries.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ontology.entities import K8sEntity, ResourceKind
from ontology.graph import OntologyGraph
from ontology.relationships import RelationshipType as R

MAX_HOPS = 4
BACK = {R.OWNS, R.LIMITS, R.QUOTA_BLOCKS, R.CONFIGURES}


@dataclass
class Hop:
    src: str          # the object acting on the next one (an edge's source)
    rel: str
    dst: str
    evidence: str


@dataclass
class Chain:
    symptom: str
    cause: str
    mechanism: str
    hops: list[Hop] = field(default_factory=list)

    def to_text(self) -> str:
        path = " ; ".join(f"{h.dst} <-{h.rel}- {h.src} ({h.evidence})" for h in self.hops)
        return f"cause={self.cause} symptom={self.symptom} mechanism={self.mechanism} | hops: {path}"


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


def _abnormal(graph: OntologyGraph, node: K8sEntity, edge, came_from: K8sEntity) -> tuple[str, K8sEntity] | None:
    kind, method = _kind(node), (edge.metadata or {}).get("method", "")
    if kind == "LimitRange" and edge.rel_type == R.LIMITS:
        if method.startswith("admission refusal"):
            return f"its {method[len('admission refusal ('):-1]} refuses the pods: {edge.metadata['evidence']}", node
        if method.startswith("default injected") and _kind(came_from) == "Pod" and oom_killed(came_from):
            return f"its default limit was injected and the container is OOMKilled: {edge.metadata['evidence']}", node
    if kind == "ResourceQuota" and edge.rel_type == R.QUOTA_BLOCKS:
        return quota_full(graph, node)
    if kind == "ConfigMap" and edge.rel_type == R.CONFIGURES and _kind(came_from) == "Pod":
        written, started = last_write(node), started_at(came_from)
        if written and started and written < started:
            return (f"last written {written.isoformat()}, before {came_from.name} started ({started.isoformat()}); "
                    f"read through the environment at start ({method})", node)
    return None


def _starts(graph: OntologyGraph, seed: K8sEntity) -> list[K8sEntity]:
    """A seed, and for a warning event the object it is about (HAS_EVENT's source)."""
    out = [seed]
    if _kind(seed) == "Event":
        out += [graph.get(e.source_uid) for e in graph._radj.get(seed.uid, []) if e.rel_type == R.HAS_EVENT]
    return [e for e in out if e is not None]


def correlate(graph: OntologyGraph, seeds: list[K8sEntity] | None = None) -> list[Chain]:
    from dedup.bfs import find_unhealthy
    chains: dict[str, Chain] = {}
    for seed in seeds if seeds is not None else find_unhealthy(graph):
        for start in _starts(graph, seed):
            stack: list[tuple[K8sEntity, list[Hop], set[str]]] = [(start, [], {start.uid})]
            while stack:
                node, hops, seen = stack.pop()
                if len(hops) >= MAX_HOPS:
                    continue
                for edge in graph._radj.get(node.uid, []):
                    if edge.rel_type not in BACK or edge.source_uid in seen:
                        continue
                    up = graph.get(edge.source_uid)
                    if up is None:
                        continue
                    path = hops + [Hop(_fqn(up), edge.rel_type.value, _fqn(node), (edge.metadata or {}).get(
                        "evidence", (edge.metadata or {}).get("method", "")))]
                    found = _abnormal(graph, up, edge, node)
                    if found:
                        mechanism, cause = found
                        if cause is not up:
                            path = path + [Hop(_fqn(cause), "FILLS", _fqn(up), mechanism)]
                        key = _fqn(cause)
                        if key not in chains or len(path) < len(chains[key].hops):
                            chains[key] = Chain(_fqn(start), key, mechanism, path)
                    stack.append((up, path, seen | {up.uid}))
    return sorted(chains.values(), key=lambda c: (len(c.hops), c.cause))
