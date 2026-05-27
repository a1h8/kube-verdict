"""
Beam Search path state machine for RCA hypothesis exploration.

Wraps the multi-path exploration logic from the workflow into a standalone
module. The workflow (archive_path_node, select_best_node) delegates
scoring decisions to this module.

State machine
─────────────
  EXPLORING      — actively analyzing the current hypothesis
  SWITCHING      — confidence stagnated (LOW × threshold); switching to next path
  BEST_SELECTED  — all paths explored; best result restored

Path switch triggers when consecutive LOW confidence observations ≥ SWITCH_THRESHOLD.
MAX_SWITCHES caps total switches to prevent infinite cycling.
"""
from __future__ import annotations

from dataclasses import dataclass, field

SWITCH_THRESHOLD: int = 2   # consecutive LOW observations → trigger switch
MAX_SWITCHES:     int = 3   # hard cap on total path switches


@dataclass
class PathState:
    step:       int
    hypothesis: str
    confidence: str   # LOW | MEDIUM | HIGH
    score:      float
    report:     dict  = field(default_factory=dict)
    switches:   int   = 0


@dataclass
class BeamSearchResult:
    best_path:            PathState | None
    switches_used:        int
    max_switches_reached: bool


def should_switch_path(
    conf_history: list[str],
    threshold:    int = SWITCH_THRESHOLD,
) -> bool:
    """
    Return True when the last `threshold` entries in conf_history are all LOW,
    signalling that the current path is stagnant and should be archived.
    """
    if len(conf_history) < threshold:
        return False
    return all(c.upper() == "LOW" for c in conf_history[-threshold:])


def select_best(paths: list[PathState]) -> PathState | None:
    """Return the PathState with the highest confidence label, breaking ties by score."""
    _rank = {"HIGH": 3, "MEDIUM": 2, "LOW": 1, "": 0}
    if not paths:
        return None
    return max(paths, key=lambda p: (_rank.get(p.confidence.upper(), 0), p.score))


def next_path_index(
    current_idx: int,
    n_paths:     int,
    archived:    set[int],
) -> int | None:
    """
    Return the index of the next non-archived hypothesis to explore,
    cycling forward from current_idx.  Returns None when all paths are archived.
    """
    for offset in range(1, n_paths + 1):
        candidate = (current_idx + offset) % n_paths
        if candidate not in archived:
            return candidate
    return None
