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
    score: float = 0.5

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


def _chain(start: K8sEntity, hops: list[Hop], confs: list[float],
           found: list[tuple[int, str, K8sEntity]]) -> Chain:
    """The chain of one path: its cause is the farthest abnormal object on it (docs/correlation-engine.md § 4, point 4);
    a nearer abnormal object stays as a hop, its mechanism added to that hop's evidence."""
    at, mechanism, cause = found[-1]
    kept = [Hop(h.src, h.rel, h.dst, h.evidence) for h in hops[:at + 1]]
    for i, m, _ in found[:-1]:
        kept[i].evidence = f"{kept[i].evidence}; abnormal: {m}"
    if _fqn(cause) != kept[-1].src:
        kept.append(Hop(_fqn(cause), "FILLS", kept[-1].src, mechanism))
    score = 0.5
    for c in confs[:at + 1]:
        score *= c
    return Chain(_fqn(start), _fqn(cause), mechanism, kept, score)


def rank(chains: list[Chain]) -> list[Chain]:
    """By score, then length (the shortest first), then fqn (point 5)."""
    return sorted(chains, key=lambda c: (-c.score, len(c.hops), c.cause))


def correlate(graph: OntologyGraph, seeds: list[K8sEntity] | None = None) -> list[Chain]:
    """Walk back from each symptom over the typed relations, as far as ``MAX_HOPS``. Each path that reaches an abnormal
    object gives a chain whose cause is the farthest one on it; per cause, the shortest chain is kept. A chain's score
    is 0.5 x the product of its edges' confidence. Not implemented, for lack of inputs: the independent-source bonus
    and the contradiction penalty of point 5 (docs/s2g/gitops-cases.md, change 2b)."""
    from dedup.bfs import find_unhealthy
    chains: dict[str, Chain] = {}

    def keep(ch: Chain) -> None:
        if ch.cause not in chains or len(ch.hops) < len(chains[ch.cause].hops):
            chains[ch.cause] = ch

    for seed in seeds if seeds is not None else find_unhealthy(graph):
        for start in _starts(graph, seed):
            stack: list[tuple[K8sEntity, list[Hop], list[float], list[tuple[int, str, K8sEntity]], set[str]]] = [
                (start, [], [], [], {start.uid})]
            while stack:
                node, hops, confs, found, seen = stack.pop()
                extended = False
                if len(hops) < MAX_HOPS:
                    for edge in graph._radj.get(node.uid, []):
                        if edge.rel_type not in BACK or edge.source_uid in seen:
                            continue
                        up = graph.get(edge.source_uid)
                        if up is None:
                            continue
                        meta = edge.metadata or {}
                        path = hops + [Hop(_fqn(up), edge.rel_type.value, _fqn(node),
                                           meta.get("evidence", meta.get("method", "")))]
                        conf = 1.0 if meta.get("confidence") is None else float(meta["confidence"])
                        hit = evaluate(graph, up, edge, node)
                        marks = found + [(len(path) - 1, hit[0], hit[1])] if hit else found
                        stack.append((up, path, confs + [conf], marks, seen | {up.uid}))
                        extended = True
                if not extended and found:
                    keep(_chain(start, hops, confs, found))
    return rank(list(chains.values()))
