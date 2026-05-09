import pytest
from dedup.jaccard import jaccard, jaccard_deduplicate


class TestJaccard:
    def test_identical(self):
        assert jaccard("a b c", "a b c") == pytest.approx(1.0)

    def test_disjoint(self):
        assert jaccard("a b c", "d e f") == pytest.approx(0.0)

    def test_partial(self):
        # intersection={a,b} union={a,b,c,d} → 0.5
        assert jaccard("a b c", "a b d") == pytest.approx(0.5)

    def test_empty_first(self):
        assert jaccard("", "a b c") == pytest.approx(0.0)

    def test_empty_both(self):
        assert jaccard("", "") == pytest.approx(0.0)

    def test_case_insensitive(self):
        assert jaccard("CrashLoopBackOff Pod", "crashloopbackoff pod") == pytest.approx(1.0)

    def test_k8s_tokens_preserved(self):
        # dots and slashes should be part of tokens
        score = jaccard("kind=Pod phase=Failed", "kind=Pod phase=Running")
        assert 0.0 < score < 1.0


class TestJaccardDeduplicate:
    def test_removes_duplicate(self):
        texts = ["a b c", "a b c", "d e f"]
        kept = jaccard_deduplicate(texts, threshold=0.9)
        assert 0 in kept
        assert 1 not in kept   # duplicate of 0
        assert 2 in kept

    def test_keeps_all_when_distinct(self):
        texts = ["a b c", "d e f", "g h i"]
        kept = jaccard_deduplicate(texts, threshold=0.9)
        assert kept == [0, 1, 2]

    def test_threshold_high_keeps_near_dupes(self):
        # threshold=1.0 → only exact duplicates removed
        texts = ["a b c", "a b d"]
        kept = jaccard_deduplicate(texts, threshold=1.0)
        assert len(kept) == 2

    def test_threshold_low_removes_similar(self):
        # threshold=0.3 → "a b c" and "a b d" share 2/4 tokens → Jaccard=0.5 > 0.3
        texts = ["a b c", "a b d", "x y z"]
        kept = jaccard_deduplicate(texts, threshold=0.3)
        assert 0 in kept
        assert 1 not in kept
        assert 2 in kept

    def test_empty_list(self):
        assert jaccard_deduplicate([]) == []

    def test_single_item(self):
        assert jaccard_deduplicate(["only one"]) == [0]

    def test_k8s_drift_dedup(self):
        # Two drift lines for the same field should be deduped
        t1 = "drift field=status.readyReplicas declared=3 observed=0 severity=critical"
        t2 = "drift field=status.readyReplicas declared=3 observed=0 severity=critical"
        t3 = "kind=Pod name=api-xyz phase=Failed restarts=15"
        kept = jaccard_deduplicate([t1, t2, t3], threshold=0.8)
        assert 0 in kept
        assert 1 not in kept
        assert 2 in kept
