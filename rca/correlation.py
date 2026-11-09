"""The correlation engine, minimal version (docs/correlation-engine.md § 4; rule: docs/s2-context-reach.md, M4-min).

From each symptom, walk back over the typed relations (OWNS, LIMITS, QUOTA_BLOCKS, CONFIGURES) built by
``ontology/relations.py``, at most ``MAX_HOPS``, and apply a fixed predicate to each object reached. A chain is
kept only when an object on it is abnormal by its kind's predicate; nothing is guessed from names. Each chain says
its cause, its mechanism, and every hop with the evidence its edge carries.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ontology.entities import K8sEntity
from ontology.graph import OntologyGraph
from ontology.anchors import evaluate, quantity  # noqa: F401  (quantity: re-exported for its tests)
from ontology.relationships import RelationshipType as R

MAX_HOPS = 4
BACK = {R.OWNS, R.LIMITS, R.QUOTA_BLOCKS, R.CONFIGURES, R.SCALES, R.RESTRICTS, R.MANAGES}


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
                    found = evaluate(graph, up, edge, node)
                    if found:
                        mechanism, cause = found
                        if cause is not up:
                            path = path + [Hop(_fqn(cause), "FILLS", _fqn(up), mechanism)]
                        key = _fqn(cause)
                        if key not in chains or len(path) < len(chains[key].hops):
                            chains[key] = Chain(_fqn(start), key, mechanism, path)
                    stack.append((up, path, seen | {up.uid}))
    return sorted(chains.values(), key=lambda c: (len(c.hops), c.cause))
