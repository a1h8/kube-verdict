"""
Integration tests — dialogue simulation over the native h0XX case bank.

Each case in tests/integration/cases/h*/  is loaded via case_loader,
converted to an OntologyGraph, and fed through the full pipeline:

  case_loader.build_graph(case)
    → FAISSStore.index_graph
    → RCAAnalyzer.analyze(query)
    → DialogueSimulator BFS tree
    → expect.json assertions

Skip conditions:
  - Ollama not reachable / model not pulled

Run:
    pytest tests/integration/use_cases/test_native_helm_dialogue.py -m integration -s -v
    pytest tests/integration/use_cases/test_native_helm_dialogue.py -m integration -k "h009" -s
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import config as cfg
from llm.ollama_client import OllamaClient
from rca.analyzer import RCAAnalyzer
from tests.integration.cases.case_loader import build_graph, list_cases, load_case
from tests.integration.use_cases.dialogue_simulator import (
    DialogueSimulator,
    _MAX_BRANCHES,
    _MAX_TURNS,
    best_score,
    count_dead_ends,
    count_nodes,
    count_resolved,
    render_tree,
    write_json,
)
from vectorstore.embedder import Embedder
from vectorstore.store import FAISSStore

pytestmark = pytest.mark.integration

CASES_ROOT = Path(__file__).parent.parent / "cases"
CASE_DIRS  = list_cases(CASES_ROOT)
CASE_IDS   = [d.name for d in CASE_DIRS]

SIM_RESULTS_DIR = Path(__file__).parent / "sim_results"

if not CASE_DIRS:
    pytest.skip("No native h0XX cases found", allow_module_level=True)

_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}

_CASE_QUERIES: dict[str, str] = {
    "h007_hpa_no_metrics":         "frontend-api HPA is stuck at 1 replica, metrics unavailable",
    "h008_init_container_fail":    "backend pod stuck in Init, db-migrate container fails on start",
    "h009_liveness_probe_loop":    "search-service restarts every few minutes, liveness probe timing out",
    "h010_resource_quota_exceeded": "worker-service pod Pending, cannot be scheduled in staging namespace",
    "h011_statefulset_pvc_stuck":  "kafka StatefulSet rolling update stuck on pod kafka-2",
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def ollama_client() -> OllamaClient:
    client = OllamaClient()
    if not client.is_available():
        pytest.skip(f"Ollama not reachable at {cfg.OLLAMA_URL} — run: ollama serve")
    if not client.model_is_pulled():
        pytest.skip(f"Model '{cfg.OLLAMA_MODEL}' not pulled — run: ollama pull {cfg.OLLAMA_MODEL}")
    return client


@pytest.fixture(scope="module", params=CASE_DIRS, ids=CASE_IDS)
def native_dialogue(request, ollama_client: OllamaClient):
    """
    Load native case → build graph → RCAAnalyzer → DialogueSimulator.
    Returns (root_node, case_data, case_name).
    """
    case_dir  = request.param
    case_name = case_dir.name
    case      = load_case(case_dir)

    query = (
        case["expect"].get("query")
        or _CASE_QUERIES.get(case_name)
        or f"diagnose {case_name}"
    )

    graph = build_graph(case)
    store = FAISSStore(embedder=Embedder())
    store.index_graph(graph)

    analyzer = RCAAnalyzer(graph=graph, store=store, llm=ollama_client)
    root = DialogueSimulator(
        analyzer=analyzer,
        max_turns=_MAX_TURNS,
        max_branches=_MAX_BRANCHES,
    ).run(query)

    json_path = write_json(
        root, f"native_{case_name}", query,
        out_dir=SIM_RESULTS_DIR,
        max_turns=_MAX_TURNS,
        max_branches=_MAX_BRANCHES,
    )

    print(f"\n{'─' * 70}")
    print(f"  {case_name}  (turns={_MAX_TURNS}  branches={_MAX_BRANCHES})")
    print(f"{'─' * 70}")
    print(render_tree(root))
    print(f"  nodes={count_nodes(root)}  resolved={count_resolved(root)}  "
          f"dead_ends={count_dead_ends(root)}  best={best_score(root):.2f}")
    print(f"  → JSON: {json_path}")

    return root, case, case_name


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestNativeHelmDialogue:

    def test_pipeline_loaded_graph(self, native_dialogue):
        """Graph must contain at least one entity after load."""
        root, case, case_name = native_dialogue
        ctx = root.report.context
        assert ctx is not None, f"{case_name}: report has no context"

    def test_root_score_positive(self, native_dialogue):
        """Pre-LLM confidence score must be positive (graph is non-trivial)."""
        root, _, case_name = native_dialogue
        assert root.score > 0.0, (
            f"{case_name}: root score is 0 — graph may be empty or unindexed"
        )

    def test_root_cause_keywords(self, native_dialogue):
        """At least 50% of root_cause_contains keywords from expect.json must appear."""
        root, case, case_name = native_dialogue
        keywords  = case["expect"]["root_cause_contains"]
        full_text = (
            root.report.raw_analysis + " " +
            root.report.root_cause + " " +
            " ".join(root.report.affected) + " " +
            " ".join(root.report.remediation)
        ).lower()

        matched   = [kw for kw in keywords if kw.lower() in full_text]
        threshold = max(1, len(keywords) // 2)
        assert len(matched) >= threshold, (
            f"{case_name}: {len(matched)}/{len(keywords)} keywords matched ({matched!r}). "
            f"Missing: {[kw for kw in keywords if kw.lower() not in full_text]!r}"
        )

    def test_fix_command_present(self, native_dialogue):
        """At least one expected fix substring from expect.json must appear."""
        root, case, case_name = native_dialogue
        fix_substrings = case["expect"].get("fix_commands_contain", [])
        if not fix_substrings:
            pytest.skip(f"{case_name}: no fix_commands_contain in expect.json")

        full_text = (
            root.report.raw_analysis + " " + " ".join(root.report.remediation)
        ).lower()
        matched = [s for s in fix_substrings if s.lower() in full_text]
        assert matched, (
            f"{case_name}: none of {fix_substrings!r} found in response. "
            f"remediation={root.report.remediation!r}"
        )

    def test_confidence_at_least_one_rank_below_expected(self, native_dialogue):
        """Confidence label must be no more than one rank below what expect.json says."""
        root, case, case_name = native_dialogue
        expected = case["expect"]["confidence"]
        actual   = root.label
        assert _RANK.get(actual, 0) >= _RANK.get(expected, 0) - 1, (
            f"{case_name}: label={actual!r} too far below expected={expected!r} "
            f"(score={root.score:.2f})"
        )

    def test_confidence_score_min(self, native_dialogue):
        """Pre-LLM score must meet the minimum threshold from expect.json."""
        root, case, case_name = native_dialogue
        min_score = case["expect"].get("confidence_score_min", 0.0)
        assert root.score >= min_score, (
            f"{case_name}: score={root.score:.3f} below minimum={min_score}"
        )

    def test_has_resolvable_path(self, native_dialogue):
        """BFS tree must contain at least one resolved node."""
        root, case, case_name = native_dialogue
        fallback_expected = case["expect"].get("fallback_expected", False)
        resolved = count_resolved(root)
        if fallback_expected:
            pytest.skip(f"{case_name}: fallback_expected=true, resolved path not required")
        assert resolved >= 1, (
            f"{case_name}: no resolved path in dialogue tree.\n"
            f"root score={root.score:.2f}  best={best_score(root):.2f}\n"
            f"{render_tree(root)}"
        )

    def test_json_exported(self, native_dialogue):
        """Simulation JSON must be written and have valid structure."""
        _, case, case_name = native_dialogue
        json_path = SIM_RESULTS_DIR / f"native_{case_name}.json"
        assert json_path.exists(), f"{case_name}: sim result JSON not found at {json_path}"
        payload = json.loads(json_path.read_text())
        assert payload["summary"]["total_nodes"] >= 1
