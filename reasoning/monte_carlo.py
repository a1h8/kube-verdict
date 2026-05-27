"""
Monte Carlo stability scoring for RCA confidence estimates.

Runs N simulations with ±PERTURBATION noise on the base confidence score
to assess whether the current diagnosis is stable or sensitive to evidence
noise.  Used by the policy gate to gate AUTO decisions.

  win_rate ≥ STABLE_WIN_RATE  →  stable   (AUTO-eligible)
  win_rate < STABLE_WIN_RATE  →  unstable  (force HUMAN_REVIEW)
"""
from __future__ import annotations

import random
from dataclasses import dataclass

N_SIMULATIONS:   int   = 200
PERTURBATION:    float = 0.10    # ±10 % uniform noise
WIN_THRESHOLD:   float = 0.60    # simulation "wins" when perturbed score ≥ this
STABLE_WIN_RATE: float = 0.80    # win_rate floor for AUTO eligibility


@dataclass(frozen=True)
class MCResult:
    win_rate:      float   # fraction of simulations where score ≥ WIN_THRESHOLD
    mean_score:    float
    std_score:     float
    is_stable:     bool    # True when win_rate ≥ STABLE_WIN_RATE
    n_simulations: int


def run_monte_carlo(
    base_score:   float,
    n:            int   = N_SIMULATIONS,
    perturbation: float = PERTURBATION,
    win_threshold: float = WIN_THRESHOLD,
    seed:         int | None = None,
) -> MCResult:
    """
    Simulate n independent confidence evaluations by adding uniform noise
    in [-perturbation, +perturbation] to base_score, clamped to [0, 1].

    Parameters
    ----------
    base_score:    pre-LLM confidence score (0–1) from compute_confidence()
    n:             number of simulations (default 200)
    perturbation:  half-width of the uniform noise band (default 0.10)
    win_threshold: minimum score for a simulation to count as a win (default 0.60)
    seed:          optional RNG seed for deterministic tests
    """
    rng = random.Random(seed)
    scores = [
        max(0.0, min(1.0, base_score + rng.uniform(-perturbation, perturbation)))
        for _ in range(n)
    ]
    wins     = sum(1 for s in scores if s >= win_threshold)
    win_rate = wins / n
    mean     = sum(scores) / n
    variance = sum((s - mean) ** 2 for s in scores) / n

    return MCResult(
        win_rate=round(win_rate, 3),
        mean_score=round(mean, 3),
        std_score=round(variance ** 0.5, 3),
        is_stable=win_rate >= STABLE_WIN_RATE,
        n_simulations=n,
    )
