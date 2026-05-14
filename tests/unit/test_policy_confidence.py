"""Unit tests for OPA / Kyverno policy-violation confidence boost in compute_confidence()."""
from __future__ import annotations

import pytest

from rca.confidence import compute_confidence


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base(**overrides):
    """Minimal base inputs that produce a LOW score (≈ 0.15), then apply overrides."""
    params = dict(
        bfs_depth=1,
        jaccard_kept_ratio=0.2,
        tfidf_top_k=3,
        matched_anchors=0,
        critical_signals=1,
    )
    params.update(overrides)
    return compute_confidence(**params)


# ---------------------------------------------------------------------------
# Policy fail count
# ---------------------------------------------------------------------------

class TestPolicyFailCount:
    def test_no_violation_no_boost(self):
        c = _base(policy_fail_count=0)
        c_boosted = _base(policy_fail_count=0, policy_audit_count=0, mutation_webhooks=0)
        assert c.score == c_boosted.score

    def test_one_fail_adds_010(self):
        base = _base()
        boosted = _base(policy_fail_count=1)
        assert round(boosted.score - base.score, 2) == pytest.approx(0.10, abs=0.01)

    def test_three_fail_adds_030(self):
        base = _base()
        boosted = _base(policy_fail_count=3)
        assert round(boosted.score - base.score, 2) == pytest.approx(0.30, abs=0.01)

    def test_fail_capped_at_030(self):
        boosted_3 = _base(policy_fail_count=3)
        boosted_10 = _base(policy_fail_count=10)
        assert boosted_3.score == boosted_10.score

    def test_score_never_exceeds_1(self):
        c = compute_confidence(
            bfs_depth=5,
            jaccard_kept_ratio=1.0,
            tfidf_top_k=20,
            matched_anchors=3,
            critical_signals=5,
            policy_fail_count=10,
            policy_audit_count=5,
            mutation_webhooks=5,
        )
        assert c.score == 1.00


# ---------------------------------------------------------------------------
# Policy audit count
# ---------------------------------------------------------------------------

class TestPolicyAuditCount:
    def test_one_audit_adds_005(self):
        base = _base()
        boosted = _base(policy_audit_count=1)
        assert round(boosted.score - base.score, 2) == pytest.approx(0.05, abs=0.01)

    def test_audit_capped_at_005(self):
        boosted_1 = _base(policy_audit_count=1)
        boosted_10 = _base(policy_audit_count=10)
        assert boosted_1.score == boosted_10.score


# ---------------------------------------------------------------------------
# Mutation webhooks
# ---------------------------------------------------------------------------

class TestMutationWebhooks:
    def test_one_webhook_adds_005(self):
        base = _base()
        boosted = _base(mutation_webhooks=1)
        assert round(boosted.score - base.score, 2) == pytest.approx(0.05, abs=0.01)

    def test_webhook_capped_at_005(self):
        boosted_1 = _base(mutation_webhooks=1)
        boosted_10 = _base(mutation_webhooks=10)
        assert boosted_1.score == boosted_10.score


# ---------------------------------------------------------------------------
# Combined policy boost cap at 0.30
# ---------------------------------------------------------------------------

class TestCombinedPolicyCap:
    def test_combined_capped_at_030(self):
        """fail=3 (0.30) + audit=1 (0.05) + webhooks=1 (0.05) → capped at 0.30."""
        combined = _base(policy_fail_count=3, policy_audit_count=1, mutation_webhooks=1)
        max_boost = _base(policy_fail_count=3)
        assert combined.score == max_boost.score

    def test_fail_plus_audit_no_cap(self):
        """fail=1 (0.10) + audit=1 (0.05) = 0.15 < cap → additive."""
        base = _base()
        combined = _base(policy_fail_count=1, policy_audit_count=1)
        assert round(combined.score - base.score, 2) == pytest.approx(0.15, abs=0.01)


# ---------------------------------------------------------------------------
# Policy reasons
# ---------------------------------------------------------------------------

class TestPolicyReasons:
    def test_no_policy_five_reasons(self):
        c = compute_confidence(
            bfs_depth=2, jaccard_kept_ratio=0.5, tfidf_top_k=5,
            matched_anchors=0, critical_signals=0,
        )
        assert len(c.reasons) == 6

    def test_with_policy_six_reasons(self):
        c = compute_confidence(
            bfs_depth=2, jaccard_kept_ratio=0.5, tfidf_top_k=5,
            matched_anchors=0, critical_signals=0,
            policy_fail_count=1,
        )
        assert len(c.reasons) == 7

    def test_policy_reason_content(self):
        c = compute_confidence(
            bfs_depth=2, jaccard_kept_ratio=0.5, tfidf_top_k=5,
            matched_anchors=0, critical_signals=0,
            policy_fail_count=2, policy_audit_count=1, mutation_webhooks=1,
        )
        policy_line = c.reasons[-1]
        assert "fail=2" in policy_line
        assert "audit=1" in policy_line
        assert "webhooks=1" in policy_line

    def test_zero_policy_no_extra_reason(self):
        c = compute_confidence(
            bfs_depth=2, jaccard_kept_ratio=0.5, tfidf_top_k=5,
            matched_anchors=0, critical_signals=0,
            policy_fail_count=0, policy_audit_count=0, mutation_webhooks=0,
        )
        assert len(c.reasons) == 6


# ---------------------------------------------------------------------------
# Backward compatibility — existing call sites without policy params
# ---------------------------------------------------------------------------

class TestBackwardCompat:
    def test_existing_signature_unchanged(self):
        """Calls without policy params must produce identical results."""
        c1 = compute_confidence(
            bfs_depth=3,
            jaccard_kept_ratio=0.8,
            tfidf_top_k=15,
            matched_anchors=4,
            critical_signals=2,
        )
        c2 = compute_confidence(
            bfs_depth=3,
            jaccard_kept_ratio=0.8,
            tfidf_top_k=15,
            matched_anchors=4,
            critical_signals=2,
            policy_fail_count=0,
            policy_audit_count=0,
            mutation_webhooks=0,
        )
        assert c1.score == c2.score
        assert c1.label == c2.label


# ---------------------------------------------------------------------------
# Case 05: NetworkPolicy scenario (matches cases/05_networkpolicy_blocked)
# ---------------------------------------------------------------------------

class TestNetworkPolicyScenario:
    def test_policy_fail_lifts_low_to_medium(self):
        """Without policy report this scenario would be LOW; Kyverno FAIL → HIGH."""
        without_policy = compute_confidence(
            bfs_depth=3,
            jaccard_kept_ratio=0.75,
            tfidf_top_k=10,
            matched_anchors=1,
            critical_signals=1,
        )
        with_policy = compute_confidence(
            bfs_depth=3,
            jaccard_kept_ratio=0.75,
            tfidf_top_k=10,
            matched_anchors=1,
            critical_signals=1,
            policy_fail_count=1,
        )
        assert without_policy.label in ("LOW", "MEDIUM")
        assert with_policy.score > without_policy.score
        assert with_policy.score >= 0.60
