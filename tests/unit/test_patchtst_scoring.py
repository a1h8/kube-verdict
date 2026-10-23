"""PatchTST scores a normal series as normal — docs/fix-plan-001.md, H67.

Rule fixed before the first measurement (2026-10-22), with the production configuration
(context 64, horizon 8, 30 epochs, thresholds 1.8 / 3.0), 200 points, data seeds 0-4:
- white noise, a noisy sine and a constant with small noise -> `normal` for every seed;
- the same noisy sine whose last 8 points (the evaluated forecast) are shifted by 5 standard
  deviations -> at least `warning` for every seed.
A failure is not met by moving a threshold.

Design history, same rule throughout: scoring the last forecast alone passed it but missed a shift
that began a few windows earlier (test_patchtst's final 10 %); the worst recent forecast against
the mean held-out error failed it (a sine at 1.93); worst against worst passes (normal <= 1.47,
spike >= 18.1), and on data seeds 5-14, never used while designing: normal <= 1.57, spike >= 18.2.
"""
from __future__ import annotations

import numpy as np
import pytest

from signals.patchtst_detector import PatchTSTDetector, SignalSegment

N, SEEDS = 200, range(5)


def _noise(rng):
    return rng.normal(0, 1, N)


def _sine(rng):
    return np.sin(np.linspace(0, 8 * np.pi, N)) + rng.normal(0, 0.1, N)


def _constant(rng):
    return 5.0 + rng.normal(0, 0.01, N)


def _final_spike(rng):
    s = _sine(rng)
    s[-8:] += 5 * s[:-8].std()
    return s


@pytest.fixture(scope="module")
def detector():
    return PatchTSTDetector()


def _detect(detector, shape, seed):
    values = shape(np.random.default_rng(seed)).astype(np.float32)
    return detector.detect(SignalSegment("Pod/prod/api", shape.__name__, values))


@pytest.mark.parametrize("shape", [_noise, _sine, _constant])
def test_a_normal_series_is_normal(detector, shape):
    results = [_detect(detector, shape, s) for s in SEEDS]
    assert all(r.method == "patchtst" for r in results)
    assert [r.severity for r in results] == ["normal"] * len(SEEDS), [round(r.score, 2) for r in results]


def test_a_final_spike_is_anomalous(detector):                                    # control
    results = [_detect(detector, _final_spike, s) for s in SEEDS]
    assert all(r.is_anomalous for r in results), [round(r.score, 2) for r in results]


def test_the_same_series_scores_the_same(detector):
    a, b = _detect(detector, _sine, 0), _detect(detector, _sine, 0)
    assert a.score == b.score


def test_the_global_torch_rng_is_left_alone(detector):
    import torch
    torch.manual_seed(123)
    expected = torch.rand(1)
    torch.manual_seed(123)
    _detect(detector, _sine, 0)
    assert torch.rand(1) == expected


def test_too_short_for_the_held_out_windows_uses_zscore(detector):
    values = np.random.default_rng(0).normal(0, 1, detector.min_len_for_patchtst - 1)
    assert detector.detect(SignalSegment("u", "m", values.astype(np.float32))).method == "zscore"
