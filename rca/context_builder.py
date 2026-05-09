from __future__ import annotations
import logging
from dataclasses import dataclass, field

import config as cfg
from dedup.bfs import expand_incident_context, find_unhealthy
from dedup.jaccard import jaccard_deduplicate
from dedup.tfidf import tfidf_rank
from ontology.entities import K8sEntity, ResourceKind
from ontology.graph import OntologyGraph
from vectorstore.store import FAISSStore

log = logging.getLogger(__name__)


@dataclass
class ContextWindow:
    """
    Structured context window built from the ontology graph.
    Organized in priority order so the LLM sees the most critical
    information first — regardless of token budget.
    """
    seeds: list[str] = field(default_factory=list)          # unhealthy resources
    drift: list[str] = field(default_factory=list)           # declared ≠ observed
    events: list[str] = field(default_factory=list)          # Warning K8s events
    helm: list[str] = field(default_factory=list)            # releases + charts
    related: list[str] = field(default_factory=list)         # BFS neighbourhood

    # raw entity refs for metadata
    seed_entities: list[K8sEntity] = field(default_factory=list, repr=False)

    @property
    def total_chunks(self) -> int:
        return (len(self.seeds) + len(self.drift) + len(self.events)
                + len(self.helm) + len(self.related))

    def to_prompt_block(self) -> str:
        lines: list[str] = []

        if self.seeds:
            lines.append(f"### CRITICAL — Unhealthy resources ({len(self.seeds)})")
            lines.extend(f"  - {t}" for t in self.seeds)

        if self.drift:
            lines.append(f"\n### CRITICAL — Helm declared vs observed drift ({len(self.drift)})")
            lines.extend(f"  - {t}" for t in self.drift)

        if self.events:
            lines.append(f"\n### WARNING — Kubernetes events ({len(self.events)})")
            lines.extend(f"  - {t}" for t in self.events)

        if self.helm:
            lines.append(f"\n### Helm / Helmfile releases ({len(self.helm)})")
            lines.extend(f"  - {t}" for t in self.helm)

        if self.related:
            lines.append(f"\n### Related context ({len(self.related)} chunks after dedup)")
            lines.extend(f"  - {t}" for t in self.related)

        return "\n".join(lines)


class ContextBuilder:
    """
    Builds a ContextWindow for a given incident query.

    Pipeline:
      1. Find unhealthy seed entities (always included verbatim)
      2. Extract drift-annotated entities (always included verbatim)
      3. Extract Warning events (always included, sorted by count desc)
      4. Extract Helm/Helmfile release context
      5. FAISS semantic search → BFS expansion → Jaccard dedup → TF-IDF rank
         for the "related" section (budget-capped)
    """

    def __init__(
        self,
        graph: OntologyGraph,
        store: FAISSStore,
        bfs_max_depth: int | None = None,
    ) -> None:
        self.graph = graph
        self.store = store
        self._bfs_max_depth = bfs_max_depth if bfs_max_depth is not None else cfg.BFS_MAX_DEPTH

    def build(self, query: str) -> ContextWindow:
        ctx = ContextWindow()

        # --- Section 1: unhealthy seeds (verbatim, not ranked) ---------------
        seeds = find_unhealthy(self.graph)
        ctx.seed_entities = seeds
        ctx.seeds = [e.to_text() for e in seeds]
        seed_uids = {e.uid for e in seeds}

        # --- Section 2: drift (verbatim) -------------------------------------
        drift_texts: list[str] = []
        drift_uids: set[str] = set()
        for entity in self.graph.entities():
            drifts = [v for k, v in entity.annotations.items() if k.startswith("drift.")]
            if drifts:
                drift_uids.add(entity.uid)
                kind_str = entity.kind.value if hasattr(entity.kind, "value") else str(entity.kind)
                header = f"{kind_str}/{entity.namespace}/{entity.name}"
                for d in drifts:
                    drift_texts.append(f"{header}: {d}")
        ctx.drift = drift_texts

        # --- Section 3: Warning events sorted by count desc ------------------
        events = sorted(
            [e for e in self.graph.entities(ResourceKind.EVENT) if e.is_warning],
            key=lambda e: e.count,
            reverse=True,
        )
        ctx.events = [e.to_text() for e in events[:15]]  # cap at 15 most frequent
        event_uids = {e.uid for e in events[:15]}

        # --- Section 4: Helm releases + charts --------------------------------
        helm_texts: list[str] = []
        helm_uids: set[str] = set()
        for entity in self.graph.entities(ResourceKind.HELM_RELEASE):
            helm_texts.append(entity.to_text())
            helm_uids.add(entity.uid)
        for entity in self.graph.entities(ResourceKind.HELM_CHART):
            helm_texts.append(entity.to_text())
            helm_uids.add(entity.uid)
        ctx.helm = helm_texts

        # --- Section 5: related context via FAISS + BFS + dedup + TF-IDF -----
        already_covered = seed_uids | drift_uids | event_uids | helm_uids

        faiss_hits = self.store.search(query, top_k=cfg.TFIDF_TOP_K * 3)
        faiss_uids = [h["uid"] for h in faiss_hits]

        bfs_entities = expand_incident_context(
            self.graph,
            seeds=seeds,
            extra_uids=faiss_uids,
            max_depth=self._bfs_max_depth,
        )

        candidate_texts: list[str] = []
        for entity in bfs_entities:
            if entity.uid not in already_covered:
                candidate_texts.append(entity.to_text())
        for hit in faiss_hits:
            if hit["uid"] not in already_covered:
                candidate_texts.append(hit["text"])

        # remove exact duplicates
        seen: set[str] = set()
        unique: list[str] = []
        for t in candidate_texts:
            if t not in seen:
                seen.add(t)
                unique.append(t)

        kept = jaccard_deduplicate(unique, threshold=cfg.JACCARD_THRESHOLD)
        deduped = [unique[i] for i in kept]
        top_idx = tfidf_rank(query, deduped, top_k=cfg.TFIDF_TOP_K)
        ctx.related = [deduped[i] for i in top_idx]

        log.info(
            "ContextWindow: %d seeds | %d drift | %d events | %d helm | %d related",
            len(ctx.seeds), len(ctx.drift), len(ctx.events),
            len(ctx.helm), len(ctx.related),
        )
        return ctx
