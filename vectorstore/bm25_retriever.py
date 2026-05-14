from __future__ import annotations
import re
from typing import Any

from rank_bm25 import BM25Okapi

# Primary: same compound token pattern as dedup/tfidf.py
_TOKEN = re.compile(r"[A-Za-z0-9_.=\-/+:]{2,}")
# Secondary split: break compound K8s tokens on = / - so that
# a query for "CrashLoopBackOff" matches "reason=CrashLoopBackOff"
_SPLIT = re.compile(r"[=\-/]")


def _tokenize(text: str) -> list[str]:
    """
    Index both compound tokens ("reason=CrashLoopBackOff") and their parts
    ("reason", "crashloopbackoff") so keyword queries match regardless of
    whether the user includes the key prefix.
    """
    tokens: list[str] = []
    for raw in _TOKEN.findall(text.lower()):
        tokens.append(raw)
        for part in _SPLIT.split(raw):
            if len(part) >= 2:
                tokens.append(part)
    return tokens


class BM25Retriever:
    """
    BM25Okapi index over the same metadata corpus as FAISSStore.

    Rebuilt lazily: call build() after a batch index_graph(), or the
    dirty-flag path in FAISSStore._ensure_bm25() handles incremental adds.
    """

    def __init__(self) -> None:
        self._bm25: BM25Okapi | None = None
        self._metadata: list[dict[str, Any]] = []

    def build(self, metadata: list[dict[str, Any]]) -> None:
        self._metadata = list(metadata)
        if not self._metadata:
            return
        corpus = [_tokenize(m["text"]) for m in self._metadata]
        self._bm25 = BM25Okapi(corpus)

    def search(self, query: str, top_k: int) -> list[dict[str, Any]]:
        if self._bm25 is None or not self._metadata:
            return []
        tokens = _tokenize(query)
        scores = self._bm25.get_scores(tokens)
        k = min(top_k, len(self._metadata))
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:k]
        return [
            {**self._metadata[i], "bm25_score": float(scores[i])}
            for i in ranked
            if scores[i] > 0.0  # skip zero-score (no token overlap)
        ]

    @property
    def size(self) -> int:
        return len(self._metadata)
