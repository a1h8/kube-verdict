"""Who owns each field of an object, read from ``metadata.managedFields`` (docs/anchors-and-relations.md, M2 step 2).

FieldsV1 encoding, read in ``kubernetes-sigs/structured-merge-diff`` (fieldpath/serialize-pe.go): ``f:<name>`` a field,
``k:<JSON>`` a list item by its key fields, ``v:<JSON>`` a set item by value, ``i:<n>`` an item by index. An entry's
``time`` is the entry's (the last time that manager changed one of its fields), not a field's: an origin dated from it
is an upper bound.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ontology.entities import K8sEntity

MANAGERS_FILE = Path(__file__).resolve().parent / "spec" / "managers.yaml"
ROLES = {"controller", "person", "release"}


@dataclass(frozen=True)
class Owner:
    manager: str
    operation: str
    subresource: str
    time: str


def _get(d: Any, *names: str) -> Any:
    for n in names:
        if isinstance(d, dict) and d.get(n) is not None:
            return d[n]
    return None


def _leaves(node: Any, prefix: tuple = ()) -> list[tuple]:
    """Every path of a FieldsV1 tree; '.' (the item itself) is not an element of the path."""
    if not isinstance(node, dict) or not node:
        return [prefix] if prefix else []
    out: list[tuple] = []
    for key, child in node.items():
        if key == ".":
            out.append(prefix)
            continue
        out += _leaves(child, prefix + (key,))
    return out


def owners(entity: K8sEntity) -> dict[tuple, Owner]:
    """FieldsV1 path → its owner; when two entries list a path, the one written last."""
    meta = _get(entity.raw or {}, "metadata") or {}
    out: dict[tuple, Owner] = {}
    for e in _get(meta, "managed_fields", "managedFields") or []:
        fields = _get(e, "fields_v1", "fieldsV1") or {}
        o = Owner(str(e.get("manager", "")), str(e.get("operation", "")), str(e.get("subresource") or ""),
                  str(e.get("time") or ""))
        for path in _leaves(fields):
            if path not in out or o.time >= out[path].time:
                out[path] = o
    return out


def fields_v1(group: str, kind: str, path: str, registry=None) -> tuple | None:
    """A dotted path, list items selected by their keys (``spec.containers[name=api].ports[containerPort=80,
    protocol=TCP]``), as a FieldsV1 path. Which fields key a list, and their types, are read in the definition
    (``x-kubernetes-list-map-keys``); a selector that does not name exactly those keys has no FieldsV1 path."""
    from ontology.schema_registry import SchemaRegistry
    reg = registry or SchemaRegistry.bundled()
    out: list[str] = []
    walked: list[str] = []
    for seg in path.split("."):
        name, _, sel = seg.partition("[")
        walked.append(name)
        out.append(f"f:{name}")
        if not sel:
            continue
        lst = reg.field(group, kind, ".".join(walked))
        given = dict(kv.split("=", 1) for kv in sel.rstrip("]").split(","))
        if lst is None or not lst.list_map_keys or set(given) != set(lst.list_map_keys):
            return None
        key = {}
        for k in sorted(lst.list_map_keys):
            ftype = reg.field(group, kind, ".".join(walked) + f"[].{k}")
            key[k] = int(given[k]) if ftype is not None and ftype.type == "integer" else given[k]
        out.append("k:" + json.dumps(key, separators=(",", ":")))
        walked[-1] += "[]"
    return tuple(out)


def fields_path(kind: str, drift_field: str) -> tuple | None:
    """The FieldsV1 path of a field the applied-vs-live comparison names (``spec.replicas``, ``container.<n>.<f…>``)."""
    from ontology.schema_registry import SchemaRegistry
    groups = SchemaRegistry.bundled().groups(kind)
    if len(groups) != 1:
        return None
    if drift_field.startswith("container."):
        _, name, *rest = drift_field.split(".")
        base = "spec" if kind == "Pod" else "spec.template.spec"
        drift_field = ".".join([f"{base}.containers[name={name}]", *rest])
    return fields_v1(groups[0], kind, drift_field)


def roles(path: Path = MANAGERS_FILE) -> dict[str, str]:
    doc = yaml.safe_load(path.read_text()) or {}
    out: dict[str, str] = {}
    for i, m in enumerate(doc.get("managers") or []):
        if m.get("role") not in ROLES or not m.get("name") or not (m.get("sources") or []):
            raise ValueError(f"managers[{i}]: a name, a role in {sorted(ROLES)} and sources are required")
        out[m["name"]] = m["role"]
    return out


def for_kinds(path: Path = MANAGERS_FILE) -> dict[str, str]:
    """Field manager name -> the kind of object it writes for (``for_kind``), when ``managers.yaml`` says it."""
    doc = yaml.safe_load(path.read_text()) or {}
    return {m["name"]: m["for_kind"] for m in doc.get("managers") or [] if m.get("for_kind")}


def owner_of(entity: K8sEntity, kind: str, drift_field: str) -> Owner | None:
    path = fields_path(kind, drift_field)
    return owners(entity).get(path) if path else None
