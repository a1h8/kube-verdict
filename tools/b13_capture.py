#!/usr/bin/env python3
"""B13 — capture live incident artifacts from real scenarios on a k3s cluster.

The B13 credibility jump: move from synthetic fixtures (h001–h010) to real
captured incidents. For each scenario this driver

  1. ``kubectl apply``-s the scenario manifest into the cluster,
  2. waits until the **real** failure state actually appears (CrashLoopBackOff,
     OOMKilled, ImagePullBackOff, …) — not a hand-written status,
  3. runs the canonical investigation pipeline (``services.run_investigation``)
     against the **live** cluster, with the real Prometheus endpoint wired in,
  4. freezes the deterministic verdict triple (``verdict`` / ``risk`` /
     ``rollback_available``) as ``tests/golden/real_00N.json`` and appends a
     provenance block to ``docs/evidence/prometheus-live.md``,
  5. tears the scenario back down (unless ``--no-cleanup``).

The frozen golden is intentionally the same 3-key shape as the synthetic
``tests/golden/baseline.json`` so the B11 regression guard can diff it. The
credibility is in the *provenance*: the inputs came from a live cluster, which
the evidence doc records (cluster, image digest of the run, live Prometheus
alerts), not from a fixture.

Usage::

    # dry run — print the plan, touch nothing
    python tools/b13_capture.py --context k3d-k0rdent --dry-run

    # capture h001 + h002 as real_001 / real_002, leave the cluster clean
    python tools/b13_capture.py --context k3d-k0rdent --scenarios h001,h002

Phase 1 covers the four scenarios that already have live-deployable manifests
(h001–h004). h005–h010 need reproducer manifests authored first (follow-up).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))  # import the app packages (services, workflow, …)

# Prometheus lives here in the demo cluster (kube-prometheus-stack). We reach it
# through the apiserver service-proxy rather than `kubectl port-forward`: on this
# k3d cluster the kubelet streaming path 502s, but the service proxy (cluster
# network) works. `kubectl proxy` exposes the apiserver locally, and the
# PrometheusCollector hits {proxy}/api/v1/namespaces/.../proxy/api/v1/alerts.
PROM_NS = "observability"
PROM_SVC_NAME = "kube-prometheus-stack-prometheus"
PROM_PORT = 9090
PROXY_PORT = 8001


def _prom_proxy_url(local: str) -> str:
    return (f"{local}/api/v1/namespaces/{PROM_NS}/services/"
            f"{PROM_SVC_NAME}:{PROM_PORT}/proxy")

# ── scenario registry — Phase 1: the manifests that actually deploy live ────────
# `reasons` are the container waiting/terminated reasons that prove the real
# failure landed; the driver polls until one of them appears.
SCENARIOS: dict[str, dict] = {
    "h001_crashloopbackoff": {
        "manifest": "demo/manifests/01-crashloop.yaml",
        "namespace": "kubeverdict-demo",
        "query": "payment-service pods are crashlooping in kubeverdict-demo",
        "reasons": ["CrashLoopBackOff", "Error", "BackOff"],
    },
    "h002_imagepullbackoff": {
        "manifest": "demo/manifests/04-imagepull.yaml",
        "namespace": "kubeverdict-demo",
        "query": "ml-inference cannot pull its container image in kubeverdict-demo",
        "reasons": ["ImagePullBackOff", "ErrImagePull"],
    },
    "h003_oomkilled": {
        "manifest": "demo/manifests/02-oom.yaml",
        "namespace": "kubeverdict-demo",
        "query": "analytics-worker keeps getting OOMKilled in kubeverdict-demo",
        "reasons": ["OOMKilled", "CrashLoopBackOff"],
    },
    "h004_missing_configmap": {
        "manifest": "demo/manifests/03-missing-config.yaml",
        "namespace": "kubeverdict-demo",
        "query": "notification-service is missing its configmap in kubeverdict-demo",
        "reasons": ["CreateContainerConfigError", "RunContainerError", "CrashLoopBackOff"],
    },
}

EVIDENCE = ROOT / "docs/evidence/prometheus-live.md"
GOLDEN_DIR = ROOT / "tests/golden"


def _sh(*args: str, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, check=check, text=True,
        capture_output=capture, cwd=str(ROOT),
    )


def _kubectl(context: str, *args: str, capture: bool = False, check: bool = True):
    return _sh("kubectl", "--context", context, *args, capture=capture, check=check)


# ── live cluster steps ──────────────────────────────────────────────────────────

def _retry(fn, attempts: int = 3, delay: float = 3.0):
    """Run `fn`, retrying on transient kubectl/apiserver hiccups."""
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except subprocess.CalledProcessError as exc:
            last = exc
            if i < attempts - 1:
                time.sleep(delay)
    raise last  # type: ignore[misc]


def ensure_namespace(context: str, namespace: str) -> None:
    """Create the scenario namespace if it does not already exist (idempotent)."""
    cp = _kubectl(context, "create", "namespace", namespace, capture=True, check=False)
    if cp.returncode != 0 and "AlreadyExists" not in (cp.stderr or ""):
        raise subprocess.CalledProcessError(cp.returncode, cp.args, cp.stdout, cp.stderr)


def apply(context: str, scenario: dict) -> None:
    _retry(lambda: ensure_namespace(context, scenario["namespace"]))
    _retry(lambda: _kubectl(context, "apply", "-f", scenario["manifest"]))


def teardown(context: str, scenario: dict) -> None:
    # Best-effort: retry through transient apiserver hiccups, but never raise —
    # cleanup must not abort the batch or mask a successful capture. A persistent
    # failure is surfaced as a warning so leftover pods can be cleaned by hand.
    try:
        _retry(lambda: _kubectl(context, "delete", "-f", scenario["manifest"],
                                "--ignore-not-found", "--wait=false"))
    except subprocess.CalledProcessError as exc:
        print(f"  !! teardown of {scenario['manifest']} failed "
              f"(leftover pods in ns/{scenario['namespace']}): {exc}", file=sys.stderr)


def _failure_reasons(context: str, namespace: str) -> set[str]:
    """All container waiting/terminated reasons currently visible in `namespace`."""
    cp = _kubectl(context, "get", "pods", "-n", namespace, "-o", "json",
                  capture=True, check=False)
    if cp.returncode != 0 or not cp.stdout.strip():
        return set()
    found: set[str] = set()
    for pod in json.loads(cp.stdout).get("items", []):
        for cs in pod.get("status", {}).get("containerStatuses", []):
            for phase in ("waiting", "terminated"):
                reason = cs.get("state", {}).get(phase, {}).get("reason")
                if reason:
                    found.add(reason)
            last = cs.get("lastState", {}).get("terminated", {}).get("reason")
            if last:
                found.add(last)
    return found


def wait_for_failure(context: str, scenario: dict, timeout: int) -> str:
    """Block until one of the scenario's expected failure reasons appears."""
    want = set(scenario["reasons"])
    deadline = time.time() + timeout
    while time.time() < deadline:
        hit = _failure_reasons(context, scenario["namespace"]) & want
        if hit:
            return sorted(hit)[0]
        time.sleep(5)
    raise TimeoutError(
        f"none of {sorted(want)} appeared in ns/{scenario['namespace']} "
        f"within {timeout}s"
    )


class _PromProxy:
    """Expose the cluster Prometheus locally via `kubectl proxy` + service-proxy.

    Returns the base URL the PrometheusCollector should use; appending
    ``/api/v1/alerts`` yields the working service-proxy endpoint.
    """

    def __init__(self, context: str):
        self.context = context
        self.proc: subprocess.Popen | None = None

    def __enter__(self) -> str:
        self.proc = subprocess.Popen(
            ["kubectl", "--context", self.context, "proxy", f"--port={PROXY_PORT}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(ROOT),
        )
        base = _prom_proxy_url(f"http://localhost:{PROXY_PORT}")
        # Poll until Prometheus answers through the proxy — a blind sleep raced
        # the pipeline's prometheus node and gave "connection refused".
        deadline = time.time() + 30
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError("kubectl proxy exited early")
            try:
                with urllib.request.urlopen(f"{base}/-/ready", timeout=2) as r:
                    if r.status == 200:
                        return base
            except (urllib.error.URLError, ConnectionError, OSError):
                time.sleep(1)
        raise TimeoutError(f"prometheus not ready via proxy at {base} within 30s")

    def __exit__(self, *exc) -> None:
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def investigate(context: str, scenario: dict) -> dict:
    """Run the canonical pipeline against the live cluster; return final state."""
    from services.investigation_service import run_investigation, verdict_summary

    state = asyncio.run(run_investigation(
        query=scenario["query"],
        namespaces=[scenario["namespace"]],
        kube_context=context,
    ))
    summary = verdict_summary(state)
    br = state.get("blast_radius") or {}
    summary["_triple"] = {
        "verdict": state.get("verdict"),
        "risk": br.get("risk"),
        "rollback_available": br.get("rollback_available"),
    }
    summary["_ingestion_stats"] = state.get("ingestion_stats") or {}
    return summary


# ── artifact writers ────────────────────────────────────────────────────────────

def write_golden(index: int, scenario_id: str, summary: dict) -> Path:
    path = GOLDEN_DIR / f"real_{index:03d}.json"
    path.write_text(json.dumps(summary["_triple"], indent=2, sort_keys=True) + "\n")
    return path


def append_evidence(scenario_id: str, context: str, summary: dict, reason: str) -> None:
    prom = summary["_ingestion_stats"].get("prometheus", {})
    alerts = prom.get("alerts", 0)
    when = datetime.now(timezone.utc).isoformat()
    block = [
        f"\n## {scenario_id} — captured {when}\n",
        f"- **Cluster context:** `{context}` (live k3s)",
        f"- **Observed failure reason:** `{reason}` (real container state, not a fixture)",
        f"- **Live Prometheus alerts correlated:** {alerts} "
        f"(`prometheus` node fallback={prom.get('fallback', 'n/a')})",
        f"- **Verdict:** `{summary['_triple']['verdict']}` · "
        f"risk `{summary['_triple']['risk']}` · "
        f"rollback_available `{summary['_triple']['rollback_available']}`",
        f"- **Root cause (LLM):** {summary.get('root_cause', '').strip() or '—'}",
        "",
    ]
    header = ""
    if not EVIDENCE.exists():
        header = (
            "# Prometheus & live-incident evidence (B13)\n\n"
            "Real runs captured against a live k3s cluster by "
            "`tools/b13_capture.py` — proof the collectors work against a real "
            "endpoint, not only fixtures. Each block below is one scenario "
            "deployed, observed failing, and investigated end-to-end live.\n\n"
            "**How to read this**\n\n"
            "- *Snapshots, not CI baselines.* The verdict comes from a live LLM "
            "analysis plus Monte-Carlo stability sims, and the analysis prompt "
            "embeds a timestamp — so the same scenario can yield a different "
            "verdict on a later run. The `real_00N.json` files are frozen "
            "*captured* verdicts (provenance evidence), **not** deterministic "
            "fixtures, and are deliberately not wired into the B11 regression "
            "guard (which stays on the synthetic h001–h010 baseline).\n"
            "- *0 alerts correlated is expected here.* `fallback=False` proves "
            "the collector reached the real Prometheus; the cluster's firing "
            "alerts are cluster-scoped (e.g. KubeProxyDown) and a fresh "
            "<2-minute incident has not tripped any `for:`-gated rule yet, so "
            "none map onto the demo-namespace entities. The proof is the live "
            "connection, not the count.\n"
        )
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    with EVIDENCE.open("a") as fh:
        if header:
            fh.write(header)
        fh.write("\n".join(block))


# ── orchestration ───────────────────────────────────────────────────────────────

def capture(context: str, scenario_id: str, index: int, *,
            cleanup: bool, timeout: int) -> dict:
    scenario = SCENARIOS[scenario_id]
    print(f"\n=== {scenario_id} → real_{index:03d} ===")
    print(f"  apply {scenario['manifest']} → ns/{scenario['namespace']}")
    apply(context, scenario)
    try:
        print(f"  waiting for real failure {scenario['reasons']} …")
        reason = wait_for_failure(context, scenario, timeout)
        print(f"  observed: {reason}")
        with _PromProxy(context) as prom_url:
            os.environ["PROMETHEUS_ENABLED"] = "true"
            os.environ["PROMETHEUS_URL"] = prom_url
            # Force a fresh LLM analysis per capture (no example-cache reuse), and
            # give the local model room — mistral is slow on a full RCA prompt.
            os.environ["EXAMPLE_LOOKUP_DISABLED"] = "true"
            os.environ.setdefault("OLLAMA_TIMEOUT", "300")
            print(f"  investigating live (prometheus={prom_url}) …")
            summary = investigate(context, scenario)
        golden = write_golden(index, scenario_id, summary)
        append_evidence(scenario_id, context, summary, reason)
        print(f"  → {golden.relative_to(ROOT)}  {summary['_triple']}")
        return summary
    finally:
        if cleanup:
            print(f"  teardown {scenario['manifest']}")
            teardown(context, scenario)


def main() -> int:
    ap = argparse.ArgumentParser(description="Capture B13 live incident artifacts")
    ap.add_argument("--context", required=True, help="kube context (e.g. k3d-k0rdent)")
    ap.add_argument("--scenarios", default="h001,h002",
                    help="comma-separated scenario prefixes (default: h001,h002)")
    ap.add_argument("--start-index", type=int, default=1,
                    help="first real_NNN index (default: 1)")
    ap.add_argument("--timeout", type=int, default=180,
                    help="seconds to wait for the failure state (default: 180)")
    ap.add_argument("--no-cleanup", action="store_true",
                    help="leave scenarios running after capture")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan and exit — touch nothing")
    args = ap.parse_args()

    # Resolve scenario prefixes (h001) to full ids (h001_crashloopbackoff).
    wanted = [s.strip() for s in args.scenarios.split(",") if s.strip()]
    resolved: list[str] = []
    for w in wanted:
        match = [sid for sid in SCENARIOS if sid.startswith(w)]
        if not match:
            print(f"unknown scenario '{w}'. known: {', '.join(SCENARIOS)}", file=sys.stderr)
            return 2
        resolved.append(match[0])

    if args.dry_run:
        print(f"context: {args.context}  (DRY RUN — nothing applied)")
        for i, sid in enumerate(resolved, start=args.start_index):
            sc = SCENARIOS[sid]
            print(f"  real_{i:03d} ← {sid}: apply {sc['manifest']} "
                  f"→ ns/{sc['namespace']}, wait {sc['reasons']}")
        return 0

    failures: list[str] = []
    for i, sid in enumerate(resolved, start=args.start_index):
        try:
            capture(args.context, sid, i,
                    cleanup=not args.no_cleanup, timeout=args.timeout)
        except Exception as exc:  # one bad scenario must not abort the batch
            print(f"  !! {sid} failed: {exc}", file=sys.stderr)
            failures.append(sid)
            if not args.no_cleanup:
                teardown(args.context, SCENARIOS[sid])
    print("\ndone. regenerate golden baseline diff with the B11 guard if needed.")
    if failures:
        print(f"failed scenarios: {', '.join(failures)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
