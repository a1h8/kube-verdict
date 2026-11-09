"""Relations computed from the fields the Kubernetes API documents (docs/correlation-engine.md § 1, M1).

Since M2 step 2 (docs/anchors-and-relations.md) the relations are declared in ontology/spec/relations.yaml and built
by ontology/declared_relations.py; this module keeps only what is not data yet. Run once the graph holds every collected object (end of ``K8sCollector.collect``), live and in the replay alike.
Each relation is an ``Edge`` whose metadata says how it was computed (``method``), what shows it (``evidence``),
its ``confidence`` and ``relations_version``. Nothing here reads names or free text to guess a link, except the
messages the API server's admission plugins write, quoted from their documented form.
"""
from __future__ import annotations

from typing import Any

from ontology.entities import K8sEntity, ResourceKind
from ontology.graph import OntologyGraph
from ontology.relationships import Edge, RelationshipType

RELATIONS_VERSION = 1

def _kind(entity: K8sEntity) -> str:
    return str(getattr(entity.kind, "value", entity.kind))


def _of_kind(graph: OntologyGraph, kind: str) -> list[K8sEntity]:
    return [e for e in graph.entities() if _kind(e) == kind]


def _get(d: Any, *path: str) -> Any:
    for p in path:
        if not isinstance(d, dict):
            return None
        d = d.get(p)
    return d


def _edge(graph: OntologyGraph, src: K8sEntity, dst: K8sEntity, rel: RelationshipType, method: str,
          evidence: str, confidence: float) -> None:
    graph.add_edge(Edge(src.uid, dst.uid, rel, {"method": method, "evidence": evidence, "confidence": confidence,
                                                 "relations_version": RELATIONS_VERSION}))


def _container_limits(pod: K8sEntity) -> dict[str, dict[str, str]]:
    """container name -> {resource: limit}, from the pod's spec (client ``to_dict`` or API JSON)."""
    out: dict[str, dict[str, str]] = {}
    for c in _get(pod.raw, "spec", "containers") or []:
        limits = _get(c, "resources", "limits") or {}
        out[str(c.get("name", ""))] = {str(k): str(v) for k, v in limits.items()}
    return out


def _owner(graph: OntologyGraph, kind: str, name: str, namespace: str | None) -> K8sEntity | None:
    if not (kind and name):
        return None
    return next((e for e in graph.entities() if _kind(e) == kind and e.name == name and e.namespace == namespace),
                None)


def _owner_refs(entity: K8sEntity) -> list[dict]:
    meta = (entity.raw or {}).get("metadata") or {}
    return meta.get("owner_references") or meta.get("ownerReferences") or []


def _declared_limits(graph: OntologyGraph, pod: K8sEntity) -> dict[str, dict[str, str]] | None:
    """What the pod's owner template declares (container -> {resource: limit}); None when no owner template is known.

    Walks up the owners (ReplicaSet then Deployment, Job then CronJob) to the first template that lists containers.
    """
    owner = _owner(graph, getattr(pod, "owner_ref_kind", ""), getattr(pod, "owner_ref_name", ""), pod.namespace)
    for _ in range(3):
        if owner is None:
            return None
        containers = (_get(owner.raw, "spec", "template", "spec", "containers")
                      or _get(owner.raw, "spec", "jobTemplate", "spec", "template", "spec", "containers")
                      or _get(owner.raw, "spec", "job_template", "spec", "template", "spec", "containers") or [])
        if containers:
            return {str(c.get("name", "")): {str(k): str(v) for k, v in (_get(c, "resources", "limits") or {}).items()}
                    for c in containers}
        ref = next(iter(_owner_refs(owner)), None)
        owner = _owner(graph, (ref or {}).get("kind", ""), (ref or {}).get("name", ""), pod.namespace) if ref else None
    return None


def build_limits_injected(graph: OntologyGraph) -> int:
    """LIMITS for an injected default: LimitRange → Pod, when a container's limit equals the LimitRange's
    ``spec.limits[type=Container].default`` for that resource and the pod's owner template declares no limit for it
    (a default applies only then). With LimitRanger's own record on the pod (``kubernetes.io/limit-ranger:
    "LimitRanger plugin set: <res> limit for container <name>"``) the confidence is 1.0; with only one of the two,
    0.7, since an annotation can be written by anyone (H23) and a declared value can equal the default by chance.
    LimitRanger's refusals are declared data (ontology/spec/relations.yaml).
    """
    n = 0
    by_ns: dict[str | None, list[K8sEntity]] = {}
    for lr in _of_kind(graph, "LimitRange"):
        by_ns.setdefault(lr.namespace, []).append(lr)
    if not by_ns:
        return 0

    for pod in graph.entities(ResourceKind.POD):
        ranges = by_ns.get(pod.namespace)
        if not ranges:
            continue
        note = str((pod.k8s_annotations or {}).get("kubernetes.io/limit-ranger", ""))
        declared = _declared_limits(graph, pod)
        for lr in ranges:
            for item in _get(lr.raw, "spec", "limits") or []:
                if item.get("type") != "Container":
                    continue
                for res, default in (item.get("default") or {}).items():
                    for cname, limits in _container_limits(pod).items():
                        if declared is not None and res in declared.get(cname, {}):
                            continue          # the template declares it: LimitRanger set nothing (the documented rule)
                        # an equal value proves nothing when the template is unknown: only LimitRanger's record then
                        equal = declared is not None and limits.get(res) == str(default)
                        recorded = f"{res} limit for container {cname}" in note
                        if not (equal or recorded):
                            continue
                        conf = 1.0 if (equal and recorded) else 0.7
                        method = "default injected" + (" (annotation)" if recorded else "") + (
                            " (value)" if equal else "")
                        _edge(graph, lr, pod, RelationshipType.LIMITS, method,
                              f"container {cname} {res} limit {limits.get(res, '?')} = default {default}"
                              + (f"; {note}" if recorded else ""), conf)
                        n += 1

    return n


def build_relations(graph: OntologyGraph) -> dict[str, int]:
    """The declared relations (ontology/spec/relations.yaml, built by ontology/declared_relations.py), and the one
    relation still in code: a default LimitRanger injected (LIMITS), which is the variability of the pod's limits
    (docs/anchors-and-relations.md § 3) and moves to data with it (step 5)."""
    from ontology.declared_relations import build_declared
    counts = build_declared(graph)
    counts["LIMITS (injected default)"] = build_limits_injected(graph)
    return counts
