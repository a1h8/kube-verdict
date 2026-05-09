from __future__ import annotations
import logging
import pickle
from pathlib import Path
from typing import Any

import faiss

import config as cfg
from ontology.entities import K8sEntity
from ontology.graph import OntologyGraph
from vectorstore.embedder import Embedder

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
            }
            for i, e in enumerate(entities)
        ]
        self._uid_to_row = {m["uid"]: i for i, m in enumerate(self._metadata)}
        log.info("Index built: %d vectors (dim=%d)", self._index.ntotal, self._embedder.dim)

    def add_entity(self, entity: K8sEntity, kube_version: str = "") -> None:
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
        })
        self._uid_to_row[entity.uid] = row

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
            entry["score"] = float(score)
            results.append(entry)

        return results

    def search_by_uid(self, uid: str) -> dict[str, Any] | None:
        row = self._uid_to_row.get(uid)
        if row is None:
            return None
        return dict(self._metadata[row])

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

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
