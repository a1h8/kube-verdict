"""
Beam Search path state machine for RCA hypothesis exploration.

The confidence router's stagnation test (``should_switch_path``) and its switch cap. The
selection helpers that had no caller, and the "beam search exhausted" NO_GO that needed
MAX_SWITCHES (3) switches where at most MAX_PATHS - 1 (2) exist, were removed
(docs/fix-plan-001.md, dead code). Since docs/structured-in-product.md the loop runs only
when no structured diagnosis ran.

Path switch triggers when consecutive LOW confidence observations ≥ SWITCH_THRESHOLD.
MAX_SWITCHES caps total switches to prevent infinite cycling.
"""
from __future__ import annotations

SWITCH_THRESHOLD: int = 2   # consecutive LOW observations → trigger switch
MAX_SWITCHES:     int = 3   # hard cap on total path switches


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
