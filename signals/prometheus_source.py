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

All metrics fall back gracefully: if a query returns no data, the entry
is simply omitted from the returned segments — SignalAnalyzer falls back
to synthetic history for that metric.
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
    '{{pod="{pod}",namespace="{namespace}",container!="POD"}}[{step}s])'
)
_Q_MEMORY = (
    'container_memory_working_set_bytes'
    '{{pod="{pod}",namespace="{namespace}",container!="POD"}}'
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
            values = self._range_query(query, lookback, step)
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
            values = self._range_query(query, lookback, step)
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
        self, query: str, lookback_s: int, step_s: int
    ) -> np.ndarray | None:
        """
        Run a Prometheus range query and return a float32 numpy array.
        Returns None on error or empty result.
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
            data = resp.json()
            results = data.get("data", {}).get("result", [])
            if not results:
                return None
            # Take the first matching series; aggregate multiple by mean
            arrays = [
                np.array([float(v) for _, v in r["values"]], dtype=np.float32)
                for r in results
            ]
            return np.mean(np.vstack(arrays), axis=0) if len(arrays) > 1 else arrays[0]
        except requests.Timeout:
            log.debug("prometheus range query timed out: %s", query[:80])
            return None
        except (requests.RequestException, KeyError, ValueError) as exc:
            log.debug("prometheus range query failed (%s): %s", exc, query[:80])
            return None

    def _headers(self) -> dict[str, str]:
        if self.token:
            return {"Authorization": f"Bearer {self.token}"}
        return {}
