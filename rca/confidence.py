"""
Pre-LLM context quality score.

compute_confidence() scores the information richness of a ContextWindow
BEFORE it is sent to the LLM, based on six orthogonal signal sources plus
an optional OPA / Kyverno policy-violation boost:

  BFS depth          — how deeply the ontology graph was traversed
  Jaccard diversity  — fraction of unique chunks kept after dedup
  TF-IDF features    — number of ranked context chunks sent to the LLM
  Anchor matches     — declared-value anchors found on K8s entities
  Critical signals   — unhealthy seeds + firing Prometheus alerts
  Helm drift items   — declared vs observed diffs (values.yaml / kubectl)
  Policy violations  — OPA / Kyverno FAIL results (boost, capped at +0.30)

Score range : 0.0 – 1.0  (always capped)
Labels      : LOW (< 0.40) | MEDIUM (0.40 – 0.69) | HIGH (≥ 0.70)

The result is stored in ContextWindow.pre_llm_confidence and surfaced
in the LLM prompt so the model can calibrate its own confidence answer.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ContextConfidence:
    score: float             # 0.0 – 1.0
    label: str               # LOW | MEDIUM | HIGH
    reasons: tuple[str, ...] # one line per component, for display / debugging


def compute_confidence(
    *,
    bfs_depth: int,
    jaccard_kept_ratio: float,
    tfidf_top_k: int,
    matched_anchors: int,
    critical_signals: int = 0,
    helm_drift_items: int = 0,
    policy_fail_count: int = 0,
    policy_audit_count: int = 0,
    mutation_webhooks: int = 0,
) -> ContextConfidence:
    """
    Weighted sum of six base components (max 1.0) plus an optional
    OPA/Kyverno policy-violation boost (additive, final score capped at 1.0):

      Component             Weight / formula
      ──────────────────    ──────────────────────────────────────────────
      BFS depth             min(depth / 5, 1) × 0.25
      Jaccard diversity     min(ratio, 1) × 0.25
      TF-IDF features       min(count / 20, 1) × 0.20
      Anchor matches        min(count × 0.05, 0.15)
      Critical signals      min(count × 0.03, 0.15)  (seeds, alerts)
      Helm drift items      min(count × 0.07, 0.20)  (values.yaml/kubectl diffs)
      ──────────────────    ──────────────────────────────────────────────
      Policy fail (OPA/Kyverno FAIL)     min(count × 0.10, 0.30)  ┐
      Policy audit (wgpolicyk8s.io)      min(count × 0.05, 0.05)  ├ total capped at 0.30
      Mutation webhook applied           min(count × 0.05, 0.05)  ┘

    Parameters
    ----------
    bfs_depth:
        Number of hops configured for BFS graph traversal (BFS_MAX_DEPTH).
    jaccard_kept_ratio:
        Fraction of candidate chunks that survived Jaccard dedup:
        ``len(kept) / max(1, len(candidates))``.  Higher = more diverse context.
    tfidf_top_k:
        Number of TF-IDF–ranked chunks included in the context window.
    matched_anchors:
        Number of ``anchor.*`` annotation lines extracted from the graph.
    critical_signals:
        Unhealthy seeds + firing Prometheus alerts (excludes drift — counted
        separately to avoid cap interaction).
    helm_drift_items:
        Number of drift items in the context window: declared-vs-observed
        differences from values.yaml / kubectl state / HelmDriftDetector.
        Each adds +0.07, capped at +0.20.  This is the primary confidence
        signal for Helm / Helmfile input cases.
    policy_fail_count:
        Number of FAIL results in ``PolicyReport.results`` (OPA / Kyverno).
        Each adds +0.10 to the score, capped at +0.30.
    policy_audit_count:
        Number of audit-mode violations from ``policyreport.wgpolicyk8s.io``.
        Each adds +0.05, capped at +0.05.
    mutation_webhooks:
        Number of ``MutatingWebhookConfiguration`` entries that applied to the
        affected resources.  Each adds +0.05, capped at +0.05.
    """
    bfs_c    = min(bfs_depth / 5.0,          1.0) * 0.25
    jac_c    = min(jaccard_kept_ratio,        1.0) * 0.25
    tfidf_c  = min(tfidf_top_k / 20.0,       1.0) * 0.20
    anchor_c = min(matched_anchors  * 0.05,  0.15)
    signal_c = min(critical_signals * 0.03,  0.15)
    drift_c  = min(helm_drift_items * 0.07,  0.20)

    policy_c = min(
        min(policy_fail_count  * 0.10, 0.30)
        + min(policy_audit_count * 0.05, 0.05)
        + min(mutation_webhooks  * 0.05, 0.05),
        0.30,
    )

    score = round(
        min(bfs_c + jac_c + tfidf_c + anchor_c + signal_c + drift_c + policy_c, 1.0),
        2,
    )

    base_reasons: tuple[str, ...] = (
        f"BFS depth {bfs_depth}/5 → {bfs_c:.2f}",
        f"Jaccard diversity {jaccard_kept_ratio:.0%} → {jac_c:.2f}",
        f"TF-IDF features {tfidf_top_k}/20 → {tfidf_c:.2f}",
        f"Anchors matched {matched_anchors} → {anchor_c:.2f}",
        f"Critical signals {critical_signals} → {signal_c:.2f}",
        f"Helm drift items {helm_drift_items} → {drift_c:.2f}",
    )

    policy_reasons: tuple[str, ...] = ()
    if policy_c > 0:
        policy_reasons = (
            f"Policy violations (fail={policy_fail_count} audit={policy_audit_count}"
            f" webhooks={mutation_webhooks}) → +{policy_c:.2f}",
        )

    return ContextConfidence(
        score=score,
        label=_label(score),
        reasons=base_reasons + policy_reasons,
    )


def _label(score: float) -> str:
    if score >= 0.70:
        return "HIGH"
    if score >= 0.40:
        return "MEDIUM"
    return "LOW"
