from __future__ import annotations


def rrf_fuse(
    ranked_lists: list[list[dict]],
    top_k: int,
    k: int = 60,
) -> list[dict]:
    """
    Reciprocal Rank Fusion (Cormack, Clarke & Buettcher, SIGIR 2009).

    score(d) = Σ_list  1 / (k + rank(d, list))

    k=60 is the constant from the original paper — it dampens the advantage
    of top-1 hits and gives tail results a fair chance to surface.  Documents
    absent from a list are simply not scored for that list (no penalty).
    """
    scores: dict[str, float] = {}
    best_entry: dict[str, dict] = {}

    for ranked in ranked_lists:
        for rank, entry in enumerate(ranked, start=1):
            uid = entry["uid"]
            scores[uid] = scores.get(uid, 0.0) + 1.0 / (k + rank)
            if uid not in best_entry:
                best_entry[uid] = entry

    sorted_uids = sorted(scores, key=lambda u: scores[u], reverse=True)[:top_k]
    return [
        {**best_entry[uid], "rrf_score": scores[uid]}
        for uid in sorted_uids
    ]
