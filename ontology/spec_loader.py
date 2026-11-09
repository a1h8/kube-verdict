"""Load and validate the declared ontology: relations, anchors, variability (docs/anchors-and-relations.md, M2).

The data live in ``ontology/spec/relations.yaml`` and ``ontology/spec/anchors.yaml``. This module knows only the fixed
sets of matchers, anchor types and variability classes, and the parameters each one needs; it names no kind. A file
that uses an unknown matcher, type or class, a relation the ontology does not declare, misses a required parameter, or
cites no source, is refused with every reason at once.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ontology.relationships import RelationshipType

SPEC_DIR = Path(__file__).resolve().parent / "spec"
SPEC_VERSION = 1

# matcher -> required parameters (docs/anchors-and-relations.md § 1)
MATCHERS: dict[str, set[str]] = {
    "owner_reference": set(),
    "label_selector": {"selector"},
    "name_reference": {"path", "holder"},
    "port_match": set(),
    "event_quote": {"reasons", "pattern", "link"},
    "marker": {"key", "on"},
}
# anchor type -> required parameters (§ 2)
ANCHOR_TYPES: dict[str, set[str]] = {
    "declared": {"field"},
    "default": {"field"},
    "valid": {"field", "values"},
    "bound": {"left", "right"},
    "fits": {"resource", "evidence"},
    "fresh": {"through"},
}
VARIABILITY = re.compile(r"^(static|volatile|gitops-ignored|controller:[a-z0-9-]+)$")
EVENT_LINKS = {"event", "involved", "both"}
MARKER_ON = {"label", "annotation"}


class SpecError(ValueError):
    """The declared ontology is invalid; ``reasons`` lists every problem found."""

    def __init__(self, reasons: list[str]):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


@dataclass(frozen=True)
class RelationSpec:
    name: str
    source_kind: str
    target_kind: str
    matcher: str
    params: dict[str, Any] = field(default_factory=dict)
    sources: tuple[str, ...] = ()

    @property
    def rel_type(self) -> RelationshipType:
        return RelationshipType[self.name]


@dataclass(frozen=True)
class AnchorSpec:
    id: str
    kind: str
    type: str
    params: dict[str, Any] = field(default_factory=dict)
    variability: str = "static"
    sources: tuple[str, ...] = ()


def _sources(entry: dict, where: str, why: list[str]) -> tuple[str, ...]:
    src = entry.get("sources") or []
    if not isinstance(src, list) or not src or not all(isinstance(s, str) and s.startswith("http") for s in src):
        why.append(f"{where}: sources must be a non-empty list of URLs")
        return ()
    return tuple(src)


def _check_params(where: str, params: Any, required: set[str], why: list[str]) -> dict:
    if not isinstance(params, dict):
        why.append(f"{where}: params must be a mapping")
        return {}
    missing = required - set(params)
    if missing:
        why.append(f"{where}: missing params {sorted(missing)}")
    return params


def parse_relations(doc: Any) -> list[RelationSpec]:
    why: list[str] = []
    out: list[RelationSpec] = []
    entries = (doc or {}).get("relations") if isinstance(doc, dict) else None
    if not isinstance(entries, list):
        raise SpecError(["relations: a top-level list 'relations' is required"])
    for i, e in enumerate(entries):
        where = f"relations[{i}]"
        if not isinstance(e, dict):
            why.append(f"{where}: must be a mapping")
            continue
        name, matcher = e.get("name", ""), e.get("matcher", "")
        if name not in RelationshipType.__members__:
            why.append(f"{where}: {name!r} is not a relation the ontology declares")
        if matcher not in MATCHERS:
            why.append(f"{where}: unknown matcher {matcher!r}")
        for k in ("source_kind", "target_kind"):
            if not isinstance(e.get(k), str) or not e.get(k):
                why.append(f"{where}: {k} is required")
        params = _check_params(where, e.get("params", {}), MATCHERS.get(matcher, set()), why)
        if matcher == "event_quote":
            if params.get("link") not in EVENT_LINKS:
                why.append(f"{where}: link must be one of {sorted(EVENT_LINKS)}")
            try:
                groups = re.compile(str(params.get("pattern", ""))).groupindex
                # the target is named by the message, or recognised by a value it quotes (LimitRanger names no range)
                if "name" not in groups and not ("value" in groups and params.get("target_field")):
                    why.append(f"{where}: pattern needs a named group 'name', or 'value' with target_field")
            except re.error as exc:
                why.append(f"{where}: invalid pattern ({exc})")
        if matcher == "marker" and params.get("on") not in MARKER_ON:
            why.append(f"{where}: on must be one of {sorted(MARKER_ON)}")
        src = _sources(e, where, why)
        out.append(RelationSpec(name, str(e.get("source_kind", "")), str(e.get("target_kind", "")), matcher,
                                dict(params), src))
    if why:
        raise SpecError(why)
    return out


def parse_anchors(doc: Any) -> list[AnchorSpec]:
    why: list[str] = []
    out: list[AnchorSpec] = []
    entries = (doc or {}).get("anchors") if isinstance(doc, dict) else None
    if not isinstance(entries, list):
        raise SpecError(["anchors: a top-level list 'anchors' is required"])
    seen: set[str] = set()
    for i, e in enumerate(entries):
        where = f"anchors[{i}]"
        if not isinstance(e, dict):
            why.append(f"{where}: must be a mapping")
            continue
        aid, typ = str(e.get("id", "")), e.get("type", "")
        if not aid or aid in seen:
            why.append(f"{where}: id is required and unique")
        seen.add(aid)
        if typ not in ANCHOR_TYPES:
            why.append(f"{where}: unknown anchor type {typ!r}")
        if not isinstance(e.get("kind"), str) or not e.get("kind"):
            why.append(f"{where}: kind is required")
        var = str(e.get("variability", "static"))
        if not VARIABILITY.match(var):
            why.append(f"{where}: unknown variability {var!r}")
        params = _check_params(where, e.get("params", {}), ANCHOR_TYPES.get(typ, set()), why)
        if "hop" in params and params["hop"] not in RelationshipType.__members__:
            why.append(f"{where}: hop {params['hop']!r} is not a relation the ontology declares")
        if var.startswith("controller:") and "hop" not in params:
            why.append(f"{where}: a controller-managed field names the relation to its controller (hop)")
        src = _sources(e, where, why)
        out.append(AnchorSpec(aid, str(e.get("kind", "")), typ, dict(params), var, src))
    if why:
        raise SpecError(why)
    return out


def load(spec_dir: Path = SPEC_DIR) -> tuple[list[RelationSpec], list[AnchorSpec]]:
    """The declared relations and anchors, validated."""
    rel = parse_relations(yaml.safe_load((spec_dir / "relations.yaml").read_text()))
    anc = parse_anchors(yaml.safe_load((spec_dir / "anchors.yaml").read_text()))
    return rel, anc
