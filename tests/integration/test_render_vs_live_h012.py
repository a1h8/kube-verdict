"""
h012 — GitOps render-vs-live scenario (the anchor-by-render wedge).

Unlike h001–h011 (which diff values.yaml against the *stored* Helm release
values), this case reconstructs the EXPECTED state by **rendering the chart with
`helm template`** and diffs that rendered manifest against the OBSERVED live
cluster. That is the differentiator: rendered intent → declared-vs-observed
drift evidence, before any LLM explanation.

Two layers of validation:

  1. test_render_vs_live_* — DETERMINISTIC, runs on every CI run with no helm
     binary. It diffs the committed rendered golden (rendered/expected.yaml,
     produced by `helm template` and version-controlled as evidence) against the
     observed graph, and asserts the drift is detected and OOM ranks H1.

  2. TestHelmRenderFreshness — helm-GUARDED. Re-runs the real ManifestRenderer
     (`helm template`) on the chart and asserts the output still matches the
     committed golden, so the evidence can never silently rot.

So the render-vs-live *diff* path is validated in CI unconditionally; the actual
`helm template` render is validated wherever the helm binary is present.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from ingestion.manifest_differ import ManifestDiffer
from ingestion.manifest_renderer import ManifestRenderer
from ontology.graph import OntologyGraph
from rca.remediation_engine import RemediationEngine
from tests.integration.cases.case_loader import (
    _deployment_from_kubectl,
    _event_from_kubectl,
    _pod_from_kubectl,
)

CASE_DIR = Path(__file__).parent / "cases" / "h012_gitops_render_vs_live"
NS = "production"


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _load_yaml_docs(path: Path) -> list[dict]:
    return [d for d in yaml.safe_load_all(path.read_text()) if isinstance(d, dict)]


@pytest.fixture(scope="module")
def expect() -> dict:
    return json.loads((CASE_DIR / "expect.json").read_text())


@pytest.fixture(scope="module")
def rendered_expected() -> list[dict]:
    """The committed `helm template` output — the EXPECTED (declared) state."""
    return _load_yaml_docs(CASE_DIR / "rendered" / "expected.yaml")


@pytest.fixture
def observed_graph() -> OntologyGraph:
    """
    Build the OBSERVED live cluster graph from kube/*.yaml.

    The live deployment runs replicas=1 with memory=128Mi (declared 3 / 512Mi)
    and its pod was OOMKilled. The `gitops.resources.api.memory` annotation
    mirrors what K8sCollector writes from the live container spec — it is the
    observed side the differ compares the rendered limit against.
    """
    g = OntologyGraph()

    dep_raw = _load_yaml_docs(CASE_DIR / "kube" / "deployment.yaml")[0]
    dep = _deployment_from_kubectl(dep_raw)
    # Live container memory limit, as the collector would surface it.
    live_mem = (
        dep_raw["spec"]["template"]["spec"]["containers"][0]
        ["resources"]["limits"]["memory"]
    )
    dep.annotations["gitops.resources.api.memory"] = live_mem
    g.add_entity(dep)

    for pod_raw in _load_yaml_docs(CASE_DIR / "kube" / "pod.yaml"):
        g.add_entity(_pod_from_kubectl(pod_raw))

    events_doc = _load_yaml_docs(CASE_DIR / "kube" / "events.yaml")[0]
    for evt_raw in events_doc.get("items", []):
        g.add_entity(_event_from_kubectl(evt_raw))

    return g


@pytest.fixture
def drifts(rendered_expected, observed_graph):
    """Diff the rendered expected state against the observed cluster."""
    return ManifestDiffer(track_orphans=False).diff(rendered_expected, observed_graph)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Render-vs-live drift detection (deterministic — no helm binary)
# ─────────────────────────────────────────────────────────────────────────────

class TestRenderVsLiveDrift:
    def test_replica_drift_is_critical(self, drifts):
        replica = [d for d in drifts if d.field_path == "spec.replicas"]
        assert replica, "expected spec.replicas drift (declared 3 vs live 1)"
        assert int(replica[0].declared) == 3
        assert int(replica[0].observed) == 1
        assert replica[0].severity == "critical"

    def test_memory_limit_drift_detected(self, drifts):
        mem = [d for d in drifts if d.field_path == "container.api.resources.memory"]
        assert mem, "expected rendered memory limit (512Mi) vs live (128Mi) drift"
        assert str(mem[0].declared) == "512Mi"
        assert str(mem[0].observed) == "128Mi"

    def test_all_drifts_sourced_from_gitops_render(self, drifts):
        assert drifts and all(d.source == "gitops" for d in drifts)

    def test_drift_annotated_on_live_deployment(self, observed_graph, drifts):
        dep = next(e for e in observed_graph.entities() if e.name == "api"
                   and e.kind.value == "Deployment")
        assert any(k.startswith("gitops.") for k in dep.annotations)

    def test_drift_matches_expectation_contract(self, drifts, expect):
        """The detected drift covers everything expect.json declares."""
        found = {d.field_path for d in drifts}
        for spec in expect["render_vs_live_drift"]:
            assert spec["field_path"] in found, (
                f"expect.json declares drift on {spec['field_path']} but it was not detected"
            )


# ─────────────────────────────────────────────────────────────────────────────
# 2. OOM ranks H1 over the render-vs-live evidence (deterministic)
# ─────────────────────────────────────────────────────────────────────────────

class TestRankingOnRenderedDrift:
    @pytest.fixture
    def hypotheses(self, observed_graph, drifts):
        # drifts fixture has already annotated observed_graph in place.
        return RemediationEngine().score(observed_graph)

    def test_oom_kill_fires(self, hypotheses):
        assert any(h.rule_id == "oom_kill" for h in hypotheses)

    def test_oom_kill_ranked_h1(self, hypotheses):
        assert hypotheses, "no hypotheses produced"
        assert hypotheses[0].rule_id == "oom_kill", (
            f"expected oom_kill as H1, got "
            f"{[(h.rule_id, round(h.weight, 3)) for h in hypotheses]}"
        )

    def test_expected_rules_present(self, hypotheses, expect):
        fired = {h.rule_id for h in hypotheses}
        for rule_id in expect.get("expected_rules", []):
            assert rule_id in fired, f"expected rule {rule_id} did not fire"

    def test_hypotheses_sorted_by_weight(self, hypotheses):
        weights = [h.weight for h in hypotheses]
        assert weights == sorted(weights, reverse=True)


# ─────────────────────────────────────────────────────────────────────────────
# 3. helm-guarded: committed golden is a faithful `helm template` render
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.skipif(shutil.which("helm") is None,
                    reason="helm binary not installed")
class TestHelmRenderFreshness:
    """
    Re-renders the chart with the real binary and asserts it still equals the
    committed golden — guarding against the chart and its rendered evidence
    drifting apart. Skipped on minimal CI images without helm; the diff path
    above still validates render-vs-live deterministically there.
    """

    def test_rendered_matches_committed_golden(self, rendered_expected):
        live = ManifestRenderer().render(
            str(CASE_DIR / "chart"),
            release_name="api",
            namespace=NS,
        )
        committed = {(d["kind"], d["metadata"]["name"]): d for d in rendered_expected}
        live_map = {(d["kind"], d["metadata"]["name"]): d for d in live}
        assert live_map.keys() == committed.keys()
        for key, doc in committed.items():
            assert live_map[key] == doc, (
                f"{key}: live `helm template` output diverged from committed golden; "
                f"regenerate rendered/expected.yaml"
            )

    def test_rendered_declares_intended_state(self):
        live = ManifestRenderer().render(
            str(CASE_DIR / "chart"),
            release_name="api",
            namespace=NS,
        )
        dep = next(d for d in live if d["kind"] == "Deployment")
        assert dep["spec"]["replicas"] == 3
        mem = dep["spec"]["template"]["spec"]["containers"][0]["resources"]["limits"]["memory"]
        assert mem == "512Mi"
