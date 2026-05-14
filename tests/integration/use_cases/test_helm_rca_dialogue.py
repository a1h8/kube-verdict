"""
Integration tests — full RCA dialogue simulation from real Helm + kubectl inputs.

Input:  cases/helm_cases/h*/helm/values.yaml + observed/*.json
Pipeline:
  HelmDriftDetector (real drift from values.yaml vs kubectl state)
    → OntologyGraph
    → ContextBuilder
    → RCAAnalyzer (Ollama/Mistral)
    → DialogueSimulator (BFS proposal tree)
Output:
  ASCII tree of resolution paths (✓ resolved / ✗ dead_end)
  JSON saved to sim_results/helm_{case_name}.json
  expect.json assertions on root_cause keywords and confidence

Skip: Ollama not reachable or model not pulled.

Run:
    pytest tests/integration/use_cases/test_helm_rca_dialogue.py -m integration -s -v
    SIM_MAX_TURNS=1 pytest … -m integration -k "h001" -s
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import config as cfg
from llm.ollama_client import OllamaClient
from rca.analyzer import RCAAnalyzer
from tests.helm_cases.helm_case_factory import build_helm_graph, load_helm_case
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

HELM_CASES_ROOT = Path(__file__).parent.parent.parent.parent / "cases" / "helm_cases"
CASE_DIRS       = sorted(HELM_CASES_ROOT.glob("h*/"))
CASE_IDS        = [d.name for d in CASE_DIRS]

SIM_RESULTS_DIR = Path(__file__).parent / "sim_results"

if not CASE_DIRS:
    pytest.skip("No helm_cases found", allow_module_level=True)

_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def ollama_client() -> OllamaClient:
    client = OllamaClient()
    if not client.is_available():
        pytest.skip(f"Ollama not reachable at {cfg.OLLAMA_URL}")
    if not client.model_is_pulled():
        pytest.skip(f"Model '{cfg.OLLAMA_MODEL}' not pulled")
    return client


@pytest.fixture(scope="module", params=CASE_DIRS, ids=CASE_IDS)
def helm_dialogue(request, ollama_client: OllamaClient):
    """
    Full pipeline: real kubectl JSON → graph → RCAAnalyzer → DialogueSimulator.
    Returns (root_node, case_data, case_name).
    """
    case_dir  = request.param
    case      = load_helm_case(case_dir)
    case_name = case_dir.name

    graph = build_helm_graph(case)
    store = FAISSStore(embedder=Embedder())
    store.index_graph(graph)

    query    = case["expect"].get("query", f"diagnose {case_name}")
    analyzer = RCAAnalyzer(graph=graph, store=store, llm=ollama_client)

    root = DialogueSimulator(
        analyzer=analyzer,
        max_turns=_MAX_TURNS,
        max_branches=_MAX_BRANCHES,
        on_node=lambda r: None,  # silent in fixture; printed in test body
    ).run(query)

    json_path = write_json(
        root, f"helm_{case_name}", query,
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

class TestHelmRCADialogue:

    def test_pipeline_ingests_drift(self, helm_dialogue):
        """The graph must contain at least one drift annotation before LLM is called."""
        root, case, case_name = helm_dialogue
        ctx = root.report.context
        has_drift = (
            ctx and (ctx.drift or any("drift" in s.lower() for s in ctx.seeds))
        )
        assert has_drift, (
            f"{case_name}: no drift in context — check values.yaml vs observed/ files. "
            f"context seeds={ctx.seeds if ctx else 'None'}"
        )

    def test_root_cause_keywords_in_response(self, helm_dialogue):
        """At least 50% of root_cause_contains keywords must appear in the LLM response."""
        root, case, case_name = helm_dialogue
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
            f"{case_name}: {len(matched)}/{len(keywords)} keywords found "
            f"({matched!r}). Missing: {[kw for kw in keywords if kw.lower() not in full_text]!r}"
        )

    def test_fix_command_mentions_helm(self, helm_dialogue):
        """Remediation must reference helm (the correct fix path for a drift case)."""
        root, case, case_name = helm_dialogue
        fix_substrings = case["expect"].get("fix_commands_contain", [])
        if not fix_substrings:
            pytest.skip(f"{case_name}: no fix_commands_contain in expect.json")

        full_text = " ".join([
            root.report.raw_analysis,
            " ".join(root.report.remediation),
        ]).lower()
        matched = [s for s in fix_substrings if s.lower() in full_text]
        assert matched, (
            f"{case_name}: none of {fix_substrings!r} found. "
            f"Got remediation: {root.report.remediation!r}"
        )

    def test_has_resolvable_path(self, helm_dialogue):
        """BFS tree must contain at least one resolved path."""
        root, case, case_name = helm_dialogue
        resolved = count_resolved(root)
        assert resolved >= 1, (
            f"{case_name}: no resolved path in dialogue tree.\n"
            f"root score={root.score:.2f}  best={best_score(root):.2f}\n"
            f"{render_tree(root)}"
        )

    def test_confidence_not_worse_than_expected(self, helm_dialogue):
        root, case, case_name = helm_dialogue
        expected = case["expect"]["confidence"]
        actual   = root.label
        assert _RANK.get(actual, 0) >= _RANK.get(expected, 0) - 1, (
            f"{case_name}: label={actual!r} too far below expected={expected!r} "
            f"(score={root.score:.2f})"
        )

    def test_json_exported(self, helm_dialogue):
        _, case, case_name = helm_dialogue
        json_path = SIM_RESULTS_DIR / f"helm_{case_name}.json"
        assert json_path.exists(), f"{case_name}: sim result JSON not found at {json_path}"
        payload = json.loads(json_path.read_text())
        assert payload["summary"]["total_nodes"] >= 1

    def test_proposals_diverse(self, helm_dialogue):
        """Root proposals must not all be generic (drift case should trigger drift proposals)."""
        root, case, case_name = helm_dialogue
        if not root.children:
            pytest.skip(f"{case_name}: no children")
        categories = [c.proposal.category for c in root.children if c.proposal]
        generic_count = categories.count("generic")
        assert generic_count <= max(1, len(categories) // 2), (
            f"{case_name}: all proposals are generic — "
            f"ProposalEngine did not detect drift/memory/image signals in the report"
        )
