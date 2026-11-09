"""Build the relations declared in ontology/spec/relations.yaml (docs/anchors-and-relations.md § 1, M2 step 2).

One generic implementation per matcher; no kind is named here. An edge already in the graph (same source, target and
relation) is not built twice, so a relation the collector builds and the spec declares exists once.
"""
from __future__ import annotations

import re
from typing import Any, Iterator

from ontology.entities import K8sEntity, ResourceKind
from ontology.graph import OntologyGraph
from ontology.relationships import Edge
from ontology.spec_loader import RelationSpec, load

RELATIONS_VERSION = 2   # 2: built from the declared ontology


def _kind(e: K8sEntity) -> str:
    return str(getattr(e.kind, "value", e.kind))


def _snake(s: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", s).lower()


def _key(d: dict, k: str) -> Any:
    """A key in either spelling: the API's camelCase or the client's to_dict snake_case."""
    if not isinstance(d, dict):
        return None
    return d.get(k) if k in d else d.get(_snake(k))


def resolve(obj: Any, path: str) -> list[Any]:
    """All values at a dotted path, where ``seg[]`` walks a list: ``spec.containers[].envFrom[].configMapRef.name``."""
    values = [obj]
    for seg in path.split("."):
        many, name = seg.endswith("[]"), seg[:-2] if seg.endswith("[]") else seg
        nxt: list[Any] = []
        for v in values:
            x = _key(v, name)
            if x is None:
                continue
            if many:
                nxt.extend(x if isinstance(x, list) else [])
            else:
                nxt.append(x)
        values = nxt
    return values


def _matches_kind(e: K8sEntity, kind: str) -> bool:
    return kind == "*" or _kind(e) == kind


def _owner_refs(e: K8sEntity) -> list[dict]:
    meta = (e.raw or {}).get("metadata") or {}
    refs = meta.get("owner_references") or meta.get("ownerReferences") or []
    if not refs and getattr(e, "owner_ref_kind", ""):
        refs = [{"kind": e.owner_ref_kind, "name": e.owner_ref_name}]
    return refs


def _owner_reference(graph: OntologyGraph, spec: RelationSpec) -> Iterator[tuple]:
    index = {(_kind(e), e.namespace, e.name): e for e in graph.entities()}
    for e in list(graph.entities()):
        for ref in _owner_refs(e):
            owner = index.get((ref.get("kind", ""), e.namespace, ref.get("name", "")))
            if owner is not None and _matches_kind(owner, spec.source_kind) and _matches_kind(e, spec.target_kind):
                yield owner, e, "ownerReferences", f"{_kind(e)}/{e.name} owned by {_kind(owner)}/{owner.name}", 1.0


def _name_reference(graph: OntologyGraph, spec: RelationSpec) -> Iterator[tuple]:
    paths = spec.params["path"] if isinstance(spec.params["path"], list) else [spec.params["path"]]
    holder_is_source = spec.params["holder"] == "source"
    holder_kind = spec.source_kind if holder_is_source else spec.target_kind
    ref_kind = spec.target_kind if holder_is_source else spec.source_kind
    refs = {(e.namespace, e.name): e for e in graph.entities() if _matches_kind(e, ref_kind)}
    for holder in list(graph.entities()):
        if not _matches_kind(holder, holder_kind):
            continue
        for path in paths:
            for name in resolve(holder.raw or {}, path):
                ref = refs.get((holder.namespace, str(name)))
                if ref is None:
                    continue
                src, dst = (holder, ref) if holder_is_source else (ref, holder)
                how = next((seg[:-2] for seg in reversed(path.split(".")) if seg.endswith("[]")), path)
                yield src, dst, how, f"{_kind(holder)}/{holder.name} {path} = {name}", 1.0


def _label_selector(graph: OntologyGraph, spec: RelationSpec) -> Iterator[tuple]:
    for src in list(graph.entities()):
        if not _matches_kind(src, spec.source_kind):
            continue
        sel = (resolve(src.raw or {}, spec.params["selector"]) or [None])[0] or getattr(src, "selector", None)
        if not isinstance(sel, dict) or not sel:
            continue
        for dst in graph.entities():
            if (_matches_kind(dst, spec.target_kind) and dst.namespace == src.namespace
                    and all((dst.labels or {}).get(k) == v for k, v in sel.items())):
                yield src, dst, "selector", f"{sel}", 1.0


def _fill(template: str, groups: dict[str, str]) -> str:
    return template.format(**groups)


def _event_quote(graph: OntologyGraph, spec: RelationSpec) -> Iterator[tuple]:
    rx = re.compile(spec.params["pattern"])
    reasons = set(spec.params["reasons"])
    gmap = spec.params.get("group_map") or {}
    sources = [e for e in graph.entities() if _matches_kind(e, spec.source_kind)]
    for ev in graph.entities(ResourceKind.EVENT):
        if getattr(ev, "reason", "") not in reasons:
            continue
        m = rx.search(getattr(ev, "message", "") or "")
        if not m:
            continue
        groups = {k: (gmap.get(k, {}).get(v, v) if v is not None else "") for k, v in m.groupdict().items()}
        method = _fill(spec.params.get("method", spec.matcher), groups)
        evidence = _fill(spec.params["evidence"], groups) if spec.params.get("evidence") else ev.message
        if "name" in groups:
            src = next((s for s in sources if s.namespace == ev.namespace and s.name == groups["name"]), None)
            matched = [src] if src else []
        else:
            field_path = _fill(spec.params["target_field"], groups)
            matched = [s for s in sources if s.namespace == ev.namespace and _field_equals(s, field_path, groups["value"])]
        involved = next((e for e in graph.entities() if _kind(e) == getattr(ev, "involved_kind", "")
                         and e.name == getattr(ev, "involved_name", "") and e.namespace == ev.namespace), None)
        for src in matched:
            if spec.params["link"] in ("event", "both"):
                yield src, ev, method, evidence, 1.0
            if spec.params["link"] in ("involved", "both") and involved is not None:
                yield src, involved, method, evidence, 1.0


def _field_equals(e: K8sEntity, path: str, value: str) -> bool:
    """``spec.limits[type=Container].max.memory``: a list item selected by a key, then a dotted path."""
    m = re.match(r"^(?P<list>[\w.]+)\[(?P<k>\w+)=(?P<v>[^\]]+)\]\.(?P<rest>.+)$", path)
    if not m:
        return any(str(x) == value for x in resolve(e.raw or {}, path))
    for item in resolve(e.raw or {}, m["list"] + "[]"):
        if isinstance(item, dict) and str(_key(item, m["k"])) == m["v"]:
            if any(str(x) == value for x in resolve(item, m["rest"])):
                return True
    return False


MATCHERS = {"owner_reference": _owner_reference, "name_reference": _name_reference,
            "label_selector": _label_selector, "event_quote": _event_quote}


def build_named(graph: OntologyGraph, *names: str) -> dict[str, int]:
    """Only the declared relations of these names (tests, and partial rebuilds)."""
    return build_declared(graph, [s for s in load()[0] if s.name in names])


def build_declared(graph: OntologyGraph, specs: list[RelationSpec] | None = None) -> dict[str, int]:
    """Every declared relation whose matcher is implemented; returns the edges added, per relation."""
    specs = specs if specs is not None else load()[0]
    have = {(e.source_uid, e.target_uid, e.rel_type) for e in graph._edges}
    counts: dict[str, int] = {}
    for spec in specs:
        fn = MATCHERS.get(spec.matcher)
        if fn is None:
            continue
        n = 0
        for src, dst, method, evidence, conf in fn(graph, spec):
            key = (src.uid, dst.uid, spec.rel_type)
            if key in have:
                continue
            graph.add_edge(Edge(src.uid, dst.uid, spec.rel_type, {
                "method": method, "evidence": evidence, "confidence": conf, "relations_version": RELATIONS_VERSION,
                "declared": spec.name}))
            have.add(key)
            n += 1
        counts[spec.name] = counts.get(spec.name, 0) + n
    return counts
