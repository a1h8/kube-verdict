"""Unit tests for BM25Retriever and rrf_fuse."""
from __future__ import annotations

import pytest

from vectorstore.bm25_retriever import BM25Retriever, _tokenize
from vectorstore.rrf import rrf_fuse


# ── tokenizer ─────────────────────────────────────────────────────────────────

def test_tokenize_k8s_tokens():
    tokens = _tokenize("phase=Failed restarts=15 reason=OOMKilled")
    assert "phase=failed" in tokens
    assert "restarts=15" in tokens
    assert "reason=oomkilled" in tokens


def test_tokenize_short_tokens_excluded():
    tokens = _tokenize("a b cd efg")
    assert "a" not in tokens
    assert "b" not in tokens
    assert "cd" in tokens
    assert "efg" in tokens


# ── BM25Retriever ─────────────────────────────────────────────────────────────

_METADATA = [
    {"uid": "pod/ns/crasher",  "text": "Pod ns/crasher phase=Failed reason=CrashLoopBackOff restarts=15", "doc_source": "cluster"},
    {"uid": "pod/ns/healthy",  "text": "Pod ns/healthy phase=Running restarts=0",                         "doc_source": "cluster"},
    {"uid": "pod/ns/oom",      "text": "Pod ns/oom phase=Failed reason=OOMKilled memory=512Mi",           "doc_source": "cluster"},
    {"uid": "svc/ns/frontend", "text": "Service ns/frontend selector=app:frontend port=80",               "doc_source": "cluster"},
]


def test_bm25_returns_nothing_on_empty():
    r = BM25Retriever()
    assert r.search("CrashLoopBackOff", top_k=5) == []


def test_bm25_build_and_search_exact():
    r = BM25Retriever()
    r.build(_METADATA)
    results = r.search("CrashLoopBackOff restarts", top_k=3)
    assert results, "Expected at least one result"
    assert results[0]["uid"] == "pod/ns/crasher"


def test_bm25_oom_query():
    r = BM25Retriever()
    r.build(_METADATA)
    results = r.search("OOMKilled memory", top_k=3)
    assert results[0]["uid"] == "pod/ns/oom"


def test_bm25_no_overlap_returns_empty():
    r = BM25Retriever()
    r.build(_METADATA)
    # query tokens that don't appear in any document
    results = r.search("xyzzyqwertz", top_k=3)
    assert results == []


def test_bm25_top_k_respected():
    r = BM25Retriever()
    r.build(_METADATA)
    results = r.search("phase=Failed", top_k=1)
    assert len(results) <= 1


def test_bm25_result_has_bm25_score():
    r = BM25Retriever()
    r.build(_METADATA)
    results = r.search("CrashLoopBackOff", top_k=2)
    for res in results:
        assert "bm25_score" in res
        assert res["bm25_score"] > 0.0


# ── rrf_fuse ──────────────────────────────────────────────────────────────────

def _make_list(uids: list[str]) -> list[dict]:
    return [{"uid": u, "text": u, "doc_source": "cluster"} for u in uids]


def test_rrf_single_list():
    ranked = _make_list(["a", "b", "c"])
    fused = rrf_fuse([ranked], top_k=3)
    uids = [e["uid"] for e in fused]
    assert uids == ["a", "b", "c"]


def test_rrf_two_lists_agreement_boosts():
    list1 = _make_list(["a", "b", "c"])
    list2 = _make_list(["b", "a", "d"])
    fused = rrf_fuse([list1, list2], top_k=4)
    uids = [e["uid"] for e in fused]
    # "a" and "b" appear in both lists → should rank above "c" and "d"
    assert set(uids[:2]) == {"a", "b"}


def test_rrf_top_k_limits_output():
    list1 = _make_list(["a", "b", "c", "d", "e"])
    list2 = _make_list(["e", "d", "c", "b", "a"])
    fused = rrf_fuse([list1, list2], top_k=3)
    assert len(fused) == 3


def test_rrf_result_has_rrf_score():
    ranked = _make_list(["x", "y"])
    fused = rrf_fuse([ranked], top_k=2)
    for entry in fused:
        assert "rrf_score" in entry
        assert entry["rrf_score"] > 0.0


def test_rrf_scores_decrease():
    ranked = _make_list(["a", "b", "c"])
    fused = rrf_fuse([ranked], top_k=3)
    scores = [e["rrf_score"] for e in fused]
    assert scores == sorted(scores, reverse=True)


def test_rrf_item_in_both_lists_scores_higher_than_single():
    shared = {"uid": "shared", "text": "shared", "doc_source": "cluster"}
    only1  = {"uid": "only1",  "text": "only1",  "doc_source": "cluster"}
    list1 = [shared, only1]
    list2 = [shared]
    fused = rrf_fuse([list1, list2], top_k=2)
    uid_score = {e["uid"]: e["rrf_score"] for e in fused}
    assert uid_score["shared"] > uid_score["only1"]


def test_rrf_custom_k():
    ranked = _make_list(["a", "b"])
    fused_60  = rrf_fuse([ranked], top_k=2, k=60)
    fused_600 = rrf_fuse([ranked], top_k=2, k=600)
    # smaller k → rank-1 advantage is bigger → ratio between positions is higher
    ratio_60  = fused_60[0]["rrf_score"]  / fused_60[1]["rrf_score"]
    ratio_600 = fused_600[0]["rrf_score"] / fused_600[1]["rrf_score"]
    assert ratio_60 > ratio_600
