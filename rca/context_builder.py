from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import config as cfg
from dedup.bfs import expand_incident_context, find_unhealthy
from dedup.jaccard import jaccard_deduplicate
from dedup.tfidf import tfidf_rank
from ontology.entities import K8sEntity, LokiLog, OtelTrace, PrometheusAlert, ResourceKind
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
    examples: list[str] = field(default_factory=list)        # similar resolved incidents
    alerts: list[str] = field(default_factory=list)          # firing Prometheus alerts
    traces: list[str] = field(default_factory=list)          # OTel error traces
    logs: list[str] = field(default_factory=list)            # Loki error/warn logs
    events: list[str] = field(default_factory=list)          # Warning K8s events
    anchors: list[str] = field(default_factory=list)         # declared values + K8s schema
    anchor_fixes: list[str] = field(default_factory=list)    # helm commands to restore declared values
    helm: list[str] = field(default_factory=list)            # releases + charts
    related: list[str] = field(default_factory=list)         # BFS neighbourhood

    # raw entity refs for metadata
    seed_entities: list[K8sEntity] = field(default_factory=list, repr=False)

    @property
    def total_chunks(self) -> int:
        return (
            len(self.seeds)
            + len(self.drift)
            + len(self.examples)
            + len(self.alerts)
            + len(self.traces)
            + len(self.logs)
            + len(self.events)
            + len(self.anchors)
            + len(self.anchor_fixes)
            + len(self.helm)
            + len(self.related)
        )

    def to_prompt_block(self) -> str:
        lines: list[str] = []

        if self.seeds:
            lines.append(f"### CRITICAL — Unhealthy resources ({len(self.seeds)})")
            lines.extend(f"  - {t}" for t in self.seeds)

        if self.drift:
            lines.append(
                f"### CRITICAL — Helm declared vs observed drift ({len(self.drift)})"
            )
            lines.extend(f"  - {t}" for t in self.drift)

        if self.examples:
            lines.append(
                f"### SIMILAR PAST INCIDENTS — proven remediations "
                f"({len(self.examples)})"
            )
            lines.extend(f"  - {t}" for t in self.examples)

        if self.anchor_fixes:
            lines.append(
                f"### ANCHOR FIX SUGGESTIONS — helm commands to restore "
                f"declared values ({len(self.anchor_fixes)})"
            )
            lines.extend(f"  - {t}" for t in self.anchor_fixes)

        if self.alerts:
            lines.append(f"### CRITICAL — Firing Prometheus alerts ({len(self.alerts)})")
            lines.extend(f"  - {t}" for t in self.alerts)

        if self.traces:
            lines.append(
                f"### TRACES — OpenTelemetry error traces ({len(self.traces)})"
            )
            lines.extend(f"  - {t}" for t in self.traces)

        if self.logs:
            lines.append(
                f"### LOGS — Recent error/warn log lines ({len(self.logs)})"
            )
            lines.extend(f"  - {t}" for t in self.logs)

        if self.events:
            lines.append(f"### WARNING — Kubernetes events ({len(self.events)})")
            lines.extend(f"  - {t}" for t in self.events)

        if self.anchors:
            lines.append(
                f"### ANCHORS — Declared values & K8s schema ({len(self.anchors)})"
            )
            lines.extend(f"  - {t}" for t in self.anchors)

        if self.helm:
            lines.append(f"### Helm / Helmfile releases ({len(self.helm)})")
            lines.extend(f"  - {t}" for t in self.helm)

        if self.related:
            lines.append(
                f"### Related context ({len(self.related)} chunks after dedup)"
            )
            lines.extend(f"  - {t}" for t in self.related)

        return "\n".join(lines)


# ── Anchor → Helm value mapping ───────────────────────────────────────────────

def _field_path_to_helm_key(field_path: str) -> str:
    """Best-effort mapping from anchor field_path to Helm --set key."""
    # container.NAME.resources.limits.X  →  resources.limits.X
    m = re.match(r"container\.\w+\.(resources\..+)", field_path)
    if m:
        return m.group(1)
    # container.NAME.image  →  image
    if re.match(r"container\.\w+\.image$", field_path):
        return "image"
    # container.NAME.imagePullPolicy  →  imagePullPolicy
    if re.match(r"container\.\w+\.imagePullPolicy", field_path):
        return "imagePullPolicy"
    # spec.replicas  →  replicaCount
    if field_path == "spec.replicas":
        return "replicaCount"
    # spec.X  →  X
    if field_path.startswith("spec."):
        return field_path[5:]
    return field_path


def anchor_fix_hints(graph: "OntologyGraph", seeds: list[K8sEntity]) -> list[str]:
    """
    Public: for each unhealthy entity with manifest-sourced anchors,
    generate an explicit helm command to restore the declared value.
    """
    hints: list[str] = []

    release_name_map: dict[tuple[str, str], str] = {}
    for hr in graph.entities(ResourceKind.HELM_RELEASE):
        release_name_map[(hr.namespace or "", hr.name)] = hr.name

    for entity in seeds:
        kind_str = (
            entity.kind.value if hasattr(entity.kind, "value") else str(entity.kind)
        )
        ns = entity.namespace or ""
        name = entity.name
        release = release_name_map.get((ns, name)) or name

        for ann_key, ann_val in sorted(entity.annotations.items()):
            if not ann_key.startswith("anchor."):
                continue
            if "[manifest]" not in ann_val:
                continue
            m = re.search(r"declared='?([^'\s|]+)'?\s*\[manifest\]", ann_val)
            if not m:
                continue
            declared_val = m.group(1)
            field_path = ann_key[len("anchor."):]
            helm_key = _field_path_to_helm_key(field_path)
            hints.append(
                f"{kind_str}/{ns}/{name}  {field_path}={declared_val!r} "
                f"(declared in chart)  →  helm upgrade {release} -n {ns} "
                f"--set {helm_key}={declared_val}"
            )

    return hints[:12]


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
        self._bfs_max_depth = (
            bfs_max_depth if bfs_max_depth is not None else cfg.BFS_MAX_DEPTH
        )

    def build(self, query: str) -> ContextWindow:
        ctx = ContextWindow()

        # --- Section 1: unhealthy seeds (verbatim, not ranked) ---------------
        seeds = find_unhealthy(self.graph)
        ctx.seed_entities = seeds
        ctx.seeds = [e.to_text() for e in seeds]
        seed_uids = {e.uid for e in seeds}

        # --- Section 1b: anchor fix hints (from manifest anchors on seeds) -----
        ctx.anchor_fixes = self._anchor_fix_hints(seeds)

        # --- Section 2: drift (verbatim) -------------------------------------
        drift_texts: list[str] = []
        drift_uids: set[str] = set()
        for entity in self.graph.entities():
            drifts = [
                v for k, v in entity.annotations.items()
                if k.startswith("drift.")
            ]
            if drifts:
                drift_uids.add(entity.uid)
                kind_str = (
                    entity.kind.value
                    if hasattr(entity.kind, "value")
                    else str(entity.kind)
                )
                header = f"{kind_str}/{entity.namespace}/{entity.name}"
                for d in drifts:
                    drift_texts.append(f"{header}: {d}")
        ctx.drift = drift_texts

        # --- Section 3: Firing Prometheus alerts (critical first) -------------
        alert_entities = [
            e
            for e in self.graph.entities(ResourceKind.PROMETHEUS_ALERT)
            if isinstance(e, PrometheusAlert) and e.state == "firing"
        ]
        alert_entities.sort(
            key=lambda a: (a.severity != "critical", a.severity != "warning")
        )
        ctx.alerts = [e.to_text() for e in alert_entities]
        alert_uids = {e.uid for e in alert_entities}

        # --- Section 4a: OTel error traces ------------------------------------
        trace_entities = [
            e
            for e in self.graph.entities(ResourceKind.OTEL_TRACE)
            if isinstance(e, OtelTrace) and e.status == "ERROR"
        ]
        ctx.traces = [e.to_text() for e in trace_entities[:20]]  # cap at 20
        trace_uids = {e.uid for e in trace_entities[:20]}

        # --- Section 4b: Loki error/warn logs ---------------------------------
        log_entities = [
            e
            for e in self.graph.entities(ResourceKind.LOKI_LOG)
            if isinstance(e, LokiLog) and e.level in ("error", "warn")
        ]
        ctx.logs = [e.to_text() for e in log_entities[:20]]  # cap at 20
        log_uids = {e.uid for e in log_entities[:20]}

        # --- Section 5: Warning events sorted by count desc ------------------
        events = sorted(
            [e for e in self.graph.entities(ResourceKind.EVENT) if e.is_warning],
            key=lambda e: e.count,
            reverse=True,
        )
        ctx.events = [e.to_text() for e in events[:15]]  # cap at 15 most frequent
        event_uids = {e.uid for e in events[:15]}

        # --- Section 5b: Anchors (declared values + K8s schema) ---------------
        priority = seed_uids | drift_uids
        anchor_texts: list[str] = []
        for entity in sorted(
            self.graph.entities(),
            key=lambda e: (0 if e.uid in priority else 1, e.name),
        ):
            prefix = (
                f"{entity.kind.value if hasattr(entity.kind, 'value') else entity.kind}"
                f"/{entity.namespace}/{entity.name}"
            )
            for k, v in sorted(entity.annotations.items()):
                if k.startswith("anchor."):
                    anchor_texts.append(f"{prefix}: {v}")
        ctx.anchors = anchor_texts[:30]

        # --- Section 6: Helm releases + charts --------------------------------
        helm_texts: list[str] = []
        helm_uids: set[str] = set()
        for entity in self.graph.entities(ResourceKind.HELM_RELEASE):
            helm_texts.append(entity.to_text())
            helm_uids.add(entity.uid)
        for entity in self.graph.entities(ResourceKind.HELM_CHART):
            helm_texts.append(entity.to_text())
            helm_uids.add(entity.uid)
        ctx.helm = helm_texts

        # --- Section 7: related context via FAISS + BFS + dedup + TF-IDF -----
        already_covered = (
            seed_uids | drift_uids | alert_uids | trace_uids | log_uids
            | event_uids | helm_uids
        )

        faiss_hits = self.store.search(query, top_k=cfg.TFIDF_TOP_K * 3)

        # Split example hits (resolved incidents) from entity hits
        example_hits = [h for h in faiss_hits if h["uid"].startswith("example:")]
        entity_hits = [h for h in faiss_hits if not h["uid"].startswith("example:")]
        ctx.examples = [h["text"] for h in example_hits[:5]]

        faiss_uids = [h["uid"] for h in entity_hits]

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
            "ContextWindow: %d seeds | %d drift | %d examples | %d alerts"
            " | %d traces | %d logs | %d events | %d anchors | %d anchor_fixes"
            " | %d helm | %d related",
            len(ctx.seeds),
            len(ctx.drift),
            len(ctx.examples),
            len(ctx.alerts),
            len(ctx.traces),
            len(ctx.logs),
            len(ctx.events),
            len(ctx.anchors),
            len(ctx.anchor_fixes),
            len(ctx.helm),
            len(ctx.related),
        )
        return ctx

    def _anchor_fix_hints(self, seeds: list[K8sEntity]) -> list[str]:
        return anchor_fix_hints(self.graph, seeds)
