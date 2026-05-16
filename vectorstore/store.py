from __future__ import annotations
import logging
import pickle
from pathlib import Path
from typing import Any

import faiss

import config as cfg
from ontology.entities import K8sEntity
from ontology.graph import OntologyGraph
from vectorstore.bm25_retriever import BM25Retriever
from vectorstore.embedder import Embedder
from vectorstore.rrf import rrf_fuse

log = logging.getLogger(__name__)


class FAISSStore:
    """
    FAISS-backed vector store for K8s ontology entities.

    Each entity is stored as:
      - its L2-normalized embedding vector (float32)
      - its UID and to_text() in a parallel metadata list

    Search returns the top-k most semantically similar entities
    for a free-text query — the context window fed to the LLM.
    """

    def __init__(self, embedder: Embedder | None = None) -> None:
        self._embedder = embedder or Embedder()
        self._index: faiss.Index | None = None
        self._metadata: list[dict[str, Any]] = []  # parallel to index rows
        self._uid_to_row: dict[str, int] = {}
        self._bm25 = BM25Retriever()
        self._bm25_dirty: bool = False
        self._last_retrieval_stats: dict = {}

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------

    def index_graph(self, graph: OntologyGraph) -> None:
        """
        Embeds every entity in the graph and builds the FAISS index.
        Also records the server version as index-level metadata.
        """
        entities = list(graph.entities())
        if not entities:
            log.warning("Graph is empty — nothing to index.")
            return

        texts = [e.to_text() for e in entities]
        log.info("Embedding %d entities…", len(texts))
        vectors = self._embedder.embed(texts)

        self._index = faiss.IndexFlatIP(self._embedder.dim)
        self._index.add(vectors)

        self._metadata = [
            {
                "uid": e.uid,
                "name": e.name,
                "kind": e.kind.value if hasattr(e.kind, "value") else str(e.kind),
                "namespace": e.namespace,
                "text": texts[i],
                "kube_version": str(graph.server_version) if graph.server_version else "",
                "doc_source": "cluster",
            }
            for i, e in enumerate(entities)
        ]
        self._uid_to_row = {m["uid"]: i for i, m in enumerate(self._metadata)}
        self._bm25.build(self._metadata)
        self._bm25_dirty = False
        log.info("Index built: %d vectors (dim=%d)", self._index.ntotal, self._embedder.dim)

    def index_anchor_violations(self, graph: OntologyGraph) -> int:
        """
        Index manifest anchor violations as separate doc_source='anchor' documents.

        Each violated field (anchor.* annotation with [manifest] drift) is indexed
        as an individual entry so the RRF naturally boosts them via SOURCE_WEIGHTS["anchor"].
        Returns the number of anchor violation docs added.
        """
        count = 0
        for entity in graph.entities():
            ann = getattr(entity, "annotations", {}) or {}
            kind_str = entity.kind.value if hasattr(entity.kind, "value") else str(entity.kind)
            ns = entity.namespace or ""
            for ann_key, ann_val in ann.items():
                if not ann_key.startswith("anchor.") or "[manifest]" not in str(ann_val):
                    continue
                field_path = ann_key[len("anchor."):]
                uid = f"anchor:{entity.uid}:{field_path}"
                if uid in self._uid_to_row:
                    continue  # already indexed (idempotent)
                text = (
                    f"ANCHOR VIOLATION: {kind_str}/{ns}/{entity.name} "
                    f"field={field_path} {ann_val}"
                )
                vec = self._embedder.embed([text])
                if self._index is None:
                    self._index = faiss.IndexFlatIP(self._embedder.dim)
                self._index.add(vec)
                row = len(self._metadata)
                self._metadata.append({
                    "uid":         uid,
                    "name":        entity.name,
                    "kind":        kind_str,
                    "namespace":   ns,
                    "text":        text,
                    "kube_version": str(graph.server_version) if graph.server_version else "",
                    "doc_source":  "anchor",
                })
                self._uid_to_row[uid] = row
                self._bm25_dirty = True
                count += 1
        if count:
            log.info("index_anchor_violations: %d anchor violation doc(s) added", count)
        return count

    def add_entity(
        self,
        entity: K8sEntity,
        kube_version: str = "",
        doc_source: str = "cluster",
    ) -> None:
        """Incrementally add a single entity to the index."""
        text = entity.to_text()
        vec = self._embedder.embed([text])

        if self._index is None:
            self._index = faiss.IndexFlatIP(self._embedder.dim)

        self._index.add(vec)
        row = len(self._metadata)
        self._metadata.append({
            "uid": entity.uid,
            "name": entity.name,
            "kind": entity.kind.value if hasattr(entity.kind, "value") else str(entity.kind),
            "namespace": entity.namespace,
            "text": text,
            "kube_version": kube_version,
            "doc_source": doc_source,
        })
        self._uid_to_row[entity.uid] = row
        self._bm25_dirty = True

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(self, query: str, top_k: int | None = None) -> list[dict[str, Any]]:
        """
        Returns up to top_k metadata dicts for the entities most similar
        to the query string.  Each dict includes uid, name, kind, namespace,
        text, score.
        """
        if self._index is None or self._index.ntotal == 0:
            log.warning("Index is empty — run index_graph() first.")
            return []

        k = min(top_k or cfg.TFIDF_TOP_K, self._index.ntotal)
        q_vec = self._embedder.embed([query])
        scores, indices = self._index.search(q_vec, k)

        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0:
                continue
            entry = dict(self._metadata[idx])
            weight = cfg.SOURCE_WEIGHTS.get(entry.get("doc_source", "cluster"), 1.0)
            entry["score"] = float(score) * weight
            results.append(entry)

        results.sort(key=lambda x: x["score"], reverse=True)
        return results

    def _ensure_bm25(self) -> None:
        if self._bm25_dirty:
            self._bm25.build(self._metadata)
            self._bm25_dirty = False

    def hybrid_search(self, query: str, top_k: int | None = None) -> list[dict[str, Any]]:
        """
        Hybrid dense+sparse retrieval fused with Reciprocal Rank Fusion.

        1. FAISS cosine-similarity search  (dense)
        2. BM25Okapi keyword search        (sparse)
        3. RRF fusion of both ranked lists
        4. SOURCE_WEIGHTS applied to rrf_score

        Replaces plain search() in the ContextBuilder hot-path so that
        rare/exact K8s tokens (error codes, image tags, resource names)
        are not penalised by embedding distance alone.
        """
        if self._index is None or self._index.ntotal == 0:
            log.warning("Index is empty — run index_graph() first.")
            return []

        self._ensure_bm25()
        k = min(top_k or cfg.TFIDF_TOP_K, self._index.ntotal)
        fetch_k = k * cfg.RRF_FETCH_MULTIPLIER

        # --- dense ---
        q_vec = self._embedder.embed([query])
        scores, indices = self._index.search(q_vec, min(fetch_k, self._index.ntotal))
        dense_hits: list[dict[str, Any]] = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0:
                continue
            entry = dict(self._metadata[idx])
            entry["score"] = float(score)
            dense_hits.append(entry)

        # --- sparse ---
        sparse_hits = self._bm25.search(query, top_k=fetch_k)

        # --- fuse ---
        fused = rrf_fuse([dense_hits, sparse_hits], top_k=k, k=cfg.RRF_K)

        # apply source weights to rrf_score
        for entry in fused:
            weight = cfg.SOURCE_WEIGHTS.get(entry.get("doc_source", "cluster"), 1.0)
            entry["rrf_score"] *= weight

        fused.sort(key=lambda x: x["rrf_score"], reverse=True)
        self._last_retrieval_stats = {
            "dense": len(dense_hits),
            "sparse": len(sparse_hits),
            "fused": len(fused),
            "top_rrf_score": round(fused[0]["rrf_score"], 4) if fused else 0.0,
        }
        log.info(
            "hybrid_search: dense=%d sparse=%d → fused=%d (top_k=%d)",
            len(dense_hits), len(sparse_hits), len(fused), k,
        )
        return fused

    @property
    def last_retrieval_stats(self) -> dict:
        """Stats from the most recent hybrid_search call."""
        return self._last_retrieval_stats

    def search_by_uid(self, uid: str) -> dict[str, Any] | None:
        row = self._uid_to_row.get(uid)
        if row is None:
            return None
        return dict(self._metadata[row])

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Text persistence (Option B — DB-backed reconstruction)
    # ------------------------------------------------------------------

    def persist_texts(self, conn) -> int:
        """
        Upsert all indexed texts into vector_store_docs.
        Call this after index_graph() so the DB always mirrors the index.
        Returns the number of rows written.
        """
        from persistence.vector_store_repo import persist_texts
        n = persist_texts(conn, self._metadata)
        log.info("persist_texts: %d docs written to DB", n)
        return n

    def rebuild_from_db(self, conn) -> None:
        """
        Reconstruct the FAISS index from texts stored in vector_store_docs.
        Use when index.faiss is absent but the DB has rows (e.g. after a
        fresh pod restart without a mounted volume).
        """
        from persistence.vector_store_repo import load_texts
        metadata = load_texts(conn)
        if not metadata:
            log.warning("rebuild_from_db: no rows in vector_store_docs — index stays empty")
            return

        texts = [m["text"] for m in metadata]
        log.info("rebuild_from_db: re-embedding %d docs…", len(texts))
        vectors = self._embedder.embed(texts)

        self._index = faiss.IndexFlatIP(self._embedder.dim)
        self._index.add(vectors)
        self._metadata = metadata
        self._uid_to_row = {m["uid"]: i for i, m in enumerate(metadata)}
        self._bm25.build(self._metadata)
        self._bm25_dirty = False
        log.info("rebuild_from_db: index rebuilt — %d vectors", self._index.ntotal)

    def save(self, path: Path | str | None = None) -> None:
        dest = Path(path or cfg.VECTOR_STORE_PATH)
        dest.parent.mkdir(parents=True, exist_ok=True)

        faiss.write_index(self._index, str(dest))
        meta_path = dest.with_suffix(".meta.pkl")
        with open(meta_path, "wb") as f:
            pickle.dump(
                {"metadata": self._metadata, "uid_to_row": self._uid_to_row}, f
            )
        log.info("Index saved to %s (%d vectors)", dest, self._index.ntotal)

    def load(self, path: Path | str | None = None) -> None:
        src = Path(path or cfg.VECTOR_STORE_PATH)
        if not src.exists():
            raise FileNotFoundError(f"No FAISS index at {src}")

        self._index = faiss.read_index(str(src))
        meta_path = src.with_suffix(".meta.pkl")
        with open(meta_path, "rb") as f:
            data = pickle.load(f)
        self._metadata = data["metadata"]
        self._uid_to_row = data["uid_to_row"]
        self._bm25.build(self._metadata)
        self._bm25_dirty = False
        log.info("Index loaded from %s (%d vectors)", src, self._index.ntotal)

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    @property
    def size(self) -> int:
        return self._index.ntotal if self._index else 0

    def summary(self) -> str:
        if not self._metadata:
            return "FAISSStore: empty"
        kube_versions = {m["kube_version"] for m in self._metadata if m["kube_version"]}
        kind_counts: dict[str, int] = {}
        for m in self._metadata:
            kind_counts[m["kind"]] = kind_counts.get(m["kind"], 0) + 1
        lines = [
            f"FAISSStore: {self.size} vectors  dim={self._embedder.dim}",
            f"  K8s version(s): {', '.join(sorted(kube_versions)) or 'unknown'}",
        ]
        for kind, count in sorted(kind_counts.items()):
            lines.append(f"  {kind}: {count}")
        return "\n".join(lines)
