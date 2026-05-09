from __future__ import annotations
from collections import defaultdict, deque
from typing import TYPE_CHECKING, Iterator

from .entities import K8sEntity, ResourceKind
from .relationships import Edge, RelationshipType

if TYPE_CHECKING:
    from .version import KubeVersion


class OntologyGraph:
    """
    In-memory directed graph of K8s entities and their relationships.
    Nodes are K8sEntity instances keyed by UID.
    Edges are typed relationships between them.
    Carries the server KubeVersion so downstream consumers can adapt.
    """

    def __init__(self, server_version: "KubeVersion | None" = None) -> None:
        self.server_version = server_version
        self._nodes: dict[str, K8sEntity] = {}
        self._edges: list[Edge] = []
        # adjacency: uid → list of outgoing edges
        self._adj: dict[str, list[Edge]] = defaultdict(list)
        # reverse adjacency for inbound traversal
        self._radj: dict[str, list[Edge]] = defaultdict(list)

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def add_entity(self, entity: K8sEntity) -> None:
        self._nodes[entity.uid] = entity

    def add_edge(self, edge: Edge) -> None:
        self._edges.append(edge)
        self._adj[edge.source_uid].append(edge)
        self._radj[edge.target_uid].append(edge)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get(self, uid: str) -> K8sEntity | None:
        return self._nodes.get(uid)

    def entities(self, kind: ResourceKind | None = None) -> Iterator[K8sEntity]:
        for entity in self._nodes.values():
            if kind is None or entity.kind == kind:
                yield entity

    def neighbors(
        self,
        uid: str,
        rel_type: RelationshipType | None = None,
        reverse: bool = False,
    ) -> list[K8sEntity]:
        edges = self._radj[uid] if reverse else self._adj[uid]
        result = []
        for edge in edges:
            if rel_type and edge.rel_type != rel_type:
                continue
            target_uid = edge.source_uid if reverse else edge.target_uid
            node = self._nodes.get(target_uid)
            if node:
                result.append(node)
        return result

    def bfs(
        self,
        start_uid: str,
        max_depth: int = 3,
        rel_types: set[RelationshipType] | None = None,
    ) -> list[K8sEntity]:
        """
        Breadth-first traversal from start_uid up to max_depth hops.
        Returns all reachable entities (excluding the start node itself).
        """
        visited: set[str] = {start_uid}
        queue: deque[tuple[str, int]] = deque([(start_uid, 0)])
        result: list[K8sEntity] = []

        while queue:
            uid, depth = queue.popleft()
            if depth >= max_depth:
                continue
            for edge in self._adj[uid]:
                if rel_types and edge.rel_type not in rel_types:
                    continue
                if edge.target_uid not in visited:
                    visited.add(edge.target_uid)
                    node = self._nodes.get(edge.target_uid)
                    if node:
                        result.append(node)
                        queue.append((edge.target_uid, depth + 1))

        return result

    def subgraph_for_incident(self, seed_uids: list[str], depth: int = 2) -> list[K8sEntity]:
        """
        Given a list of unhealthy resource UIDs, expand the graph around them
        to collect all related entities — the context window for RCA.
        """
        seen: set[str] = set()
        collected: list[K8sEntity] = []
        for uid in seed_uids:
            for entity in self.bfs(uid, max_depth=depth):
                if entity.uid not in seen:
                    seen.add(entity.uid)
                    collected.append(entity)
        return collected

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    @property
    def node_count(self) -> int:
        return len(self._nodes)

    @property
    def edge_count(self) -> int:
        return len(self._edges)

    def summary(self) -> str:
        kind_counts: dict[str, int] = defaultdict(int)
        for entity in self._nodes.values():
            kind_str = entity.kind.value if hasattr(entity.kind, "value") else str(entity.kind)
            kind_counts[kind_str] += 1
        version_str = str(self.server_version) if self.server_version else "unknown"
        lines = [
            f"OntologyGraph [K8s {version_str}]: "
            f"{self.node_count} nodes, {self.edge_count} edges"
        ]
        for kind, count in sorted(kind_counts.items()):
            lines.append(f"  {kind}: {count}")
        return "\n".join(lines)
