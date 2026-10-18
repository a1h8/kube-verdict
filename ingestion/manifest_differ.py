"""
ManifestDiffer — diffs Helm-rendered manifests against live K8s entities.

Detects:
  MISSING   rendered resource not found in the cluster       (critical)
  ORPHANED  cluster resource not in rendered manifest         (warning)
  REPLICAS  spec.replicas mismatch                            (warning/critical)
  IMAGE     container image tag mismatch                      (warning)
  RESOURCES CPU/memory request or limit mismatch              (info)
  ENV       selected environment variable value mismatch      (info)

Results are written as `<prefix>.*` annotations on matched entities and returned
as a flat list of DriftItem for upstream consumers. Two prefixes (docs/three-states.md):
  applied.*  the manifest Helm applied vs the live object — edited outside Helm
  gitops.*   Git's render vs the manifest Helm applied — deployed ≠ Git
"""
from __future__ import annotations

import logging

from ontology.entities import Deployment, DriftItem, K8sEntity, StatefulSet
from ontology.graph import OntologyGraph
from ontology.relationships import RelationshipType

log = logging.getLogger(__name__)

# Resource kinds we track for missing/orphan detection
_TRACKED_KINDS = frozenset({
    "Deployment", "StatefulSet", "DaemonSet",
    "Service", "Ingress", "ServiceAccount",
    "ConfigMap", "PersistentVolumeClaim",
})

# Env var names that are high-value to diff (add more as needed)
_SENSITIVE_ENV_KEYS = frozenset({
    "DATABASE_URL", "REDIS_URL", "KAFKA_BROKERS",
    "LOG_LEVEL", "REPLICAS", "PORT", "DB_HOST", "DB_PORT",
})
# Probe fields compared when the rendered side sets them (Kubernetes fills the others in)
_PROBES = ("livenessProbe", "readinessProbe", "startupProbe")
_PROBE_FIELDS = ("timeoutSeconds", "periodSeconds", "failureThreshold", "initialDelaySeconds",
                 "successThreshold")


class ManifestDiffer:
    """
    Compare rendered Kubernetes manifests against the OntologyGraph.

    Parameters
    ----------
    track_orphans:  Also flag cluster resources absent from rendered output.
    """

    def __init__(self, track_orphans: bool = True) -> None:
        self.track_orphans = track_orphans

    def diff(
        self,
        rendered: list[dict],
        graph: OntologyGraph,
        release_uid: str = "",
        namespace: str = "",
        prefix: str = "gitops",
        covered=None,
    ) -> list[DriftItem]:
        """
        Returns all DriftItems found.

        *covered(kind, namespace)* says whether that kind was listed there: an object of a kind
        that was not (a refused listing, a fixture without it) is unknown, not missing
        (docs/three-states.md, rule 4). None = every kind was listed.
        Side-effect: writes gitops.* annotations onto entities.

        *namespace* is the release namespace: `helm template` often leaves
        `metadata.namespace` out, and a rendered object is compared only with the live
        object of its own namespace (docs/fix-plan-001.md, H29).
        """
        default_ns = namespace
        all_drifts: list[DriftItem] = []
        matched_uids: set[str] = set()

        for manifest in rendered:
            kind = manifest.get("kind", "")
            meta = manifest.get("metadata", {})
            name = meta.get("name", "")
            namespace = meta.get("namespace") or default_ns

            entity = _find_entity(graph, kind, name, namespace)

            if entity is None:
                if kind in _TRACKED_KINDS and (covered is None or covered(kind, namespace)):
                    item = DriftItem(
                        field_path=f"{kind}.{namespace}.{name}",
                        declared="present",
                        observed="missing",
                        severity="critical",
                        source="gitops",
                    )
                    all_drifts.append(item)
                    _annotate_release(graph, release_uid,
                                      f"{prefix}.missing.{kind}.{name}", item.to_text())
                    log.info("gitops: %s/%s/%s rendered but absent from cluster",
                             kind, namespace, name)
                continue

            matched_uids.add(entity.uid)
            drifts = self._compare(manifest, entity)
            if any(e.rel_type == RelationshipType.SCALES for e in graph._radj.get(entity.uid, [])):
                # an HPA owns the replica count: not a drift (docs/three-states.md, rule 3)
                drifts = [d for d in drifts if d.field_path != "spec.replicas"]
            for d in drifts:
                all_drifts.append(d)
                entity.annotations[f"{prefix}.{d.field_path}"] = d.to_text()
                log.info("gitops drift on %s: %s", entity.uid, d.to_text())

        # Orphan detection: tracked kinds in cluster but not in rendered output
        if self.track_orphans:
            for entity in graph.entities():
                kind_val = getattr(entity.kind, "value", entity.kind)
                if (kind_val in _TRACKED_KINDS
                        and entity.uid not in matched_uids
                        and entity.labels.get("app.kubernetes.io/managed-by") == "Helm"):
                    item = DriftItem(
                        field_path=f"{kind_val}.{entity.namespace}.{entity.name}",
                        declared="absent",
                        observed="present",
                        severity="warning",
                        source="gitops",
                    )
                    all_drifts.append(item)
                    entity.annotations[f"{prefix}.orphan"] = item.to_text()

        return all_drifts

    def diff_manifests(
        self,
        declared: list[dict],
        applied: list[dict],
        graph: OntologyGraph,
        release_uid: str = "",
        namespace: str = "",
        prefix: str = "gitops",
    ) -> list[DriftItem]:
        """Git's render (*declared*) against the manifest Helm applied (*applied*); each drift
        is annotated on the live object of the same kind / name / namespace (or the release)."""
        def key(doc):
            meta = doc.get("metadata") or {}
            return doc.get("kind", ""), meta.get("namespace") or namespace, meta.get("name", "")
        applied_by_key = {key(d): d for d in applied if isinstance(d, dict)}
        out: list[DriftItem] = []
        for doc in declared:
            if not isinstance(doc, dict):
                continue
            kind, ns, name = key(doc)
            target = _find_entity(graph, kind, name, ns)
            other = applied_by_key.get((kind, ns, name))
            if other is None:
                if kind in _TRACKED_KINDS:
                    item = DriftItem(field_path=f"{kind}.{ns}.{name}", declared="present",
                                     observed="absent from the applied release", severity="warning",
                                     source="gitops")
                    out.append(item)
                    _annotate_release(graph, release_uid, f"{prefix}.missing.{kind}.{name}",
                                      item.to_text())
                continue
            for d in self._compare(doc, entity_from_manifest(other, ns)):
                out.append(d)
                if target is not None:
                    target.annotations[f"{prefix}.{d.field_path}"] = d.to_text()
                else:
                    _annotate_release(graph, release_uid, f"{prefix}.{kind}.{name}.{d.field_path}",
                                      d.to_text())
        return out

    # ------------------------------------------------------------------

    def _compare(self, manifest: dict, entity: K8sEntity) -> list[DriftItem]:
        kind = manifest.get("kind", "")
        spec = manifest.get("spec", {})
        drifts: list[DriftItem] = []

        # Replica-bearing kinds
        if kind in ("Deployment", "StatefulSet"):
            drifts += _diff_replicas(spec, entity)

        # Pod template containers (Deployment/StatefulSet) or bare Pod
        if kind == "Pod":
            drifts += _diff_containers(spec, entity)
        else:
            pod_spec = spec.get("template", {}).get("spec", {})
            if pod_spec:
                drifts += _diff_containers(pod_spec, entity)

        # Service ports
        if kind == "Service":
            drifts += _diff_service_ports(spec, entity)

        return drifts


# ─────────────────────────────────────────────────────────────────────────────
# Field-level diff helpers
# ─────────────────────────────────────────────────────────────────────────────

def _diff_replicas(spec: dict, entity: K8sEntity) -> list[DriftItem]:
    rendered = spec.get("replicas")
    if rendered is None or not hasattr(entity, "replicas"):
        return []
    observed = entity.replicas
    if rendered == observed:
        return []
    delta = abs(rendered - observed)
    return [DriftItem(
        field_path="spec.replicas",
        declared=rendered,
        observed=observed,
        severity="critical" if delta > 1 else "warning",
        source="gitops",
    )]


def _diff_containers(pod_spec: dict, entity: K8sEntity) -> list[DriftItem]:
    drifts: list[DriftItem] = []
    containers: list[dict] = pod_spec.get("containers") or []

    # Build map of running container state.
    # Pods: use container_statuses (image = currently running tag).
    # Deployments/StatefulSets: use spec.container.<name>.image annotations
    # written by K8sCollector from the live deployment spec.
    running: dict[str, dict] = {}
    if hasattr(entity, "container_statuses") and entity.container_statuses:
        for cs in entity.container_statuses:
            if isinstance(cs, dict):
                running[cs.get("name", "")] = cs
    for key, val in entity.annotations.items():
        if key.startswith("spec.container.") and key.endswith(".image"):
            cname = key[len("spec.container."):-len(".image")]
            if cname not in running:
                running[cname] = {"image": val}

    for c in containers:
        name = c.get("name", "")
        rendered_image = c.get("image", "")

        # Image drift
        observed_image = (running.get(name) or {}).get("image", "")
        if rendered_image and observed_image and rendered_image != observed_image:
            drifts.append(DriftItem(
                field_path=f"container.{name}.image",
                declared=rendered_image,
                observed=observed_image,
                severity="warning",
                source="gitops",
            ))

        # Resource requests/limits
        drifts += _diff_resources(name, c.get("resources", {}), entity)

        # Probes: the fields the rendered side sets (h009: a liveness timeout)
        drifts += _diff_probes(name, c, entity)

        # Selected env vars
        drifts += _diff_env(name, c.get("env") or [], entity)

    return drifts


def _diff_resources(
    c_name: str, rendered_resources: dict, entity: K8sEntity
) -> list[DriftItem]:
    """Rendered limits vs live limits, rendered requests vs live requests, read from the live
    pod-template container (``Deployment.containers``). docs/case-audit-001.md § Correction F9:
    the observed side was an annotation no production code wrote, and limits and requests
    shared one key."""
    live = next((c for c in getattr(entity, "containers", []) or [] if c.get("name") == c_name), None)
    if live is None:
        return []
    live_resources = live.get("resources") or {}
    drifts: list[DriftItem] = []
    for category in ("limits", "requests"):
        rendered = (rendered_resources or {}).get(category) or {}
        observed = live_resources.get(category) or {}
        for field, rendered_val in rendered.items():
            observed_val = observed.get(field)
            if observed_val is not None and str(rendered_val) != str(observed_val):
                drifts.append(DriftItem(
                    field_path=f"container.{c_name}.resources.{category}.{field}",
                    declared=rendered_val,
                    observed=observed_val,
                    severity="warning" if category == "limits" else "info",
                    source="gitops",
                ))
    return drifts


def _diff_env(c_name: str, rendered_env: list[dict], entity: K8sEntity) -> list[DriftItem]:
    drifts: list[DriftItem] = []
    for entry in rendered_env:
        var_name = entry.get("name", "")
        if var_name not in _SENSITIVE_ENV_KEYS:
            continue
        rendered_val = entry.get("value")
        if rendered_val is None:
            continue
        # the live container's env (docs/fix-plan-001.md H1: the env.* annotation this read was
        # written by no production code)
        live = _live_container(entity, c_name) or {}
        observed_val = next((e.get("value") for e in live.get("env") or []
                             if e.get("name") == var_name), None)
        if observed_val is not None and str(rendered_val) != str(observed_val):
            drifts.append(DriftItem(
                field_path=f"container.{c_name}.env.{var_name}",
                declared=rendered_val,
                observed=observed_val,
                severity="info",
                source="gitops",
            ))
    return drifts


def _live_container(entity: K8sEntity, c_name: str) -> dict | None:
    return next((c for c in getattr(entity, "containers", []) or []
                 if isinstance(c, dict) and c.get("name") == c_name), None)


def _diff_probes(c_name: str, rendered: dict, entity: K8sEntity) -> list[DriftItem]:
    live = _live_container(entity, c_name)
    if live is None:
        return []
    drifts: list[DriftItem] = []
    for probe in _PROBES:
        want, have = rendered.get(probe) or {}, live.get(probe) or {}
        for field in _PROBE_FIELDS:
            if field in want and field in have and str(want[field]) != str(have[field]):
                drifts.append(DriftItem(field_path=f"container.{c_name}.{probe}.{field}",
                                        declared=want[field], observed=have[field],
                                        severity="warning", source="gitops"))
    return drifts


def entity_from_manifest(doc: dict, namespace: str = "") -> K8sEntity:
    """A manifest as the entity the differ compares against (the applied side of A)."""
    meta, spec = doc.get("metadata") or {}, doc.get("spec") or {}
    ns, name = meta.get("namespace") or namespace, meta.get("name", "")
    containers = list(((spec.get("template") or {}).get("spec") or {}).get("containers") or [])
    cls = StatefulSet if doc.get("kind") == "StatefulSet" else Deployment
    e = cls(uid=f"applied:{doc.get('kind')}/{ns}/{name}", name=name, namespace=ns,
            labels=meta.get("labels") or {}, replicas=spec.get("replicas") or 0)
    if cls is Deployment:
        e.containers = containers
    for c in containers:
        if c.get("image"):
            e.annotations[f"spec.container.{c.get('name')}.image"] = c["image"]
    return e


def _diff_service_ports(spec: dict, entity: K8sEntity) -> list[DriftItem]:
    drifts: list[DriftItem] = []
    rendered_ports: list[dict] = spec.get("ports") or []
    if not rendered_ports or not hasattr(entity, "ports"):
        return drifts
    observed_ports: list[dict] = entity.ports or []
    observed_set = {(p.get("port"), p.get("protocol", "TCP")) for p in observed_ports}
    for p in rendered_ports:
        key = (p.get("containerPort") or p.get("port"), p.get("protocol", "TCP"))
        if key not in observed_set:
            drifts.append(DriftItem(
                field_path=f"spec.ports.{key[0]}",
                declared=str(key),
                observed="missing",
                severity="warning",
                source="gitops",
            ))
    return drifts


# ─────────────────────────────────────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────────────────────────────────────

def _find_entity(
    graph: OntologyGraph, kind: str, name: str, namespace: str
) -> K8sEntity | None:
    for entity in graph.entities():
        if entity.name != name:
            continue
        if entity.kind.value != kind:
            continue
        if entity.namespace and entity.namespace != namespace:
            continue                       # a namespaced object of another (or unknown) namespace
        return entity
    return None


def _annotate_release(
    graph: OntologyGraph, release_uid: str, key: str, value: str
) -> None:
    if not release_uid:
        return
    entity = graph.get(release_uid)
    if entity:
        entity.annotations[key] = value


def compare_applied_to_live(graph: OntologyGraph, covered=None) -> tuple[int, int]:
    """B for every release whose applied manifest was read: (releases compared, drifts).
    *covered* as in ``ManifestDiffer.diff``."""
    from ontology.entities import ResourceKind
    releases = drifts = 0
    for rel in graph.entities(ResourceKind.HELM_RELEASE):
        applied = getattr(rel, "applied_manifest", None)
        if applied:
            releases += 1
            drifts += len(ManifestDiffer(track_orphans=False).diff(
                applied, graph, release_uid=rel.uid, namespace=rel.namespace or "default",
                prefix="applied", covered=covered))
    return releases, drifts
