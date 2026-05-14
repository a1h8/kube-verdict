"""
Dialogue tree simulator.

Runs a BFS expansion of follow-up proposals over a fixed number of turns,
using a live RCAAnalyzer.  Each node stores the query, the report, and the
expansion status (pending → resolved | dead_end).

Outputs:
  - ASCII tree  (render_tree)
  - JSON export (node_to_dict / write_json)
  - Summary row (summary_row)
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from rca.analyzer import RCAAnalyzer, RCAReport
from tests.integration.use_cases.proposal_engine import Proposal, generate_proposals

_MAX_TURNS    = int(os.getenv("SIM_MAX_TURNS",    "2"))
_MAX_BRANCHES = int(os.getenv("SIM_MAX_BRANCHES", "3"))

_SCORE_RESOLVED_ABS   = 0.70   # score ≥ this → resolved unconditionally
_SCORE_RESOLVED_DELTA = 0.10   # score rose by this much from parent → resolved
_SCORE_STAGNANT_DELTA = 0.03   # |delta| < this → dead_end (stagnant)
_SCORE_REGRESS_DELTA  = 0.05   # score fell more than this → dead_end (regressed)
# LLM-stated HIGH + pre_llm score ≥ this also counts as resolved.
# Rationale: on a small graph the graph-topology score is capped even when the
# LLM correctly identifies and explains the root cause.
_SCORE_LLM_HIGH_MIN   = 0.45


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class DialogueNode:
    turn: int
    query: str
    report: RCAReport
    proposal: Optional[Proposal] = None   # None for root
    parent_score: float = 0.0
    status: str = "pending"               # pending | resolved | dead_end
    dead_end_reason: str = ""
    children: list["DialogueNode"] = field(default_factory=list)

    @property
    def score(self) -> float:
        c = self.report.context
        if c and c.pre_llm_confidence:
            return c.pre_llm_confidence.score
        return 0.0

    @property
    def label(self) -> str:
        c = self.report.context
        if c and c.pre_llm_confidence:
            return c.pre_llm_confidence.label
        return "LOW"

    @property
    def delta(self) -> float:
        return self.score - self.parent_score


# ---------------------------------------------------------------------------
# Simulator
# ---------------------------------------------------------------------------

class DialogueSimulator:

    def __init__(
        self,
        analyzer: RCAAnalyzer,
        max_turns: int = _MAX_TURNS,
        max_branches: int = _MAX_BRANCHES,
        on_node=None,   # callable(root: DialogueNode) — called after each node
    ) -> None:
        self.analyzer     = analyzer
        self.max_turns    = max_turns
        self.max_branches = max_branches
        self.on_node      = on_node

    def run(self, root_query: str) -> DialogueNode:
        root_report = self.analyzer.analyze(root_query)
        root = DialogueNode(turn=0, query=root_query, report=root_report)
        if self.on_node:
            self.on_node(root)
        self._expand(root, root)
        return root

    def _expand(self, node: DialogueNode, root: DialogueNode) -> None:
        if node.turn >= self.max_turns:
            _mark_dead_end(node, "max_turns_reached")
            return

        proposals = generate_proposals(node.report, max_n=self.max_branches)
        if not proposals:
            _mark_dead_end(node, "no_proposals")
            return

        for proposal in proposals:
            child_report = self.analyzer.analyze(proposal.follow_up_query)
            child = DialogueNode(
                turn=node.turn + 1,
                query=proposal.follow_up_query,
                report=child_report,
                proposal=proposal,
                parent_score=node.score,
            )

            if _is_resolved(child):
                child.status = "resolved"
            elif _is_dead_end(child):
                _mark_dead_end(child, _dead_end_reason(child))
            else:
                self._expand(child, root)

            node.children.append(child)
            if self.on_node:
                self.on_node(root)


# ---------------------------------------------------------------------------
# Status helpers
# ---------------------------------------------------------------------------

def _is_resolved(node: DialogueNode) -> bool:
    if node.score >= _SCORE_RESOLVED_ABS:
        return True
    if node.delta >= _SCORE_RESOLVED_DELTA and node.score >= 0.55:
        return True
    # LLM stated MEDIUM/HIGH confidence + remediation commands present + sufficient
    # graph quality → the investigation converged.  On small graphs the topology
    # score is capped below 0.70 even when the LLM correctly identifies the root
    # cause — use the LLM output as a tiebreaker rather than a hard gate.
    llm_conf = (node.report.confidence or "").upper()
    if (llm_conf.startswith(("HIGH", "MEDIUM"))
            and bool(node.report.remediation)
            and node.score >= _SCORE_LLM_HIGH_MIN):
        return True
    return False


def _is_dead_end(node: DialogueNode) -> bool:
    if node.score < node.parent_score - _SCORE_REGRESS_DELTA:
        return True
    if abs(node.delta) < _SCORE_STAGNANT_DELTA:
        return True
    return False


def _dead_end_reason(node: DialogueNode) -> str:
    if node.score < node.parent_score - _SCORE_REGRESS_DELTA:
        return "confidence_regressed"
    return "confidence_stagnant"


def _mark_dead_end(node: DialogueNode, reason: str) -> None:
    node.status = "dead_end"
    node.dead_end_reason = reason


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def _walk(node: DialogueNode):
    yield node
    for child in node.children:
        yield from _walk(child)


def count_resolved(root: DialogueNode) -> int:
    return sum(1 for n in _walk(root) if n.status == "resolved")


def count_dead_ends(root: DialogueNode) -> int:
    return sum(1 for n in _walk(root) if n.status == "dead_end")


def count_nodes(root: DialogueNode) -> int:
    return sum(1 for _ in _walk(root))


def best_score(root: DialogueNode) -> float:
    return max(n.score for n in _walk(root))


# ---------------------------------------------------------------------------
# ASCII tree renderer
# ---------------------------------------------------------------------------

_STATUS_ICON = {"resolved": "✓", "dead_end": "✗", "pending": "…"}
_STATUS_SUFFIX = {
    "resolved": " resolved",
    "dead_end": lambda n: f" dead_end ({n.dead_end_reason})",
    "pending":  "",
}


def render_tree(root: DialogueNode) -> str:
    lines: list[str] = []
    _render_node(root, prefix="", is_last=True, lines=lines)
    return "\n".join(lines)


def _render_node(
    node: DialogueNode,
    prefix: str,
    is_last: bool,
    lines: list[str],
) -> None:
    connector = "└── " if is_last else "├── "
    icon      = _STATUS_ICON.get(node.status, "?")
    suffix    = _STATUS_SUFFIX.get(node.status, "")
    if callable(suffix):
        suffix = suffix(node)

    if node.proposal:
        head = f"[{node.proposal.label}] {node.proposal.description}"
    else:
        # Root
        short_q = node.query[:70] + ("…" if len(node.query) > 70 else "")
        head    = short_q
        connector = ""
        prefix    = ""

    score_part = f"score={node.score:.2f}, {node.label}"
    status_part = f" {icon}{suffix}" if node.status != "pending" else ""
    lines.append(f"{prefix}{connector}{head} → {score_part}{status_part}")

    child_prefix = prefix + ("    " if is_last else "│   ")
    for i, child in enumerate(node.children):
        _render_node(child, child_prefix, i == len(node.children) - 1, lines)


# ---------------------------------------------------------------------------
# JSON serialization
# ---------------------------------------------------------------------------

def node_to_dict(node: DialogueNode) -> dict:
    ctx = node.report.context
    return {
        "turn":             node.turn,
        "query":            node.query,
        "score":            round(node.score, 4),
        "label":            node.label,
        "delta":            round(node.delta, 4),
        "status":           node.status,
        "dead_end_reason":  node.dead_end_reason or None,
        "retrieval":        ctx.retrieval_stats if ctx else {},
        "raw_analysis":     node.report.raw_analysis[:1200] if node.report.raw_analysis else "",
        "root_cause":       node.report.root_cause or "",
        "remediation":      list(node.report.remediation) if node.report.remediation else [],
        "affected":         list(node.report.affected) if node.report.affected else [],
        "proposal": {
            "label":       node.proposal.label,
            "category":    node.proposal.category,
            "description": node.proposal.description,
        } if node.proposal else None,
        "children": [node_to_dict(c) for c in node.children],
    }


def write_json(
    root: DialogueNode,
    case_name: str,
    root_query: str,
    out_dir: Path,
    max_turns: int,
    max_branches: int,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "case":          case_name,
        "generated_at":  datetime.now(timezone.utc).isoformat(),
        "root_query":    root_query,
        "config":        {"max_turns": max_turns, "max_branches": max_branches},
        "summary": {
            "total_nodes": count_nodes(root),
            "resolved":    count_resolved(root),
            "dead_ends":   count_dead_ends(root),
            "best_score":  round(best_score(root), 4),
            "root_score":  round(root.score, 4),
        },
        "tree": node_to_dict(root),
    }
    path = out_dir / f"{case_name}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    return path


# ---------------------------------------------------------------------------
# Summary row (for the cross-case table)
# ---------------------------------------------------------------------------

def summary_row(case_name: str, root: DialogueNode) -> dict:
    return {
        "case":        case_name,
        "nodes":       count_nodes(root),
        "resolved":    count_resolved(root),
        "dead_ends":   count_dead_ends(root),
        "root_score":  round(root.score, 3),
        "best_score":  round(best_score(root), 3),
    }
