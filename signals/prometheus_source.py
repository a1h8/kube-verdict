"""
PrometheusMetricSource — fetches real time-series from Prometheus for
use by SignalAnalyzer / PatchTSTDetector.

Three analysis horizons are supported:

  short  — last 1 h,  step 1 m   (~60 pts)  : is it getting worse right now?
  medium — last 24 h, step 15 m  (~96 pts)  : when did it start / what trend?
  long   — last 7 d,  step 1 h   (~168 pts) : anomaly vs normal weekly pattern?

Metrics fetched per entity type
────────────────────────────────
  Pod         : restart_count  (increase in restarts over window)
                cpu_usage      (rate of CPU seconds)
                memory_bytes   (working set bytes)
  Deployment  : ready_ratio    (available / desired replicas)
  StatefulSet : ready_ratio    (ready / desired replicas)

A query that returns no data omits its entry. A query that fails (HTTP error,
timeout, malformed answer) omits it too and is recorded in ``errors``: an
unreachable Prometheus is a failed collection, not a cluster without metrics
(docs/fix-plan-001.md, H68). Several series (one per container) are aligned on
their timestamps and summed for a pod; a ratio is averaged.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Literal

import numpy as np
import requests

from signals.patchtst_detector import SignalSegment

log = logging.getLogger(__name__)

Horizon = Literal["short", "medium", "long"]

# (lookback_seconds, step_seconds, label)
_HORIZON_CONFIG: dict[Horizon, tuple[int, int]] = {
    "short":  (3_600,        60),    # 1 h  / 1 m
    "medium": (86_400,      900),    # 24 h / 15 m
    "long":   (604_800,   3_600),    # 7 d  / 1 h
}

# PromQL templates — {pod}, {namespace}, {deployment}, {statefulset}, {step}s
_Q_RESTARTS = (
    'increase(kube_pod_container_status_restarts_total'
    '{{pod="{pod}",namespace="{namespace}"}}[{step}s])'
)
_Q_CPU = (
    'rate(container_cpu_usage_seconds_total'
    '{{pod="{pod}",namespace="{namespace}",container!="POD",container!=""}}[{step}s])'
)
_Q_MEMORY = (
    'container_memory_working_set_bytes'
    '{{pod="{pod}",namespace="{namespace}",container!="POD",container!=""}}'
)
_Q_DEP_READINESS = (
    'kube_deployment_status_replicas_available'
    '{{deployment="{deployment}",namespace="{namespace}"}}'
    ' / kube_deployment_status_replicas_desired'
    '{{deployment="{deployment}",namespace="{namespace}"}}'
)
_Q_STS_READINESS = (
    'kube_statefulset_status_replicas_ready'
    '{{statefulset="{statefulset}",namespace="{namespace}"}}'
    ' / kube_statefulset_status_replicas'
    '{{statefulset="{statefulset}",namespace="{namespace}"}}'
)


@dataclass
class HorizonSegment:
    """A SignalSegment tagged with its analysis horizon."""
    segment: SignalSegment
    horizon: Horizon


class PrometheusMetricSource:
    """
    Fetches multi-horizon time series for K8s entities from Prometheus.

    Parameters
    ----------
    url:     Prometheus base URL (e.g. http://prometheus:9090)
    token:   Optional Bearer token
    timeout: Per-request timeout in seconds
    """

    def __init__(
        self,
        url: str,
        token: str | None = None,
        timeout: int = 30,
    ) -> None:
        self.url = url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.errors: list[str] = []      # failed queries, for the caller to report

    # ------------------------------------------------------------------
    # Per-entity segment fetchers
    # ------------------------------------------------------------------

    def pod_segments(
        self, uid: str, pod: str, namespace: str, horizons: list[Horizon] | None = None
    ) -> list[HorizonSegment]:
        """Fetch restart_count, cpu_usage, memory_bytes for a Pod."""
        result: list[HorizonSegment] = []
        for h in (horizons or list(_HORIZON_CONFIG)):
            lookback, step = _HORIZON_CONFIG[h]
            subs = {"pod": pod, "namespace": namespace, "step": str(step)}

            for metric_name, query in [
                ("restart_count", _Q_RESTARTS.format(**subs)),
                ("cpu_usage",     _Q_CPU.format(**subs)),
                ("memory_bytes",  _Q_MEMORY.format(**subs)),
            ]:
                values = self._range_query(query, lookback, step)
                if values is not None and len(values) >= 5:
                    result.append(HorizonSegment(
                        segment=SignalSegment(
                            entity_uid=uid,
                            metric_name=metric_name,
                            values=values,
                            sample_interval_s=step,
                        ),
                        horizon=h,
                    ))
        return result

    def deployment_segments(
        self, uid: str, deployment: str, namespace: str,
        horizons: list[Horizon] | None = None,
    ) -> list[HorizonSegment]:
        """Fetch ready_ratio for a Deployment."""
        result: list[HorizonSegment] = []
        for h in (horizons or list(_HORIZON_CONFIG)):
            lookback, step = _HORIZON_CONFIG[h]
            query = _Q_DEP_READINESS.format(
                deployment=deployment, namespace=namespace
            )
            values = self._range_query(query, lookback, step, how="mean")
            if values is not None and len(values) >= 5:
                result.append(HorizonSegment(
                    segment=SignalSegment(
                        entity_uid=uid,
                        metric_name="ready_ratio",
                        values=values,
                        sample_interval_s=step,
                    ),
                    horizon=h,
                ))
        return result

    def statefulset_segments(
        self, uid: str, statefulset: str, namespace: str,
        horizons: list[Horizon] | None = None,
    ) -> list[HorizonSegment]:
        """Fetch ready_ratio for a StatefulSet."""
        result: list[HorizonSegment] = []
        for h in (horizons or list(_HORIZON_CONFIG)):
            lookback, step = _HORIZON_CONFIG[h]
            query = _Q_STS_READINESS.format(
                statefulset=statefulset, namespace=namespace
            )
            values = self._range_query(query, lookback, step, how="mean")
            if values is not None and len(values) >= 5:
                result.append(HorizonSegment(
                    segment=SignalSegment(
                        entity_uid=uid,
                        metric_name="ready_ratio",
                        values=values,
                        sample_interval_s=step,
                    ),
                    horizon=h,
                ))
        return result

    # ------------------------------------------------------------------
    # Low-level Prometheus query
    # ------------------------------------------------------------------

    def _range_query(
        self, query: str, lookback_s: int, step_s: int, how: str = "sum"
    ) -> np.ndarray | None:
        """
        Run a Prometheus range query and return a float32 numpy array.
        Returns None on an empty result, and on a failure, which is recorded in ``errors``.
        Several series are aligned on their timestamps and combined (``sum`` or ``mean``).
        """
        now = int(time.time())
        params = {
            "query": query,
            "start": now - lookback_s,
            "end":   now,
            "step":  step_s,
        }
        try:
            resp = requests.get(
                f"{self.url}/api/v1/query_range",
                params=params,
                headers=self._headers(),
                timeout=self.timeout,
            )
            resp.raise_for_status()
            results = resp.json().get("data", {}).get("result", [])
            if not results:
                return None
            return _combine([r["values"] for r in results], how)
        except (requests.RequestException, KeyError, TypeError, ValueError) as exc:
            reason = "timed out" if isinstance(exc, requests.Timeout) else f"{type(exc).__name__}: {exc}"
            log.warning("prometheus range query failed (%s): %s", reason, query[:80])
            self.errors.append(f"{reason} — {query[:80]}")
            return None

    def _headers(self) -> dict[str, str]:
        if self.token:
            return {"Authorization": f"Bearer {self.token}"}
        return {}


def _combine(series: list[list], how: str) -> np.ndarray:
    """Align series on their timestamps; sum them (a pod's containers) or average them.

    A container absent at a timestamp (started later, or restarted) counts 0 in a sum and is
    left out of a mean; ``np.vstack`` on series of different lengths used to drop the metric.
    """
    by_ts: dict[float, list[float]] = {}
    for values in series:
        for ts, v in values:
            by_ts.setdefault(float(ts), []).append(float(v))
    points = [by_ts[ts] for ts in sorted(by_ts)]
    combine = sum if how == "sum" else (lambda vs: sum(vs) / len(vs))
    return np.array([combine(vs) for vs in points], dtype=np.float32)
