"""
AnchorEngine — collects expected/declared values from three sources and
writes them as `anchor.*` annotations on OntologyGraph entities.

Sources (applied in order, higher-priority sources overwrite lower):
  1. K8s schema defaults  — known defaults, valid values, descriptions
  2. Helm / Helmfile       — declared values from HelmRelease.values
  3. Rendered manifests    — exact values from `helm template` output
                             (requires a GitProvider; skipped if absent)

Annotation format on entities:
  anchor.spec.replicas           → "declared=3 source=helm"
  anchor.spec.strategy.type      → "k8s_default=RollingUpdate valid=RollingUpdate|Recreate"
  anchor.container.api.image     → "declared=nginx:1.25-alpine source=manifest"

These annotations feed into:
  - ContextBuilder  →  ### ANCHORS section in the LLM prompt
  - RemediationEngine evidence_boosts (anchor drift raises rule weight)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ingestion.k8s_schema import FieldMeta, K8sApiSchema, schema_for_kind
from ingestion.manifest_renderer import ManifestRenderer
from ontology.entities import (
    K8sEntity,
    HelmfileEnvironment, HelmRelease,
    ResourceKind,
)
from ontology.graph import OntologyGraph

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Data model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AnchorRecord:
    """
    A single expected-value anchor for one field on one entity.
    """
    entity_uid:       str
    entity_kind:      str
    entity_name:      str
    entity_namespace: str
    field_path:       str   # e.g. "spec.replicas", "container.api.resources.limits.memory"
    declared_value:   str   # What the field is supposed to contain
    source:           str   # "manifest" | "k8s_defaults"
    valid_values:     list[str] = field(default_factory=list)
    k8s_default:      str = ""
    description:      str = ""
    severity_on_drift: str = "warning"

    # --- K8s-schema-only anchor (no declared value) -------------------------
    @classmethod
    def from_schema(
        cls,
        entity: K8sEntity,
        field_path: str,
        meta: FieldMeta,
    ) -> "AnchorRecord":
        return cls(
            entity_uid=entity.uid,
            entity_kind=getattr(entity.kind, "value", entity.kind),
            entity_name=entity.name,
            entity_namespace=entity.namespace or "",
            field_path=field_path,
            declared_value=meta.k8s_default,
            source="k8s_defaults",
            valid_values=list(meta.valid_values),
            k8s_default=meta.k8s_default,
            description=meta.description,
            severity_on_drift=meta.severity_on_drift,
        )

    def annotation_key(self) -> str:
        return f"anchor.{self.field_path}"

    def to_text(self) -> str:
        parts: list[str] = []
        if self.declared_value:
            parts.append(f"declared={self.declared_value!r} [{self.source}]")
        if self.k8s_default and self.source != "k8s_defaults":
            parts.append(f"k8s_default={self.k8s_default!r}")
        if self.valid_values:
            parts.append(f"valid={'|'.join(self.valid_values)}")
        if self.description:
            parts.append(self.description[:100])
        return f"{self.field_path}: " + " | ".join(parts)



# ─────────────────────────────────────────────────────────────────────────────
# Engine
# ─────────────────────────────────────────────────────────────────────────────

class AnchorEngine:
    """
    Collects declared-value anchors from two generic sources and writes them
    as ``anchor.*`` annotations on OntologyGraph entities.

    Sources (applied in priority order, higher wins on dedup):
      1. K8s schema defaults  — valid values and defaults from the K8s API spec
      2. Rendered manifests   — ``helm template`` output; the rendered manifest IS
                                the ground truth for what Helm will deploy, including
                                the full value hierarchy (chart < env < value_files < inline)

    Heuristic Helm-value → K8s-field mapping is intentionally absent: it only
    works for charts that follow community naming conventions, and it is made
    redundant by the rendered-manifest source.

    Parameters
    ----------
    renderer:    ManifestRenderer (injectable for tests; default: real one).
    api_schema:  K8sApiSchema (injectable for tests; default: embedded schema).
    """

    def __init__(
        self,
        renderer:   ManifestRenderer | None = None,
        api_schema: K8sApiSchema | None = None,
    ) -> None:
        self._renderer  = renderer  or ManifestRenderer()
        self._api_schema = api_schema or K8sApiSchema()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def collect(
        self,
        graph:        OntologyGraph,
        provider=None,          # GitProvider | None
        charts_path:  str = "charts",
    ) -> list[AnchorRecord]:
        """
        Collect all anchors and write them as annotations.
        Returns the complete list of AnchorRecord objects.
        """
        records: list[AnchorRecord] = []

        # Source 1 — K8s schema: valid values + defaults (always available)
        records += self._from_k8s_schema(graph)

        # Source 2 — Rendered manifests: helm template output (generic ground truth)
        if provider is not None:
            records += self._from_rendered_manifests(graph, provider, charts_path)

        # Deduplicate: highest-priority source wins for each (entity_uid, field_path)
        final = self._deduplicate(records)

        # Write anchor.* annotations onto entities
        self._annotate(graph, final)

        log.info(
            "AnchorEngine: %d anchor(s) on %d entity(ies)",
            len(final),
            len({r.entity_uid for r in final}),
        )
        return final

    # ------------------------------------------------------------------
    # Source 1: K8s defaults schema
    # ------------------------------------------------------------------

    def _from_k8s_schema(self, graph: OntologyGraph) -> list[AnchorRecord]:
        records: list[AnchorRecord] = []
        for entity in graph.entities():
            kind = getattr(entity.kind, "value", entity.kind)
            fields = self._api_schema.fields_for_kind(kind)
            if not fields:
                fields = schema_for_kind(kind)
            for fp, meta in fields.items():
                # Only write schema anchors if there's a default or valid values
                if meta.k8s_default or meta.valid_values:
                    records.append(AnchorRecord.from_schema(entity, fp, meta))
        return records

    # ------------------------------------------------------------------
    # Source 2: Rendered manifests
    # ------------------------------------------------------------------

    def _from_rendered_manifests(
        self,
        graph:       OntologyGraph,
        provider,
        charts_path: str,
    ) -> list[AnchorRecord]:
        records: list[AnchorRecord] = []
        local_root = provider.local_path()

        # Environment lookup for value_files layering
        env_map: dict[str, HelmfileEnvironment] = {
            e.name: e
            for e in graph.entities(ResourceKind.HELMFILE_ENV)
            if isinstance(e, HelmfileEnvironment)
        }

        for release in graph.entities(ResourceKind.HELM_RELEASE):
            if not isinstance(release, HelmRelease):
                continue

            chart_ref = self._resolve_chart(release, local_root, charts_path)
            if chart_ref is None:
                continue

            # Prepend env value_files so Helm sees them at lower priority than release files
            env_value_files: list[str] = []
            if release.environment and release.environment in env_map:
                env = env_map[release.environment]
                local = provider.local_path()
                if local:
                    from pathlib import Path
                    root = Path(str(local))
                    env_value_files = [
                        str(root / vf)
                        for vf in (env.value_files or [])
                        if (root / vf).is_file()
                    ]

            all_value_files = env_value_files + (release.value_files or [])

            manifests = self._renderer.render(
                chart=chart_ref,
                release_name=release.name,
                namespace=release.namespace or "default",
                values=release.values,
                value_files=all_value_files or None,
            )

            for manifest in (manifests or []):
                records += self._anchors_from_manifest(manifest, graph)

        return records

    def _anchors_from_manifest(
        self, manifest: dict, graph: OntologyGraph
    ) -> list[AnchorRecord]:
        """Extract anchor field→value pairs from a single K8s manifest."""
        records: list[AnchorRecord] = []
        kind  = manifest.get("kind", "")
        meta  = manifest.get("metadata", {})
        name  = meta.get("name", "")
        ns    = meta.get("namespace", "")
        spec  = manifest.get("spec", {})

        entity = _find_entity(graph, kind, name, ns)
        if entity is None:
            return records

        extracted = _extract_manifest_fields(kind, spec)
        for fp, value in extracted.items():
            schema_meta = self._api_schema.get(kind, fp) or schema_for_kind(kind).get(fp)
            records.append(AnchorRecord(
                entity_uid=entity.uid,
                entity_kind=kind,
                entity_name=name,
                entity_namespace=ns,
                field_path=fp,
                declared_value=value,
                source="manifest",
                valid_values=list(schema_meta.valid_values) if schema_meta else [],
                k8s_default=schema_meta.k8s_default if schema_meta else "",
                description=schema_meta.description if schema_meta else "",
                severity_on_drift=schema_meta.severity_on_drift if schema_meta else "warning",
            ))
        return records

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_chart(
        release: HelmRelease,
        local_root,
        charts_path: str,
    ) -> str | None:
        if local_root is None:
            return None
        from pathlib import Path
        root = Path(str(local_root))
        for candidate in (release.chart, release.name):
            if candidate:
                chart_dir = root / charts_path / candidate
                if (chart_dir / "Chart.yaml").is_file():
                    return str(chart_dir)
        return None

    @staticmethod
    def _deduplicate(records: list[AnchorRecord]) -> list[AnchorRecord]:
        """
        When multiple records exist for the same (entity_uid, field_path),
        keep the highest-priority source: manifest > k8s_defaults.
        """
        _PRIORITY = {"manifest": 2, "k8s_defaults": 1}
        best: dict[tuple[str, str], AnchorRecord] = {}
        for r in records:
            key = (r.entity_uid, r.field_path)
            existing = best.get(key)
            if existing is None or (
                _PRIORITY.get(r.source, 0) > _PRIORITY.get(existing.source, 0)
            ):
                best[key] = r
        return list(best.values())

    @staticmethod
    def _annotate(graph: OntologyGraph, records: list[AnchorRecord]) -> None:
        uid_map: dict[str, K8sEntity] = {
            e.uid: e for e in graph.entities()
        }
        for r in records:
            entity = uid_map.get(r.entity_uid)
            if entity:
                entity.annotations[r.annotation_key()] = r.to_text()


# ─────────────────────────────────────────────────────────────────────────────
# Manifest field extraction
# ─────────────────────────────────────────────────────────────────────────────

def _extract_manifest_fields(kind: str, spec: dict) -> dict[str, str]:
    """
    Extract relevant field paths and their values from a K8s resource spec.
    Returns a flat {field_path: str_value} dict.
    """
    out: dict[str, str] = {}

    # spec.replicas (Deployment, StatefulSet)
    if "replicas" in spec:
        out["spec.replicas"] = str(spec["replicas"])

    # spec.strategy
    strategy = spec.get("strategy", {})
    if "type" in strategy:
        out["spec.strategy.type"] = strategy["type"]
    ru = strategy.get("rollingUpdate", {})
    if "maxSurge" in ru:
        out["spec.strategy.rollingUpdate.maxSurge"] = str(ru["maxSurge"])
    if "maxUnavailable" in ru:
        out["spec.strategy.rollingUpdate.maxUnavailable"] = str(ru["maxUnavailable"])

    # spec.progressDeadlineSeconds, spec.revisionHistoryLimit
    for simple_field in ("progressDeadlineSeconds", "revisionHistoryLimit",
                          "minReadySeconds", "podManagementPolicy"):
        if simple_field in spec:
            key = f"spec.{simple_field[0].lower()}{simple_field[1:]}"
            out[key] = str(spec[simple_field])

    # spec.paused
    if "paused" in spec:
        out["spec.paused"] = str(spec["paused"]).lower()

    # Pod / Deployment containers
    pod_spec = spec.get("template", {}).get("spec", {}) if kind != "Pod" else spec
    for c in pod_spec.get("containers", []):
        name = c.get("name", "container")
        if c.get("image"):
            out[f"container.{name}.image"] = c["image"]
        if c.get("imagePullPolicy"):
            out[f"container.{name}.imagePullPolicy"] = c["imagePullPolicy"]
        res = c.get("resources", {})
        for category in ("limits", "requests"):
            for res_field, val in (res.get(category) or {}).items():
                out[f"container.{name}.resources.{category}.{res_field}"] = str(val)

    # nodeSelector
    node_sel = pod_spec.get("nodeSelector", {})
    if node_sel:
        out["spec.nodeSelector"] = str(node_sel)

    # Service
    if kind == "Service":
        for sf in ("type", "sessionAffinity", "externalTrafficPolicy", "clusterIP"):
            if sf in spec:
                out[f"spec.{sf}"] = str(spec[sf])

    # PVC
    if kind == "PersistentVolumeClaim":
        if spec.get("accessModes"):
            out["spec.accessModes"] = "|".join(spec["accessModes"])
        if spec.get("volumeMode"):
            out["spec.volumeMode"] = spec["volumeMode"]
        if spec.get("storageClassName"):
            out["spec.storageClassName"] = spec["storageClassName"]
        storage = (spec.get("resources") or {}).get("requests", {}).get("storage")
        if storage:
            out["spec.resources.requests.storage"] = str(storage)

    return out


def _find_entity(
    graph: OntologyGraph,
    kind:  str,
    name:  str,
    ns:    str,
) -> K8sEntity | None:
    """Find a graph entity by kind, name, namespace."""
    _KIND_MAP = {
        "Deployment": ResourceKind.DEPLOYMENT,
        "StatefulSet": ResourceKind.STATEFULSET,
        "DaemonSet": ResourceKind.DAEMONSET,
        "Pod": ResourceKind.POD,
        "Service": ResourceKind.SERVICE,
        "ConfigMap": ResourceKind.CONFIGMAP,
        "PersistentVolumeClaim": ResourceKind.PERSISTENT_VOLUME_CLAIM,
    }
    rk = _KIND_MAP.get(kind)
    if rk is None:
        return None
    for entity in graph.entities(rk):
        if entity.name == name and (entity.namespace or "") == (ns or ""):
            return entity
    return None
