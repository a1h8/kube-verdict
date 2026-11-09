"""Every path the declared ontology reads must exist in the definition of its kind (docs/schema-registry.md § 3).

A declared relation or anchor names fields by path. The schema registry says whether such a field exists for the kind
that holds it, so a path copied wrong from a documentation page, or one a tool's version no longer has, is found
before it silently matches nothing. A kind is resolved to its API group through the registry: unknown or ambiguous
is reported, never guessed.
"""
from __future__ import annotations

import itertools
import re

from ontology.schema_registry import SchemaRegistry
from ontology.spec_loader import AnchorSpec, RelationSpec, load

# parameters that are not paths: expressions, regexes, modes
_NOT_PATHS = {"reasons", "pattern", "method", "evidence", "link", "holder", "scope", "empty_selects_all", "id",
              "group_map", "store", "key", "when", "hop", "through", "aggregate"}
_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def groups_of(reg: SchemaRegistry, kind: str) -> list[str]:
    """The API groups serving a kind name; a product-qualified ``Kind.group`` names its group."""
    if "." in kind:
        k, g = kind.split(".", 1)
        return [g] if (g, k) in reg.kinds() else []
    return sorted(g for g, k in reg.kinds() if k == kind)


def _expand(path: str, group_map: dict) -> list[str]:
    """A placeholder with known values (``group_map``) is expanded; any other stays a map key or list selector."""
    names = [n for n in _PLACEHOLDER.findall(path) if n in group_map]
    if not names:
        return [path]
    out = []
    for values in itertools.product(*(sorted(set(group_map[n].values())) for n in names)):
        p = path
        for n, v in zip(names, values):
            p = p.replace("{" + n + "}", v)
        out.append(p)
    return out


def _paths(params: dict) -> list[tuple[str, str]]:
    """(parameter, path) for every parameter holding one path or a list of paths."""
    out = []
    for name, value in params.items():
        if name in _NOT_PATHS:
            continue
        for v in value if isinstance(value, list) else [value]:
            if isinstance(v, str) and "." in v and "=" not in v.split("]")[-1]:
                out.append((name, v))
    return out


CHECKED: list[tuple[str, str]] = []     # (kind, path) of every check, for the test that the check is not vacuous


def _check(reg: SchemaRegistry, where: str, kind: str, path: str, why: list[str]) -> None:
    CHECKED.append((kind, path))
    gs = groups_of(reg, kind)
    if not gs:
        why.append(f"{where}: kind {kind!r} is in no definition")
    elif len(gs) > 1:
        why.append(f"{where}: kind {kind!r} is served by several groups {gs}; qualify it as Kind.group")
    elif reg.field(gs[0], kind.split(".", 1)[0], path) is None:
        why.append(f"{where}: {kind}.{path} is not in its definition")


def check_relations(reg: SchemaRegistry, specs: list[RelationSpec]) -> list[str]:
    why: list[str] = []
    for i, s in enumerate(specs):
        where = f"relations[{i}] {s.name}"
        gmap = s.params.get("group_map") or {}
        # the matcher decides which kind holds the path
        holder = s.target_kind if s.params.get("holder") == "target" else s.source_kind
        if s.matcher == "event_quote":
            holder = s.source_kind              # target_field is on the object the message quotes
        if holder == "*":
            continue
        for _, path in _paths(s.params):
            for p in _expand(path, gmap):
                _check(reg, where, holder, p, why)
        if s.matcher == "inventory":
            item = s.params["list"]
            keys = ["group", "kind", "namespace", "name"] if s.params["id"] == "fields" else ["id"]
            for k in keys:
                _check(reg, where, holder, f"{item}.{k}", why)
    return why


def check_anchors(reg: SchemaRegistry, anchors: list[AnchorSpec], relations: list[RelationSpec]) -> list[str]:
    why: list[str] = []
    for a in anchors:
        where = f"anchor {a.id}"
        for param, path in _paths(a.params):
            kind = a.kind
            if param == "consumer_start":       # read on the object at the other end of the hop it goes through
                rel = next((r for r in relations if r.name == a.params.get("through")), None)
                kind = rel.target_kind if rel else ""
            _check(reg, where, kind, path.replace("{r}", "{x}"), why)
    return why


def check_declared(reg: SchemaRegistry | None = None) -> list[str]:
    reg = reg or SchemaRegistry.from_bundle()
    relations, anchors = load()
    return check_relations(reg, relations) + check_anchors(reg, anchors, relations)
