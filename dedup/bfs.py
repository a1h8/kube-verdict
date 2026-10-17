from __future__ import annotations
import logging

from ontology.entities import K8sEntity, ResourceKind
from ontology.graph import OntologyGraph
from ontology.relationships import RelationshipType

import config as cfg

log = logging.getLogger(__name__)

# Relationships that are useful for incident propagation
_INCIDENT_RELS = {
    RelationshipType.OWNS,
    RelationshipType.RUNS_ON,
    RelationshipType.IN_NAMESPACE,
    RelationshipType.EXPOSES,
    RelationshipType.ROUTES_TO,
    RelationshipType.MOUNTS_CONFIGMAP,
    RelationshipType.MOUNTS_SECRET,
    RelationshipType.USES_PVC,
    RelationshipType.USES_SERVICE_ACCOUNT,
    RelationshipType.MANAGED_BY_HELM,
    RelationshipType.HAS_EVENT,
}


def _in_rollout(pod, rolling: set[tuple[str | None, str]]) -> bool:
    """The pod's ReplicaSet belongs to a Deployment that is rolling out."""
    if pod.owner_ref_kind != "ReplicaSet" or not pod.owner_ref_name:
        return False
    deployment = pod.owner_ref_name.rsplit("-", 1)[0]
    return (pod.namespace, deployment) in rolling


def find_unhealthy(graph: OntologyGraph) -> list[K8sEntity]:
    """
    Returns entities that are in a degraded or failed state.
    These are the seeds for BFS context expansion.
    """
    seeds: list[K8sEntity] = []

    # A pod is a seed when it is not Running or a container is not ready. A past restart
    # alone is not: a pod that restarted and is Running and ready again is healthy
    # (docs/reasoning-path-tests.md P1, P2). A pod that flaps but is ready at snapshot time
    # is missed — seeing it needs the graph's history.
    # A not-ready pod is skipped only when it is starting cleanly (running, never restarted)
    # inside a rollout Kubernetes reports as progressing while the Deployment stays
    # available (docs/test-cases.md § h026). Progressing stays True for up to
    # progressDeadlineSeconds even when the new pods crash, so a crashing or restarted pod
    # is always a seed.
    rolling = {(d.namespace, d.name) for d in graph.entities(ResourceKind.DEPLOYMENT)
               if d.is_rolling_out and d.is_available}
    for entity in graph.entities(ResourceKind.POD):
        if entity.is_unhealthy:
            seeds.append(entity)
        elif entity.is_not_ready and not (entity.is_starting and _in_rollout(entity, rolling)):
            seeds.append(entity)

    for entity in graph.entities(ResourceKind.DEPLOYMENT):
        if entity.is_degraded:
            seeds.append(entity)

    for entity in graph.entities(ResourceKind.STATEFULSET):
        if entity.ready_replicas < entity.replicas:
            seeds.append(entity)

    for entity in graph.entities(ResourceKind.DAEMONSET):
        if entity.ready < entity.desired:
            seeds.append(entity)

    for entity in graph.entities(ResourceKind.NODE):
        if not entity.ready:
            seeds.append(entity)

    for entity in graph.entities(ResourceKind.PERSISTENT_VOLUME):
        if entity.status_phase not in ("Bound", "Available"):
            seeds.append(entity)

    for entity in graph.entities(ResourceKind.PERSISTENT_VOLUME_CLAIM):
        if entity.status_phase != "Bound":
            seeds.append(entity)

    for entity in graph.entities(ResourceKind.RESOURCE_QUOTA):
        if entity.exhausted_resources or entity.near_limit_resources:
            seeds.append(entity)

    # Warning events are always seeds
    for entity in graph.entities(ResourceKind.EVENT):
        if entity.is_warning:
            seeds.append(entity)

    log.info("Found %d unhealthy/warning seed entities", len(seeds))
    return seeds


def expand_incident_context(
    graph: OntologyGraph,
    seeds: list[K8sEntity] | None = None,
    extra_uids: list[str] | None = None,
    max_depth: int | None = None,
) -> list[K8sEntity]:
    """
    BFS from every seed entity, following incident-relevant relationships.
    Returns a deduplicated list of all entities in the expanded neighbourhood.

    seeds       : explicit seed entities (default: find_unhealthy(graph))
    extra_uids  : additional UIDs to start from (e.g. from FAISS search hits)
    max_depth   : override BFS_MAX_DEPTH from config
    """
    depth = max_depth if max_depth is not None else cfg.BFS_MAX_DEPTH
    seed_list = seeds if seeds is not None else find_unhealthy(graph)

    all_uids: list[str] = [e.uid for e in seed_list]
    if extra_uids:
        all_uids.extend(extra_uids)

    if not all_uids:
        log.warning("No seed UIDs — returning all entities as fallback")
        return list(graph.entities())

    seen: set[str] = set()
    result: list[K8sEntity] = []

    # Always include the seeds themselves
    for uid in all_uids:
        entity = graph.get(uid)
        if entity and uid not in seen:
            seen.add(uid)
            result.append(entity)

    # BFS expansion
    for uid in all_uids:
        for neighbour in graph.bfs(uid, max_depth=depth, rel_types=_INCIDENT_RELS):
            if neighbour.uid not in seen:
                seen.add(neighbour.uid)
                result.append(neighbour)

    log.info(
        "BFS expansion: %d seeds → %d context entities (depth=%d)",
        len(all_uids), len(result), depth,
    )
    return result
