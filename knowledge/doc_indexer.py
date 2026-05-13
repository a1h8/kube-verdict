"""
Enterprise document indexer.

Chunks EnterpriseDoc objects into overlapping text windows, wraps each
window as a duck-typed entity compatible with FAISSStore.add_entity(),
and adds them to the active vector store so the ContextBuilder can
retrieve enterprise knowledge alongside cluster state.
"""
from __future__ import annotations

import logging
import re
import textwrap
from knowledge.doc_store import DocStore, EnterpriseDoc

log = logging.getLogger(__name__)

_CHUNK_SIZE  = 600   # target chars per chunk
_CHUNK_OVER  = 100   # overlap between consecutive chunks


# ── Duck-typed entity that FAISSStore.add_entity() accepts ────────────────────

class _DocChunk:
    """Minimal entity interface for FAISS indexing — no K8sEntity inheritance needed."""

    def __init__(
        self, uid: str, name: str, doc_id: str,
        doc_title: str, tags: list[str], content: str,
    ) -> None:
        self.uid       = uid
        self.name      = name
        self.namespace = "enterprise-docs"
        self.kind      = "EnterpriseDoc"    # plain string — to_text() handles it
        self._content  = content
        self._doc_id   = doc_id
        self._tags     = tags
        self._title    = doc_title

    def to_text(self) -> str:
        tag_str = " ".join(self._tags) if self._tags else ""
        return (
            f"kind=EnterpriseDoc doc_id={self._doc_id} "
            f"title={self._title} tags={tag_str} "
            f"content={self._content}"
        )


# ── Chunking ──────────────────────────────────────────────────────────────────

def _chunk_text(text: str, size: int = _CHUNK_SIZE, overlap: int = _CHUNK_OVER) -> list[str]:
    """Split text into overlapping windows, respecting paragraph boundaries."""
    # Prefer splitting at blank lines (paragraphs)
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    if not paragraphs:
        return []

    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        if len(current) + len(para) + 1 > size and current:
            chunks.append(current.strip())
            # keep overlap from the end of current chunk
            current = current[-overlap:].lstrip() + " " + para
        else:
            current = (current + " " + para).strip()

    if current:
        chunks.append(current.strip())

    # Fallback: hard-split very large paragraphs
    result: list[str] = []
    for c in chunks:
        if len(c) > size * 2:
            for part in textwrap.wrap(c, width=size):
                result.append(part)
        else:
            result.append(c)

    return result or [text[:size]]


# ── Indexer ───────────────────────────────────────────────────────────────────

class DocIndexer:
    """Index enterprise documents into an existing FAISSStore."""

    def __init__(self, store) -> None:  # store: FAISSStore (no import loop)
        self._store = store

    def index_doc(self, doc: EnterpriseDoc) -> int:
        """Chunk and index one document. Returns number of chunks added."""
        if not doc.content.strip():
            return 0
        chunks = _chunk_text(doc.content)
        for i, chunk in enumerate(chunks):
            entity = _DocChunk(
                uid       = f"doc-{doc.id}-{i}",
                name      = f"{doc.title} [{i+1}/{len(chunks)}]",
                doc_id    = doc.id,
                doc_title = doc.title,
                tags      = doc.tags,
                content   = chunk,
            )
            self._store.add_entity(entity)
        log.info("doc_indexer: '%s' → %d chunks indexed", doc.title, len(chunks))
        return len(chunks)

    def index_all(self, doc_store: DocStore) -> int:
        """Index every document in the store. Returns total chunks added."""
        total = 0
        for doc in doc_store.list():
            total += self.index_doc(doc)
        if total:
            log.info("doc_indexer: %d total chunks from enterprise docs", total)
        return total
