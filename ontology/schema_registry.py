"""The definitions of what the product collects, read where they are published (docs/schema-registry.md § 2).

Sources, merged in this order, a later one replacing an earlier one for the same group, version and kind:
the bundle of pinned upstream copies (``tools/fetch_specs.py``), the cluster's ``CustomResourceDefinition`` objects,
and the API server's ``/openapi/v3``. No field, kind or value is written here: everything comes from those documents.

Paths use the dotted form of ``ontology/declared_relations.py``: ``seg[]`` walks a list, a segment ``{x}`` or a
map key goes through ``additionalProperties``, and ``seg[k=v]`` (a list item selected by a key) walks the list.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DEFAULT_BUNDLE = Path(__file__).resolve().parent / "spec" / "upstream" / "bundle"


@dataclass(frozen=True)
class FieldSchema:
    type: str = ""
    enum: tuple[str, ...] = ()
    default: Any = None
    has_default: bool = False
    required: tuple[str, ...] = ()        # required properties of this field, when it is an object
    description: str = ""
    list_type: str = ""                   # x-kubernetes-list-type
    list_map_keys: tuple[str, ...] = ()   # x-kubernetes-list-map-keys
    preserve_unknown: bool = False        # x-kubernetes-preserve-unknown-fields, or a free-form object
    int_or_string: bool = False


def _version_rank(v: str) -> tuple:
    """v1 > v1beta2 > v1beta1 > v1alpha1, then the higher major (Kubernetes' version priority)."""
    m = re.fullmatch(r"v(\d+)(?:(alpha|beta)(\d+))?", v)
    if not m:
        return (-1, 0, 0)
    stage = {"alpha": 0, "beta": 1, None: 2}[m[2]]
    return (stage, int(m[1]), int(m[3] or 0))


class SchemaRegistry:
    def __init__(self) -> None:
        # (group, version, kind) -> (root schema, components for $ref, source)
        self._defs: dict[tuple[str, str, str], tuple[dict, dict, str]] = {}
        self._sources: list[str] = []

    # ------------------------------------------------------------------ loading

    def add_openapi_v3(self, doc: dict, source: str = "openapi-v3") -> int:
        comps = ((doc or {}).get("components") or {}).get("schemas") or {}
        n = 0
        for schema in comps.values():
            for gvk in schema.get("x-kubernetes-group-version-kind") or []:
                self._defs[(str(gvk.get("group", "")), str(gvk["version"]), str(gvk["kind"]))] = (schema, comps, source)
                n += 1
        if n:
            self._sources.append(source)
        return n

    def add_crds(self, objects: list[dict], source: str = "crd") -> int:
        n = 0
        for obj in objects:
            if not isinstance(obj, dict) or obj.get("kind") != "CustomResourceDefinition":
                continue
            spec = obj.get("spec") or {}
            group, kind = str(spec.get("group", "")), str((spec.get("names") or {}).get("kind", ""))
            for v in spec.get("versions") or []:
                schema = ((v.get("schema") or {}).get("openAPIV3Schema"))
                if v.get("served", True) and isinstance(schema, dict):
                    self._defs[(group, str(v["name"]), kind)] = (schema, {}, source)
                    n += 1
        if n:
            self._sources.append(source)
        return n

    @classmethod
    def from_bundle(cls, bundle: Path = DEFAULT_BUNDLE) -> "SchemaRegistry":
        reg = cls()
        lock = yaml.safe_load((bundle / "lock.yaml").read_text())
        for s in lock.get("sources") or []:
            for name in sorted(s["files"]):
                path = bundle / s["name"] / name
                label = f"{s['name']}@{s['tag']}/{name}"
                if s["format"] == "openapi-v3":
                    reg.add_openapi_v3(json.loads(path.read_text()), label)
                else:
                    reg.add_crds([d for d in yaml.safe_load_all(path.read_text()) if d], label)
        return reg

    # ------------------------------------------------------------------ queries

    def kinds(self) -> set[tuple[str, str]]:
        return {(g, k) for g, _, k in self._defs}

    def versions(self, group: str, kind: str) -> list[str]:
        return sorted((v for g, v, k in self._defs if g == group and k == kind), key=_version_rank, reverse=True)

    def sources(self) -> list[str]:
        return list(self._sources)

    def _root(self, group: str, kind: str, version: str | None) -> tuple[dict, dict] | None:
        version = version or next(iter(self.versions(group, kind)), None)
        found = self._defs.get((group, version or "", kind))
        return (found[0], found[1]) if found else None

    @staticmethod
    def _deref(s: dict, comps: dict) -> dict:
        """Follow ``$ref`` and merge ``allOf`` (Kubernetes' v3 documents wrap a ref with a default in ``allOf``)."""
        seen = 0
        while isinstance(s, dict) and seen < 32:
            seen += 1
            if "$ref" in s:
                ref = s["$ref"].rsplit("/", 1)[-1]
                rest = {k: v for k, v in s.items() if k != "$ref"}
                s = {**comps.get(ref, {}), **rest}
                continue
            if s.get("allOf"):
                merged = {k: v for k, v in s.items() if k != "allOf"}
                for part in s["allOf"]:
                    part = SchemaRegistry._deref(part, comps)
                    merged = {**part, **merged,
                              "properties": {**(part.get("properties") or {}), **(merged.get("properties") or {})}}
                s = merged
                continue
            return s
        return s

    def node(self, group: str, kind: str, path: str, version: str | None = None) -> dict | None:
        """The raw schema at ``path``, references resolved; None when the path does not exist."""
        root = self._root(group, kind, version)
        if root is None:
            return None
        s, comps = self._deref(root[0], root[1]), root[1]
        if not path:
            return s
        for seg in path.split("."):
            m = re.fullmatch(r"([^\[\]]*)(\[[^\]]*\])?", seg)
            name, many = (m[1], m[2] is not None) if m else (seg, False)
            if s.get("x-kubernetes-preserve-unknown-fields") and name not in (s.get("properties") or {}):
                return {"x-kubernetes-preserve-unknown-fields": True}
            props = s.get("properties") or {}
            if name in props:
                s = self._deref(props[name], comps)
            elif isinstance(s.get("additionalProperties"), dict) and (name.startswith("{") or not props):
                s = self._deref(s["additionalProperties"], comps)
            else:
                return None
            if many:
                if s.get("type") != "array":
                    return None
                s = self._deref(s.get("items") or {}, comps)
        return s

    def field(self, group: str, kind: str, path: str, version: str | None = None) -> FieldSchema | None:
        s = self.node(group, kind, path, version)
        if s is None:
            return None
        free = bool(s.get("x-kubernetes-preserve-unknown-fields")) or (
            s.get("type") == "object" and not s.get("properties") and s.get("additionalProperties") is None
            and "x-kubernetes-int-or-string" not in s)
        return FieldSchema(type=str(s.get("type", "")), enum=tuple(str(x) for x in s.get("enum") or ()),
                           default=s.get("default"), has_default="default" in s,
                           required=tuple(s.get("required") or ()), description=str(s.get("description", "")),
                           list_type=str(s.get("x-kubernetes-list-type", "")),
                           list_map_keys=tuple(s.get("x-kubernetes-list-map-keys") or ()),
                           preserve_unknown=free, int_or_string=bool(s.get("x-kubernetes-int-or-string")))

    def has_status(self, group: str, kind: str) -> bool:
        return self.node(group, kind, "status") is not None

    def has_conditions(self, group: str, kind: str) -> bool:
        return self.node(group, kind, "status.conditions[]") is not None

    def has_observed_generation(self, group: str, kind: str) -> bool:
        return self.node(group, kind, "status.observedGeneration") is not None
