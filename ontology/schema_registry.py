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
    _bundle: "SchemaRegistry | None" = None

    @classmethod
    def bundled(cls) -> "SchemaRegistry":
        """The registry of the committed bundle, built once per process."""
        if cls._bundle is None:
            cls._bundle = cls.from_bundle()
        return cls._bundle

    def __init__(self) -> None:
        # (group, version, kind) -> (root schema, components for $ref, source)
        self._defs: dict[tuple[str, str, str], tuple[dict, dict, str]] = {}
        self._sources: list[str] = []
        # (group, kind) -> [{"input", "server", "defaults": {path: value}}], from tools/observe_defaults.py
        self._observed: dict[tuple[str, str], list[dict]] = {}

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
                elif s["format"] == "crd":
                    reg.add_crds([d for d in yaml.safe_load_all(path.read_text()) if d], label)
                elif s["format"] == "observed-defaults":
                    reg.add_observed(json.loads(path.read_text()), label)
        return reg

    def add_observed(self, record: dict, source: str = "observed-defaults") -> None:
        """Defaults the API server applied to a minimal object of a kind (``tools/observe_defaults.py``)."""
        group = str(record["apiVersion"]).rpartition("/")[0]
        self._observed.setdefault((group, str(record["kind"])), []).append(
            {"input": record.get("input", ""), "server": record.get("server", ""), "defaults": record["defaults"]})
        self._sources.append(source)

    def observed_defaults(self, group: str, kind: str) -> list[dict]:
        """Each observation of a kind: its input and what the server added. A conditional default shows as a path
        whose value differs between two inputs."""
        return list(self._observed.get((group, kind), []))

    # ------------------------------------------------------------------ queries

    def kinds(self) -> set[tuple[str, str]]:
        return {(g, k) for g, _, k in self._defs}

    def versions(self, group: str, kind: str) -> list[str]:
        return sorted((v for g, v, k in self._defs if g == group and k == kind), key=_version_rank, reverse=True)

    def groups(self, kind: str) -> list[str]:
        """The API groups that serve a kind name."""
        return sorted(g for g, k in self.kinds() if k == kind)

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


_TYPES = {"string": (str,), "integer": (int,), "number": (int, float), "boolean": (bool,), "object": (dict,),
          "array": (list,)}


def _type_ok(value: Any, s: dict) -> bool:
    from datetime import date, datetime
    t = s.get("type")
    if value is None or not t:
        return True
    if s.get("x-kubernetes-int-or-string"):
        return isinstance(value, (int, str)) and not isinstance(value, bool)
    if t == "string" and isinstance(value, (datetime, date)):   # a YAML timestamp, a date-time string in the API
        return True
    if t in ("integer", "number") and isinstance(value, bool):
        return False
    return isinstance(value, _TYPES.get(t, (object,)))


def _validate(reg: "SchemaRegistry", value: Any, s: dict, comps: dict, path: str, out: list[str]) -> None:
    s = reg._deref(s, comps)
    if not _type_ok(value, s):
        out.append(f"{path}: {type(value).__name__} where the definition has {s.get('type')}")
        return
    if s.get("enum") and value is not None and str(value) not in {str(x) for x in s["enum"]}:
        out.append(f"{path}: {value!r} is not one of {list(s['enum'])}")
    if isinstance(value, dict):
        if s.get("x-kubernetes-preserve-unknown-fields"):
            return
        props, extra = s.get("properties") or {}, s.get("additionalProperties")
        if not props and extra is None:
            return                                   # a free-form object
        for k in s.get("required") or []:
            if k not in value:
                out.append(f"{path}.{k}: required, missing")
        for k, v in value.items():
            if k in props:
                _validate(reg, v, props[k], comps, f"{path}.{k}", out)
            elif isinstance(extra, dict):
                _validate(reg, v, extra, comps, f"{path}.{k}", out)
            elif extra is not True:
                out.append(f"{path}.{k}: not in the definition")
    elif isinstance(value, list):
        for i, item in enumerate(value):
            _validate(reg, item, s.get("items") or {}, comps, f"{path}[{i}]", out)


def validate(reg: "SchemaRegistry", obj: dict) -> list[str]:
    """What in ``obj`` its definition does not allow: an unknown kind or field, a wrong type, a value outside an enum,
    a missing required field. Nothing is said about fields under ``x-kubernetes-preserve-unknown-fields``."""
    kind, api_version = str(obj.get("kind", "")), str(obj.get("apiVersion", ""))
    group, _, version = api_version.rpartition("/")
    found = reg._defs.get((group, version, kind))
    if found is None:
        return [f"{api_version} {kind}: no definition"]
    out: list[str] = []
    _validate(reg, obj, found[0], found[1], kind, out)
    return out
