"""
PatchTST-based time series anomaly detector for Kubernetes metrics.

Detection strategy
──────────────────
For signals with enough history (>= context_length + prediction_length):

  1. Normalize the signal with the statistics of the history only (the
     window under evaluation is excluded).
  2. Hold out ``CALIBRATION_WINDOWS`` forecasts before the recent stretch (half a
     context); train a small PatchTSTForPrediction model on what comes before
     them (sliding-window forecasting objective), with a fixed seed.
  3. Forecast each window of the recent stretch; keep the worst RMSE.
  4. Anomaly score = worst recent RMSE / baseline RMSE, the baseline being the worst
     RMSE on the held-out windows — forecasts the model was not trained on, so
     a normal series scores about 1 (docs/fix-plan-001.md, H67: the baseline
     used to be the error on windows just trained on, and every normal series
     scored as an anomaly).
     score > warning_threshold  → WARNING
     score > critical_threshold → CRITICAL

For short signals (fewer than ``min_len_for_patchtst`` points):
  Fallback to z-score on the most recent quarter of values.

PatchTST architecture (HuggingFace transformers)
────────────────────────────────────────────────
  • Patches: overlapping windows of length `patch_length` over `context_length`
  • Channel independence: each metric treated as a separate univariate series
  • Positional encoding: relative learned
  • Head: linear projection from [CLS] token pool to prediction_length outputs
"""
from __future__ import annotations

import logging
import zlib
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import torch
import torch.nn as nn

log = logging.getLogger(__name__)

Severity = Literal["normal", "warning", "critical"]


# ─────────────────────────────────────────────────────────────────────────────
# Data types
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SignalSegment:
    """A single univariate metric time series for one K8s entity."""
    entity_uid: str
    metric_name: str
    values: np.ndarray          # shape (T,), float32
    sample_interval_s: int = 60  # seconds between consecutive samples
    # built from the current state, not measured: never evidence (docs/fix-plan-001.md, H43)
    synthetic: bool = False


@dataclass
class AnomalyResult:
    entity_uid: str
    metric_name: str
    severity: Severity
    score: float            # 0.0 = normal; >warning_threshold = anomalous
    n_points: int
    method: str             # "patchtst" | "zscore"
    horizon: str = ""       # "short" | "medium" | "long" | "" (synthetic)
    forecast: np.ndarray = field(default_factory=lambda: np.empty(0))
    actual: np.ndarray = field(default_factory=lambda: np.empty(0))
    synthetic: bool = False

    def to_text(self) -> str:
        horizon_part = f" horizon={self.horizon}" if self.horizon else ""
        return (
            f"signal metric={self.metric_name} entity={self.entity_uid}"
            f"{horizon_part} severity={self.severity} score={self.score:.3f} "
            f"method={self.method} n={self.n_points}"
        )

    @property
    def is_anomalous(self) -> bool:
        return self.severity != "normal"


# ─────────────────────────────────────────────────────────────────────────────
# Detector
# ─────────────────────────────────────────────────────────────────────────────

class PatchTSTDetector:
    """
    Lightweight PatchTST forecasting-based anomaly detector.

    Parameters
    ----------
    patch_length : int
        Number of time steps per patch (PatchTST tokenization granularity).
    context_length : int
        Length of the input window fed to the model.
    prediction_length : int
        Forecast horizon.  Anomaly score is computed on this last segment.
    d_model : int
        Transformer hidden dimension.  Keep small (32–64) for fast CPU training.
    epochs : int
        Training iterations over the sliding windows extracted from history.
    warning_threshold : float
        Anomaly-score ratio above which severity becomes WARNING.
    critical_threshold : float
        Anomaly-score ratio above which severity becomes CRITICAL.
    """

    #: Held-out forecasts the baseline error is measured on (never trained on); as many as the
    #: recent stretch holds with the default configuration (32 / 8), so both maxima compare.
    CALIBRATION_WINDOWS: int = 4

    def __init__(
        self,
        patch_length: int = 8,
        context_length: int = 64,
        prediction_length: int = 8,
        d_model: int = 32,
        num_heads: int = 4,
        num_layers: int = 2,
        epochs: int = 30,
        lr: float = 5e-4,
        warning_threshold: float = 1.8,
        critical_threshold: float = 3.0,
        max_recent_zscore: int = 8,
    ) -> None:
        self.patch_length = patch_length
        self.context_length = context_length
        self.prediction_length = prediction_length
        self.d_model = d_model
        self.num_heads = num_heads
        self.num_layers = num_layers
        self.epochs = epochs
        self.lr = lr
        self.warning_threshold = warning_threshold
        self.critical_threshold = critical_threshold
        self.max_recent_zscore = max_recent_zscore

    # ─────────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────────

    @property
    def recent_length(self) -> int:
        """The recent stretch whose forecasts are scored: half a context, whole forecasts."""
        return max(1, self.context_length // 2 // self.prediction_length) * self.prediction_length

    @property
    def min_len_for_patchtst(self) -> int:
        """One training window, the held-out forecasts, and the recent stretch."""
        return (self.context_length + self.prediction_length * (self.CALIBRATION_WINDOWS + 1)
                + self.recent_length)

    def detect(self, segment: SignalSegment) -> AnomalyResult:
        """Detect anomaly in a single metric segment."""
        values = np.asarray(segment.values, dtype=np.float32)

        if len(values) >= self.min_len_for_patchtst:
            try:
                return self._detect_patchtst(
                    values, segment.entity_uid, segment.metric_name
                )
            except Exception as exc:
                log.warning(
                    "PatchTST detection failed (%s) — falling back to z-score", exc
                )

        return self._detect_zscore(values, segment.entity_uid, segment.metric_name)

    # ─────────────────────────────────────────────────────────────────────────
    # PatchTST path
    # ─────────────────────────────────────────────────────────────────────────

    def _detect_patchtst(
        self, values: np.ndarray, entity_uid: str, metric_name: str
    ) -> AnomalyResult:
        from transformers import PatchTSTConfig, PatchTSTForPrediction

        ctx_len, pred_len = self.context_length, self.prediction_length
        eval_start = len(values) - self.recent_length  # first point of the scored stretch
        cal_start = eval_start - pred_len * self.CALIBRATION_WINDOWS

        # ── Normalise on the history only (the evaluated window would leak) ──
        mu = float(values[:eval_start].mean())
        sigma = float(values[:eval_start].std())
        sigma = sigma if sigma > 1e-8 else 1.0
        norm = (values - mu) / sigma

        # ── Training windows end before the held-out forecasts ───────────────
        train_signal = norm[:cal_start]

        # ── Build model ───────────────────────────────────────────────────────
        config = PatchTSTConfig(
            num_input_channels=1,
            context_length=ctx_len,
            patch_length=self.patch_length,
            stride=self.patch_length,          # HF uses this for positional enc
            prediction_length=pred_len,
            d_model=self.d_model,
            num_attention_heads=self.num_heads,
            num_hidden_layers=self.num_layers,
            ffn_dim=self.d_model * 4,
            dropout=0.1,
            head_dropout=0.0,
            pooling_type=None,
            channel_attention=False,
            scaling="std",
            loss="mse",
            pre_norm=True,
        )

        windows = _sliding_windows(train_signal, ctx_len, pred_len)
        if not windows:
            return self._detect_zscore(values, entity_uid, metric_name)

        # A fixed seed per series, without touching the global torch RNG: the same input gives
        # the same score from one run to the next
        with torch.random.fork_rng(devices=[]):
            torch.manual_seed(zlib.crc32(f"{entity_uid}/{metric_name}".encode()))
            model = PatchTSTForPrediction(config)
            optimizer = torch.optim.Adam(model.parameters(), lr=self.lr)
            model.train()
            for _ in range(self.epochs):
                for ctx, tgt in windows:
                    optimizer.zero_grad()
                    past = torch.from_numpy(ctx).float().unsqueeze(0).unsqueeze(-1)    # (1,ctx,1)
                    future = torch.from_numpy(tgt).float().unsqueeze(0).unsqueeze(-1)  # (1,pred,1)
                    model(past_values=past, future_values=future).loss.backward()
                    optimizer.step()

        # ── Baseline: forecasts of the held-out windows ───────────────────────
        held_out = [
            (norm[t - ctx_len: t].copy(), norm[t: t + pred_len].copy())
            for t in range(cal_start, eval_start, pred_len)
        ]
        # the worst held-out forecast, like the score's worst recent one: a max over as many
        # windows on each side, so a normal series scores about 1
        baseline_rmse = _baseline_rmse(model, held_out, worst=True)

        # ── Score the recent stretch: its worst forecast against the baseline ─
        # (the last forecast alone missed a shift that began a few windows ago)
        eval_rmse, predicted_norm, eval_tgt = -1.0, None, None
        for t in range(eval_start, len(norm) - pred_len + 1, pred_len):
            ctx, tgt = norm[t - ctx_len: t], norm[t: t + pred_len]
            with torch.no_grad():
                past = torch.from_numpy(ctx).float().unsqueeze(0).unsqueeze(-1)
                pred = model(past_values=past).prediction_outputs.squeeze().numpy()
            rmse = float(np.sqrt(np.mean((pred - tgt) ** 2)))
            if rmse > eval_rmse:
                eval_rmse, predicted_norm, eval_tgt = rmse, pred, tgt
        score = eval_rmse / max(baseline_rmse, 1e-8)

        log.debug(
            "PatchTST %s/%s: eval_rmse=%.4f baseline=%.4f score=%.3f",
            entity_uid, metric_name, eval_rmse, baseline_rmse, score,
        )

        # De-normalise forecast for readability
        forecast = predicted_norm * sigma + mu
        actual = eval_tgt * sigma + mu

        return AnomalyResult(
            entity_uid=entity_uid,
            metric_name=metric_name,
            severity=self._severity(score),
            score=score,
            n_points=len(values),
            method="patchtst",
            forecast=forecast,
            actual=actual,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Z-score fallback
    # ─────────────────────────────────────────────────────────────────────────

    def _detect_zscore(
        self, values: np.ndarray, entity_uid: str, metric_name: str
    ) -> AnomalyResult:
        if len(values) < 3:
            return AnomalyResult(
                entity_uid=entity_uid, metric_name=metric_name,
                severity="normal", score=0.0, n_points=len(values), method="zscore",
            )
        mu, sigma = values.mean(), values.std()
        if sigma < 1e-8:
            return AnomalyResult(
                entity_uid=entity_uid, metric_name=metric_name,
                severity="normal", score=0.0, n_points=len(values), method="zscore",
            )
        z = np.abs((values - mu) / sigma)
        # Cap the recent tail to an absolute size. Without it, a quarter of an
        # ever-growing cumulative window means the tail itself grows, so a
        # single isolated blip stays inside "recent" -- and therefore scored
        # against -- for many ticks after it happened, not just the tick it
        # occurred on. Bounding the tail makes a one-off blip age out almost
        # immediately, the way a real sustained deviation (which keeps
        # refilling the tail with bad points) does not. Mirrors the identical
        # fix in the companion PatchTST repo's ZScoreDetector -- this fallback
        # is meant to behave the same way (see that class's docstring).
        recent_q = min(max(1, len(z) // 4), self.max_recent_zscore)
        score = float(z[-recent_q:].max())

        return AnomalyResult(
            entity_uid=entity_uid, metric_name=metric_name,
            severity=self._severity(score), score=score,
            n_points=len(values), method="zscore",
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _severity(self, score: float) -> Severity:
        if score >= self.critical_threshold:
            return "critical"
        if score >= self.warning_threshold:
            return "warning"
        return "normal"


# ─────────────────────────────────────────────────────────────────────────────
# Module-level helpers
# ─────────────────────────────────────────────────────────────────────────────

def _sliding_windows(
    signal: np.ndarray, context_length: int, prediction_length: int
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Extract non-overlapping (context, target) pairs from signal."""
    step = max(1, context_length // 4)  # 75 % overlap
    pairs: list[tuple[np.ndarray, np.ndarray]] = []
    window = context_length + prediction_length
    for i in range(0, len(signal) - window + 1, step):
        ctx = signal[i: i + context_length].copy()
        tgt = signal[i + context_length: i + window].copy()
        pairs.append((ctx, tgt))
    return pairs


def _baseline_rmse(
    model: nn.Module, windows: list[tuple[np.ndarray, np.ndarray]], worst: bool = False
) -> float:
    """Average (or worst) RMSE of model on a set of (context, target) windows."""
    errors: list[float] = []
    model.eval()
    for ctx, tgt in windows:
        with torch.no_grad():
            past = torch.from_numpy(ctx).float().unsqueeze(0).unsqueeze(-1)
            pred = model(past_values=past).prediction_outputs.squeeze().numpy()
        errors.append(float(np.sqrt(np.mean((pred - tgt) ** 2))))
    if not errors:
        return 1.0
    return float(np.max(errors) if worst else np.mean(errors))
