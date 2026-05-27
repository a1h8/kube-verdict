"""
Policy gate: classifies a proposed remediation as AUTO / HUMAN_REVIEW / NO_GO.

Verdict rules (evaluated top-down; first match wins)
─────────────────────────────────────────────────────
  NO_GO        score < 0.60
               OR blast radius = CRITICAL
               OR rollback not available
               OR beam search exhausted all paths (max_switches_reached)

  AUTO         score ≥ 0.85
               AND blast radius = LOW
               AND rollback available
               AND namespace is not production
               AND MC win_rate ≥ 0.80

  HUMAN_REVIEW everything else (including any prod namespace — always minimum HUMAN)

Production namespaces: prod, production, live, main (and dash-prefixed/suffixed variants).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

AUTO_SCORE_MIN:    float = 0.85
HUMAN_SCORE_MIN:   float = 0.60
AUTO_WIN_RATE_MIN: float = 0.80

_PROD_NAMES: frozenset[str] = frozenset({"prod", "production", "live", "main"})


class Verdict(str, Enum):
    AUTO         = "AUTO"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    NO_GO        = "NO_GO"


@dataclass
class GateResult:
    verdict: Verdict
    reasons: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return f"{self.verdict.value}: {'; '.join(self.reasons)}"


def _is_prod(namespace: str) -> bool:
    ns = (namespace or "").lower()
    return any(
        ns == p or ns.startswith(f"{p}-") or ns.endswith(f"-{p}")
        for p in _PROD_NAMES
    )


def evaluate(
    score:                float,
    risk:                 str,
    rollback_available:   bool,
    namespace:            str   = "",
    mc_win_rate:          float = 1.0,
    max_switches_reached: bool  = False,
) -> GateResult:
    """
    Classify the proposed remediation and return a GateResult.

    Parameters
    ----------
    score               pre-LLM confidence score (0–1)
    risk                blast radius risk level: LOW | MEDIUM | HIGH | CRITICAL
    rollback_available  True when a safe rollback command was derived
    namespace           target Kubernetes namespace (prod detection)
    mc_win_rate         Monte Carlo win_rate from run_monte_carlo() (0–1)
    max_switches_reached True when beam search exhausted all hypothesis paths
    """
    # ── Hard NO_GO conditions (any one is sufficient) ─────────────────────────
    no_go_reasons: list[str] = []
    if score < HUMAN_SCORE_MIN:
        no_go_reasons.append(f"score {score:.2f} < {HUMAN_SCORE_MIN} minimum")
    if risk == "CRITICAL":
        no_go_reasons.append("blast radius CRITICAL — irreversible without rollback")
    if not rollback_available:
        no_go_reasons.append("rollback_available=False — no safe recovery path")
    if max_switches_reached:
        no_go_reasons.append("beam search exhausted all hypothesis paths")
    if no_go_reasons:
        return GateResult(verdict=Verdict.NO_GO, reasons=no_go_reasons)

    # ── AUTO conditions (all must hold) ──────────────────────────────────────
    not_auto: list[str] = []
    if score < AUTO_SCORE_MIN:
        not_auto.append(f"score {score:.2f} < {AUTO_SCORE_MIN} AUTO threshold")
    if risk != "LOW":
        not_auto.append(f"blast radius {risk} — need LOW for AUTO")
    if _is_prod(namespace):
        not_auto.append(f"namespace '{namespace}' is production — always HUMAN_REVIEW minimum")
    if mc_win_rate < AUTO_WIN_RATE_MIN:
        not_auto.append(
            f"MC win_rate {mc_win_rate:.0%} < {AUTO_WIN_RATE_MIN:.0%} stability threshold"
        )
    if not_auto:
        return GateResult(verdict=Verdict.HUMAN_REVIEW, reasons=not_auto)

    return GateResult(
        verdict=Verdict.AUTO,
        reasons=["score ≥ 0.85, risk LOW, rollback available, non-prod, MC stable"],
    )
