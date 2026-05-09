from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .entities import K8sEntity, ResourceKind


@dataclass
class APIResourceInfo:
    """Describes a resource kind as reported by the API server discovery endpoint."""
    group: str          # "" for core, "apps", "networking.k8s.io", etc.
    version: str        # "v1", "v1beta1", etc.
    kind: str           # "Pod", "MyCustomResource", etc.
    plural: str         # "pods", "mycustomresources"
    namespaced: bool
    verbs: list[str] = field(default_factory=list)
    short_names: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)

    @property
    def api_version(self) -> str:
        if self.group:
            return f"{self.group}/{self.version}"
        return self.version

    @property
    def is_listable(self) -> bool:
        return "list" in self.verbs

    def __str__(self) -> str:
        return f"{self.api_version}/{self.kind}"


@dataclass
class GenericEntity(K8sEntity):
    """
    Represents any K8s resource type discovered at runtime — including CRDs.
    Fields are stored as a flat dict extracted from the live API object.
    """
    api_version: str = ""
    resource_kind_str: str = ""       # original kind string from API server
    spec_fields: dict[str, Any] = field(default_factory=dict)
    status_fields: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        # Use UNKNOWN as a sentinel; the real kind string is in resource_kind_str
        self.kind = ResourceKind.NAMESPACE  # placeholder — not used for routing
        self.kind = self.resource_kind_str  # type: ignore[assignment]

    @classmethod
    def from_api_object(cls, obj: dict[str, Any], resource_info: APIResourceInfo) -> "GenericEntity":
        meta = obj.get("metadata", {})
        uid = meta.get("uid") or f"{resource_info.kind}-{meta.get('namespace','')}-{meta.get('name','')}"

        ts = None
        ts_raw = meta.get("creationTimestamp")
        if ts_raw:
            try:
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            except ValueError:
                pass

        entity = cls(
            uid=uid,
            name=meta.get("name", ""),
            kind=ResourceKind.NAMESPACE,  # overridden below
            namespace=meta.get("namespace"),
            labels=meta.get("labels") or {},
            annotations=meta.get("annotations") or {},
            created_at=ts,
            api_version=resource_info.api_version,
            resource_kind_str=resource_info.kind,
            spec_fields=_flatten(obj.get("spec") or {}),
            status_fields=_flatten(obj.get("status") or {}),
            raw=obj,
        )
        entity.kind = resource_info.kind  # type: ignore[assignment]
        return entity

    def to_text(self) -> str:
        parts = [
            f"kind={self.resource_kind_str}",
            f"apiVersion={self.api_version}",
            f"name={self.name}",
        ]
        if self.namespace:
            parts.append(f"namespace={self.namespace}")
        if self.labels:
            parts.append("labels=[" + " ".join(f"{k}={v}" for k, v in self.labels.items()) + "]")
        for key, val in self.spec_fields.items():
            parts.append(f"spec.{key}={val}")
        for key, val in self.status_fields.items():
            parts.append(f"status.{key}={val}")
        return " ".join(parts)


def _flatten(obj: Any, prefix: str = "", max_depth: int = 3) -> dict[str, str]:
    """Recursively flattens a nested dict into dot-notation keys, stopping at max_depth."""
    result: dict[str, str] = {}
    if max_depth == 0 or not isinstance(obj, dict):
        if not isinstance(obj, (dict, list)):
            result[prefix] = str(obj)
        return result
    for k, v in obj.items():
        full_key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            result.update(_flatten(v, full_key, max_depth - 1))
        elif isinstance(v, list):
            # Store list length and first element summary
            result[full_key] = f"[{len(v)} items]"
        else:
            result[full_key] = str(v) if v is not None else ""
    return result
