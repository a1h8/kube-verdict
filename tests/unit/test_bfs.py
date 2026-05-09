from dedup.bfs import find_unhealthy, expand_incident_context


class TestFindUnhealthy:
    def test_finds_failed_pod(self, synthetic_graph):
        seeds = find_unhealthy(synthetic_graph)
        uids = {e.uid for e in seeds}
        assert "pod-api-xyz" in uids

    def test_healthy_pod_not_seed(self, synthetic_graph):
        seeds = find_unhealthy(synthetic_graph)
        uids = {e.uid for e in seeds}
        assert "pod-api-abc" not in uids

    def test_finds_degraded_deployment(self, synthetic_graph):
        seeds = find_unhealthy(synthetic_graph)
        uids = {e.uid for e in seeds}
        assert "deploy-api" in uids

    def test_finds_pending_pvc(self, synthetic_graph):
        seeds = find_unhealthy(synthetic_graph)
        uids = {e.uid for e in seeds}
        assert "pvc-api-data" in uids

    def test_finds_warning_events(self, synthetic_graph):
        seeds = find_unhealthy(synthetic_graph)
        uids = {e.uid for e in seeds}
        assert "ev-crashloop" in uids
        assert "ev-pvc" in uids


class TestExpandIncidentContext:
    def test_includes_seeds(self, synthetic_graph):
        from dedup.bfs import find_unhealthy
        seeds = find_unhealthy(synthetic_graph)
        ctx = expand_incident_context(synthetic_graph, seeds=seeds, max_depth=1)
        seed_uids = {e.uid for e in seeds}
        ctx_uids = {e.uid for e in ctx}
        assert seed_uids.issubset(ctx_uids)

    def test_expands_to_neighbours(self, synthetic_graph):
        ctx = expand_incident_context(
            synthetic_graph, seeds=[], extra_uids=["pod-api-xyz"], max_depth=1
        )
        uids = {e.uid for e in ctx}
        # pod-api-xyz is connected to node-1, cm-api-config, secret-api, pvc-api-data, helm release
        assert "node-1" in uids or "cm-api-config" in uids or "helm-production-api" in uids

    def test_depth_zero_only_seeds(self, synthetic_graph):
        seed_pod = synthetic_graph.get("pod-api-xyz")
        ctx = expand_incident_context(
            synthetic_graph, seeds=[seed_pod], max_depth=0
        )
        # depth=0 means no BFS expansion — only the seeds themselves
        uids = {e.uid for e in ctx}
        assert "pod-api-xyz" in uids
        assert "cm-api-config" not in uids

    def test_no_duplicates(self, synthetic_graph):
        ctx = expand_incident_context(synthetic_graph, max_depth=2)
        uids = [e.uid for e in ctx]
        assert len(uids) == len(set(uids))

    def test_extra_uids_merged(self, synthetic_graph):
        ctx = expand_incident_context(
            synthetic_graph,
            seeds=[],
            extra_uids=["svc-api"],
            max_depth=1,
        )
        uids = {e.uid for e in ctx}
        assert "svc-api" in uids
