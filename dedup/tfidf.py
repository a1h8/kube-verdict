from __future__ import annotations
import logging

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

import config as cfg

log = logging.getLogger(__name__)

# Token pattern tuned for K8s diagnostic text:
#   - preserves compound tokens: "CrashLoopBackOff", "OOMKilled", "ImagePullBackOff"
#   - preserves key=value pairs: "phase=Failed", "restarts=15", "declared=3"
#   - preserves paths/versions: "v1.28.3+k3s1", "apps/v1", "nginx:1.21"
_K8S_TOKEN = r"[A-Za-z0-9_.=\-/+:]{2,}"

# Why trigrams?
# Unigram "3"          → noise
# Bigram  "restarts=15"             → one bad pod
# Trigram "phase=Failed restarts=15" → confirmed crash loop pattern
#
# Unigram "OOMKilled"               → signal
# Bigram  "reason=OOMKilled"        → stronger
# Trigram "reason=OOMKilled count=8" → repeated OOM, high severity
#
# Unigram "declared=3"              → context-free
# Trigram "declared=3 observed=0 severity=critical" → confirmed drift
_DEFAULT_NGRAM = (1, 3)


def tfidf_rank(
    query: str,
    texts: list[str],
    top_k: int | None = None,
    ngram_range: tuple[int, int] | None = None,
) -> list[int]:
    """
    Ranks texts by TF-IDF cosine similarity to the query.
    Uses (1, 3) trigrams by default so K8s compound phrases score correctly.
    Returns up to top_k indices ordered best-first.
    """
    k = top_k if top_k is not None else cfg.TFIDF_TOP_K
    ngrams = ngram_range or (1, cfg.TFIDF_NGRAM_MAX)

    if not texts:
        return []
    if len(texts) == 1:
        return [0]

    corpus = texts + [query]
    try:
        vectorizer = TfidfVectorizer(
            ngram_range=ngrams,
            analyzer="word",
            token_pattern=_K8S_TOKEN,
            sublinear_tf=True,   # log(1+tf) dampens very frequent tokens
            min_df=1,            # small corpus — keep every term
        )
        matrix = vectorizer.fit_transform(corpus)
        scores = cosine_similarity(matrix[-1], matrix[:-1])[0]
    except ValueError as exc:
        log.warning("TF-IDF failed (%s) — returning first %d chunks unranked", exc, k)
        return list(range(min(k, len(texts))))

    ranked = sorted(range(len(texts)), key=lambda i: scores[i], reverse=True)
    selected = ranked[:k]

    log.info(
        "TF-IDF (ngram=%s): %d chunks → top %d  best=%.4f",
        ngrams, len(texts), len(selected),
        float(scores[ranked[0]]) if ranked else 0.0,
    )
    return selected
