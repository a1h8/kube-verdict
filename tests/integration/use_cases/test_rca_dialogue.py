"""
Integration tests — dialogue tree simulation over the 20-case bank.

Each case is expanded into a BFS proposal tree: the root query triggers an
RCA, then ProposalEngine generates up to SIM_MAX_BRANCHES follow-up queries,
each of which is re-analyzed up to SIM_MAX_TURNS deep.

Outputs per run:
  • ASCII tree printed to the pytest capture buffer (-s to see it)
  • JSON saved to tests/integration/use_cases/sim_results/{case}.json
  • Summary table printed at session end

Environment variables:
  SIM_MAX_TURNS    (default 2)   — BFS depth after root
  SIM_MAX_BRANCHES (default 3)   — proposals per node

Skip conditions:
  - Ollama not reachable
  - Required model not pulled

Run:
    pytest tests/integration/use_cases/test_rca_dialogue.py -m integration -s -v
    pytest tests/integration/use_cases/test_rca_dialogue.py -m integration -k "001" -s
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import config as cfg
from llm.ollama_client import OllamaClient
from rca.analyzer import RCAAnalyzer
from tests.cases.graph_factory import build_graph, load_case
from tests.integration.use_cases.dialogue_simulator import (
    DialogueSimulator,
    _MAX_BRANCHES,
    _MAX_TURNS,
    best_score,
    count_dead_ends,
    count_nodes,
    count_resolved,
    render_tree,
    summary_row,
    write_json,
)
from vectorstore.embedder import Embedder
from vectorstore.store import FAISSStore

pytestmark = pytest.mark.integration

CASES_ROOT = Path(__file__).parent.parent.parent.parent / "cases"
CASE_DIRS  = sorted(CASES_ROOT.glob("0*/"))
CASE_IDS   = [d.name for d in CASE_DIRS]

SIM_RESULTS_DIR = Path(__file__).parent / "sim_results"

assert CASE_DIRS, f"No case directories found under {CASES_ROOT}"


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


@pytest.fixture(scope="session")
def sim_results_store() -> dict:
    """Shared dict to accumulate summary rows across all parametrized cases."""
    return {}


@pytest.fixture(scope="module", params=CASE_DIRS, ids=CASE_IDS)
def dialogue_tree(request, ollama_client: OllamaClient, sim_results_store: dict):
    """
    Build graph + store, run DialogueSimulator, write JSON, return
    (root_node, case_data, case_name).
    """
    case_dir = request.param
    data     = load_case(case_dir)
    inp      = data["input"]
    case_name = case_dir.name

    graph = build_graph(inp)
    store = FAISSStore(embedder=Embedder())
    store.index_graph(graph)

    analyzer  = RCAAnalyzer(graph=graph, store=store, llm=ollama_client)
    simulator = DialogueSimulator(
        analyzer=analyzer,
        max_turns=_MAX_TURNS,
        max_branches=_MAX_BRANCHES,
    )

    root = simulator.run(inp["query"])

    json_path = write_json(
        root, case_name, inp["query"],
        out_dir=SIM_RESULTS_DIR,
        max_turns=_MAX_TURNS,
        max_branches=_MAX_BRANCHES,
    )

    sim_results_store[case_name] = summary_row(case_name, root)

    print(f"\n{'─' * 70}")
    print(f"  {case_name}  (turns={_MAX_TURNS}  branches={_MAX_BRANCHES})")
    print(f"{'─' * 70}")
    print(render_tree(root))
    print(f"  nodes={count_nodes(root)}  resolved={count_resolved(root)}  "
          f"dead_ends={count_dead_ends(root)}  best={best_score(root):.2f}")
    print(f"  → JSON: {json_path}")

    return root, data, case_name


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDialogueTree:

    def test_tree_has_children(self, dialogue_tree):
        """Root must have expanded at least one proposal."""
        root, _, case_name = dialogue_tree
        assert root.children, (
            f"{case_name}: root has no children — ProposalEngine returned no proposals"
        )

    def test_at_least_one_non_dead_end(self, dialogue_tree):
        """At least one branch must not be immediately a dead_end from root."""
        root, _, case_name = dialogue_tree
        non_dead = [c for c in root.children if c.status != "dead_end"]
        assert non_dead, (
            f"{case_name}: all root-level proposals lead to immediate dead ends.\n"
            f"{render_tree(root)}"
        )

    def test_has_resolvable_path(self, dialogue_tree):
        """At least one path in the tree must reach 'resolved' status."""
        root, _, case_name = dialogue_tree
        assert count_resolved(root) >= 1, (
            f"{case_name}: no resolved path found in the dialogue tree.\n"
            f"root_score={root.score:.2f}, best={best_score(root):.2f}\n"
            f"{render_tree(root)}"
        )

    def test_json_exported(self, dialogue_tree):
        """JSON simulation result must have been written to sim_results/."""
        _, _, case_name = dialogue_tree
        json_path = SIM_RESULTS_DIR / f"{case_name}.json"
        assert json_path.exists(), f"{case_name}: sim result JSON not written"
        payload = json.loads(json_path.read_text())
        assert payload["summary"]["total_nodes"] >= 1
        assert payload["tree"]["turn"] == 0

    def test_ascii_tree_non_empty(self, dialogue_tree):
        """render_tree() must produce a non-empty string."""
        root, _, case_name = dialogue_tree
        tree_str = render_tree(root)
        assert tree_str.strip(), f"{case_name}: render_tree() returned empty string"

    def test_root_score_positive(self, dialogue_tree):
        """Root pre-LLM confidence must be positive."""
        root, _, case_name = dialogue_tree
        assert root.score > 0.0, (
            f"{case_name}: root pre_llm_confidence.score is 0 — graph may be empty"
        )

    def test_proposals_are_diverse(self, dialogue_tree):
        """Root proposals must have distinct categories (no 3× generic)."""
        root, _, case_name = dialogue_tree
        if not root.children:
            pytest.skip(f"{case_name}: no children to check")
        categories = [c.proposal.category for c in root.children if c.proposal]
        # At most half can be "generic"
        generic_count = categories.count("generic")
        assert generic_count <= max(1, len(categories) // 2), (
            f"{case_name}: too many generic proposals ({generic_count}/{len(categories)})"
        )


# ---------------------------------------------------------------------------
# Session-level summary table
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session", autouse=True)
def print_summary_table(sim_results_store: dict):
    """Print a cross-case summary table after all simulations have run."""
    yield  # let all tests run

    rows = list(sim_results_store.values())
    if not rows:
        return

    col_widths = {
        "case":       max(len(r["case"]) for r in rows),
        "nodes":      5,
        "resolved":   8,
        "dead_ends":  9,
        "root_score": 10,
        "best_score": 10,
    }

    def _fmt(r: dict) -> str:
        return (
            f"  {r['case']:<{col_widths['case']}}  "
            f"{r['nodes']:>{col_widths['nodes']}}  "
            f"{r['resolved']:>{col_widths['resolved']}}  "
            f"{r['dead_ends']:>{col_widths['dead_ends']}}  "
            f"{r['root_score']:>{col_widths['root_score']}.3f}  "
            f"{r['best_score']:>{col_widths['best_score']}.3f}"
        )

    header = (
        f"  {'Case':<{col_widths['case']}}  "
        f"{'Nodes':>{col_widths['nodes']}}  "
        f"{'Resolved':>{col_widths['resolved']}}  "
        f"{'DeadEnds':>{col_widths['dead_ends']}}  "
        f"{'RootScore':>{col_widths['root_score']}}  "
        f"{'BestScore':>{col_widths['best_score']}}"
    )
    sep = "  " + "─" * (len(header) - 2)

    print(f"\n{'═' * len(header)}")
    print("  DIALOGUE SIMULATION SUMMARY")
    print(f"{'═' * len(header)}")
    print(header)
    print(sep)
    for r in rows:
        print(_fmt(r))
    print(sep)
    resolved_total  = sum(r["resolved"]  for r in rows)
    dead_end_total  = sum(r["dead_ends"] for r in rows)
    print(f"  {'TOTAL':<{col_widths['case']}}  "
          f"{'':>{col_widths['nodes']}}  "
          f"{resolved_total:>{col_widths['resolved']}}  "
          f"{dead_end_total:>{col_widths['dead_ends']}}")
    print(f"{'═' * len(header)}\n")
