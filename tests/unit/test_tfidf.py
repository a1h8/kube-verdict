from dedup.tfidf import tfidf_rank


class TestTfidfRank:
    def test_returns_indices(self):
        texts = ["pod crashed", "service healthy", "node ready"]
        result = tfidf_rank("pod crashed", texts, top_k=2)
        assert isinstance(result, list)
        assert len(result) == 2
        assert all(0 <= i < len(texts) for i in result)

    def test_relevant_first(self):
        texts = [
            "kind=Pod phase=Running restarts=0",
            "kind=Pod phase=Failed restarts=15 CrashLoopBackOff",
            "kind=Service type=ClusterIP",
        ]
        # Use exact key=value tokens that appear in the texts
        ranked = tfidf_rank("phase=Failed restarts=15 CrashLoopBackOff", texts, top_k=3)
        assert ranked[0] == 1

    def test_trigram_signal(self):
        # With trigrams, "phase=Failed restarts=15" is a strong phrase match
        texts = [
            "kind=Pod name=api-xyz phase=Failed restarts=15",
            "kind=Pod name=api-abc phase=Running restarts=0",
            "kind=Service name=api-svc type=ClusterIP",
        ]
        ranked = tfidf_rank("phase=Failed restarts=15", texts, top_k=3)
        assert ranked[0] == 0

    def test_drift_trigram(self):
        texts = [
            "drift field=status.readyReplicas declared=3 observed=0 severity=critical",
            "kind=ConfigMap name=api-config keys=[DATABASE_URL]",
            "kind=Node name=worker-1 ready=True cpu=4",
        ]
        ranked = tfidf_rank("declared=3 observed=0 severity=critical", texts, top_k=3)
        assert ranked[0] == 0

    def test_top_k_cap(self):
        texts = [f"chunk {i}" for i in range(10)]
        result = tfidf_rank("chunk", texts, top_k=3)
        assert len(result) == 3

    def test_fewer_than_top_k(self):
        texts = ["only one chunk"]
        result = tfidf_rank("one", texts, top_k=5)
        assert result == [0]

    def test_empty_corpus(self):
        assert tfidf_rank("anything", [], top_k=5) == []

    def test_k8s_token_pattern(self):
        # "apps/v1", "v1.28.3+k3s1", "CrashLoopBackOff" must not be split
        texts = [
            "apiVersion=apps/v1 kind=Deployment",
            "version=v1.28.3+k3s1 platform=linux",
            "reason=CrashLoopBackOff count=8",
        ]
        ranked = tfidf_rank("apps/v1 Deployment", texts, top_k=3)
        assert ranked[0] == 0

    def test_ngram_override(self):
        # Explicit unigram range still works
        texts = ["hello world", "foo bar", "baz qux"]
        result = tfidf_rank("hello", texts, top_k=3, ngram_range=(1, 1))
        assert isinstance(result, list)
