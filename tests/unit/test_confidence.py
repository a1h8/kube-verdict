"""Unit tests for rca/confidence.py — compute_confidence() and ContextConfidence."""
from __future__ import annotations

import pytest

from rca.confidence import ContextConfidence, compute_confidence


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _conf(**kwargs) -> ContextConfidence:
    """Call compute_confidence with required defaults."""
    defaults = dict(
        bfs_depth=3,
        jaccard_kept_ratio=0.8,
        tfidf_top_k=15,
        matched_anchors=4,
        critical_signals=2,
    )
    defaults.update(kwargs)
    return compute_confidence(**defaults)


# ---------------------------------------------------------------------------
# Score bounds
# ---------------------------------------------------------------------------

class TestScoreBounds:
    def test_score_is_float_in_0_1(self):
        c = _conf()
        assert 0.0 <= c.score <= 1.0

    def test_all_zero_inputs_give_zero_score(self):
        c = compute_confidence(
            bfs_depth=0,
            jaccard_kept_ratio=0.0,
            tfidf_top_k=0,
            matched_anchors=0,
            critical_signals=0,
        )
        assert c.score == 0.0

    def test_max_inputs_give_score_1(self):
        c = compute_confidence(
            bfs_depth=5,
            jaccard_kept_ratio=1.0,
            tfidf_top_k=20,
            matched_anchors=3,       # 3 × 0.05 = 0.15 → capped
            critical_signals=5,      # 5 × 0.03 = 0.15 → capped
        )
        assert c.score == 1.00

    def test_score_rounded_to_2_decimals(self):
        c = _conf(bfs_depth=1, jaccard_kept_ratio=0.333)
        # score should not have more than 2 decimal places
        assert c.score == round(c.score, 2)

    def test_oversized_inputs_are_capped_at_1(self):
        c = compute_confidence(
            bfs_depth=100,
            jaccard_kept_ratio=2.0,
            tfidf_top_k=500,
            matched_anchors=100,
            critical_signals=100,
        )
        assert c.score == 1.00


# ---------------------------------------------------------------------------
# Label thresholds
# ---------------------------------------------------------------------------

class TestLabel:
    def test_high_when_score_ge_070(self):
        # BFS=5 (0.25) + jaccard=1.0 (0.25) + tfidf=20 (0.20) = 0.70 exactly
        c = compute_confidence(
            bfs_depth=5,
            jaccard_kept_ratio=1.0,
            tfidf_top_k=20,
            matched_anchors=0,
            critical_signals=0,
        )
        assert c.score == 0.70
        assert c.label == "HIGH"

    def test_medium_when_score_ge_040(self):
        # BFS=3 (0.15) + jaccard=0.5 (0.125) + tfidf=10 (0.10) = 0.375 → LOW
        # bump tfidf to 14 → 0.14 → total 0.415
        c = compute_confidence(
            bfs_depth=3,
            jaccard_kept_ratio=0.5,
            tfidf_top_k=14,
            matched_anchors=0,
            critical_signals=0,
        )
        assert c.label == "MEDIUM"

    def test_low_when_score_lt_040(self):
        c = compute_confidence(
            bfs_depth=1,
            jaccard_kept_ratio=0.0,
            tfidf_top_k=0,
            matched_anchors=0,
            critical_signals=0,
        )
        assert c.label == "LOW"

    def test_score_exactly_040_is_medium(self):
        # BFS=4 (0.20) + jaccard=0.8 (0.20) = 0.40
        c = compute_confidence(
            bfs_depth=4,
            jaccard_kept_ratio=0.8,
            tfidf_top_k=0,
            matched_anchors=0,
            critical_signals=0,
        )
        assert c.score == 0.40
        assert c.label == "MEDIUM"

    def test_score_exactly_070_is_high(self):
        c = compute_confidence(
            bfs_depth=5,
            jaccard_kept_ratio=1.0,
            tfidf_top_k=20,
            matched_anchors=0,
            critical_signals=0,
        )
        assert c.score == 0.70
        assert c.label == "HIGH"


# ---------------------------------------------------------------------------
# Individual component weights
# ---------------------------------------------------------------------------

class TestComponents:
    def test_bfs_depth_5_contributes_025(self):
        c = compute_confidence(
            bfs_depth=5,
            jaccard_kept_ratio=0.0,
            tfidf_top_k=0,
            matched_anchors=0,
            critical_signals=0,
        )
        assert c.score == pytest.approx(0.25, abs=0.01)

    def test_bfs_depth_partial(self):
        c = compute_confidence(
            bfs_depth=2,
            jaccard_kept_ratio=0.0,
            tfidf_top_k=0,
            matched_anchors=0,
            critical_signals=0,
        )
        # 2/5 * 0.25 = 0.10
        assert c.score == pytest.approx(0.10, abs=0.01)

    def test_jaccard_full_contributes_025(self):
        c = compute_confidence(
            bfs_depth=0,
            jaccard_kept_ratio=1.0,
            tfidf_top_k=0,
            matched_anchors=0,
            critical_signals=0,
        )
        assert c.score == pytest.approx(0.25, abs=0.01)

    def test_tfidf_20_contributes_020(self):
        c = compute_confidence(
            bfs_depth=0,
            jaccard_kept_ratio=0.0,
            tfidf_top_k=20,
            matched_anchors=0,
            critical_signals=0,
        )
        assert c.score == pytest.approx(0.20, abs=0.01)

    def test_anchor_cap_at_015(self):
        # 10 anchors × 0.05 = 0.50 → capped at 0.15
        c = compute_confidence(
            bfs_depth=0,
            jaccard_kept_ratio=0.0,
            tfidf_top_k=0,
            matched_anchors=10,
            critical_signals=0,
        )
        assert c.score == pytest.approx(0.15, abs=0.01)

    def test_anchor_3_gives_015(self):
        # 3 × 0.05 = 0.15 → exactly capped
        c = compute_confidence(
            bfs_depth=0,
            jaccard_kept_ratio=0.0,
            tfidf_top_k=0,
            matched_anchors=3,
            critical_signals=0,
        )
        assert c.score == pytest.approx(0.15, abs=0.01)

    def test_anchor_1_gives_005(self):
        c = compute_confidence(
            bfs_depth=0,
            jaccard_kept_ratio=0.0,
            tfidf_top_k=0,
            matched_anchors=1,
            critical_signals=0,
        )
        assert c.score == pytest.approx(0.05, abs=0.01)

    def test_critical_signals_cap_at_015(self):
        # 10 × 0.03 = 0.30 → capped at 0.15
        c = compute_confidence(
            bfs_depth=0,
            jaccard_kept_ratio=0.0,
            tfidf_top_k=0,
            matched_anchors=0,
            critical_signals=10,
        )
        assert c.score == pytest.approx(0.15, abs=0.01)

    def test_critical_signals_5_gives_015(self):
        c = compute_confidence(
            bfs_depth=0,
            jaccard_kept_ratio=0.0,
            tfidf_top_k=0,
            matched_anchors=0,
            critical_signals=5,
        )
        assert c.score == pytest.approx(0.15, abs=0.01)

    def test_critical_signals_default_is_0(self):
        c1 = compute_confidence(
            bfs_depth=2,
            jaccard_kept_ratio=0.5,
            tfidf_top_k=10,
            matched_anchors=2,
        )
        c2 = compute_confidence(
            bfs_depth=2,
            jaccard_kept_ratio=0.5,
            tfidf_top_k=10,
            matched_anchors=2,
            critical_signals=0,
        )
        assert c1.score == c2.score


# ---------------------------------------------------------------------------
# Reasons field
# ---------------------------------------------------------------------------

class TestReasons:
    def test_returns_five_reasons(self):
        c = _conf()
        assert len(c.reasons) == 6

    def test_reasons_mention_each_component(self):
        c = _conf(bfs_depth=3, matched_anchors=2, critical_signals=4)
        joined = " ".join(c.reasons)
        assert "BFS" in joined
        assert "Jaccard" in joined
        assert "TF-IDF" in joined
        assert "Anchors" in joined
        assert "Critical" in joined

    def test_reasons_contain_bfs_depth_value(self):
        c = _conf(bfs_depth=4)
        assert "4/5" in c.reasons[0]

    def test_reasons_contain_matched_anchors_value(self):
        c = _conf(matched_anchors=7)
        assert "7" in c.reasons[3]

    def test_reasons_contain_critical_signals_value(self):
        c = _conf(critical_signals=6)
        assert "6" in c.reasons[4]


# ---------------------------------------------------------------------------
# ContextConfidence is frozen
# ---------------------------------------------------------------------------

class TestFrozen:
    def test_immutable(self):
        c = _conf()
        with pytest.raises((AttributeError, TypeError)):
            c.score = 0.9  # type: ignore[misc]

    def test_hash_stable(self):
        c = _conf()
        assert hash(c) == hash(c)


# ---------------------------------------------------------------------------
# Real-world scenario smoke tests
# ---------------------------------------------------------------------------

class TestScenarios:
    def test_crashloop_with_anchors(self):
        """Pod restarting, drift detected, 2 anchors — should be MEDIUM+."""
        c = compute_confidence(
            bfs_depth=3,
            jaccard_kept_ratio=0.9,
            tfidf_top_k=12,
            matched_anchors=2,
            critical_signals=3,   # 1 seed + 2 drift
        )
        assert c.label in ("MEDIUM", "HIGH")

    def test_minimal_cluster_low_confidence(self):
        """Single namespace, no Helm, no anchors — likely LOW."""
        c = compute_confidence(
            bfs_depth=1,
            jaccard_kept_ratio=0.3,
            tfidf_top_k=3,
            matched_anchors=0,
            critical_signals=1,
        )
        assert c.label == "LOW"

    def test_rich_context_high_confidence(self):
        """Full Prometheus + anchors + deep BFS — should be HIGH."""
        c = compute_confidence(
            bfs_depth=5,
            jaccard_kept_ratio=1.0,
            tfidf_top_k=20,
            matched_anchors=4,
            critical_signals=5,
        )
        assert c.label == "HIGH"
        assert c.score == 1.00
