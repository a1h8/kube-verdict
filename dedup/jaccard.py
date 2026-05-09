from __future__ import annotations
import logging
import re

import config as cfg

log = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[A-Za-z0-9_.\-/]+")


def _tokenize(text: str) -> frozenset[str]:
    return frozenset(t.lower() for t in _TOKEN_RE.findall(text))


def jaccard(a: str, b: str) -> float:
    ta, tb = _tokenize(a), _tokenize(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def jaccard_deduplicate(
    texts: list[str],
    threshold: float | None = None,
) -> list[int]:
    """
    Greedy deduplication: iterates texts in order, keeps a chunk only if its
    Jaccard similarity to every already-kept chunk is below threshold.

    Returns a list of indices into the original texts list.

    Complexity: O(n²) on kept items — acceptable for the sizes involved
    (typically a few hundred chunks per incident).
    """
    cutoff = threshold if threshold is not None else cfg.JACCARD_THRESHOLD
    kept_indices: list[int] = []
    kept_tokens: list[frozenset[str]] = []

    for i, text in enumerate(texts):
        tokens = _tokenize(text)
        duplicate = False
        for kept_tok in kept_tokens:
            union = tokens | kept_tok
            if not union:
                continue
            sim = len(tokens & kept_tok) / len(union)
            if sim >= cutoff:
                duplicate = True
                break
        if not duplicate:
            kept_indices.append(i)
            kept_tokens.append(tokens)

    removed = len(texts) - len(kept_indices)
    log.info(
        "Jaccard dedup (threshold=%.2f): %d → %d chunks (%d removed)",
        cutoff, len(texts), len(kept_indices), removed,
    )
    return kept_indices
