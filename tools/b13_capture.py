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

    # h014/h015 don't crash — they go Running-but-NotReady for real, and each
    # needs a different real evidence backend (Loki / Tempo) alongside
    # Prometheus. Run these as SEPARATE process invocations, not batched in
    # one --scenarios list: `config.py`'s LOKI_ENABLED / OTEL_ENABLED are
    # read once at first import inside this same process, so a later
    # scenario's os.environ changes in the same run would be silently
    # ignored (config already cached from the earlier scenario's import).
    python tools/b13_capture.py --context rancher-desktop --scenarios h014 --start-index 3
    python tools/b13_capture.py --context rancher-desktop --scenarios h015 --start-index 4

Phase 1 covers the four scenarios that already have live-deployable manifests
(h001–h004). h005–h013 need reproducer manifests authored first (follow-up).
h014/h015 (`failure_mode: not_ready`) were added against the live
`kubeverdict-obs` Loki/Tempo deployed per docs/local-observability-stack.md.
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
import urllib.parse
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
# NOTE: these three point at the live `kubeverdict-obs` stack on the current
# `rancher-desktop` cluster (docs/local-observability-stack.md) — h001/h002
# in docs/evidence/prometheus-live.md were captured earlier against a
# different cluster/context (`k3d-k0rdent`, `kube-prometheus-stack` in an
# `observability` namespace); update these if you point --context elsewhere.
PROM_NS = "kubeverdict-obs"
PROM_SVC_NAME = "prometheus-server"
PROM_PORT = 80
PROXY_PORT = 8001

# Loki + Tempo live here too, reached through the same apiserver
# service-proxy mechanism as Prometheus above.
OBS_NS = "kubeverdict-obs"
LOKI_SVC_NAME = "loki"
LOKI_PORT = 3100
TEMPO_SVC_NAME = "tempo"
TEMPO_PORT = 3200

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
    # h014/h015 don't crash — they stay Running with a failing readiness
    # probe, so `failure_mode: not_ready` is checked instead of `reasons`
    # (see wait_for_failure). Each also needs a real evidence backend beyond
    # Prometheus: h014's cause is log-only (Loki), h015's is trace-only
    # (Tempo via the OTel collector) — see docs/local-observability-stack.md.
    "h014_cert_expiry": {
        "manifest": "demo/manifests/09-cert-expiry.yaml",
        "namespace": "kubeverdict-demo",
        "query": "billing-gateway pods are Running but not Ready in kubeverdict-demo",
        "failure_mode": "not_ready",
        "needs_loki": True,
        # same list as tests/integration/cases/h014_cert_expiry/expect.json —
        # we authored the injected fault, so this is ground truth, not a guess.
        "root_cause_contains": ["certificate", "expired", "x509", "tls", "renew", "issuer"],
    },
    "h015_etcd_compaction": {
        "manifest": "demo/manifests/10-etcd-compaction.yaml",
        "namespace": "kubeverdict-demo",
        "query": "inventory-api pods are Running but not Ready in kubeverdict-demo",
        "failure_mode": "not_ready",
        "needs_otel": True,
        "otel_service": "inventory-api",
        # same list as tests/integration/cases/h015_etcd_compaction/expect.json
        "root_cause_contains": ["etcd", "compaction", "latency", "deadline", "timeout", "readiness"],
    },
}

EVIDENCE = ROOT / "docs/evidence/prometheus-live.md"
GOLDEN_DIR = ROOT / "tests/golden"
VERACITY_DIR = ROOT / "tests/veracity"


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


def _pod_not_ready(context: str, namespace: str) -> bool:
    """True if some container in `namespace` is really Running but ready=false —
    the h014/h015 shape (readiness probe failing, no crash, no waiting/terminated
    reason `_failure_reasons` would ever catch)."""
    cp = _kubectl(context, "get", "pods", "-n", namespace, "-o", "json",
                  capture=True, check=False)
    if cp.returncode != 0 or not cp.stdout.strip():
        return False
    for pod in json.loads(cp.stdout).get("items", []):
        for cs in pod.get("status", {}).get("containerStatuses", []):
            if cs.get("state", {}).get("running") and cs.get("ready") is False:
                return True
    return False


def wait_for_failure(context: str, scenario: dict, timeout: int) -> str:
    """Block until the scenario's real failure state appears."""
    deadline = time.time() + timeout
    if scenario.get("failure_mode") == "not_ready":
        while time.time() < deadline:
            if _pod_not_ready(context, scenario["namespace"]):
                return "Running-but-NotReady (readiness probe failing)"
            time.sleep(5)
        raise TimeoutError(
            f"no pod went Running-but-NotReady in ns/{scenario['namespace']} "
            f"within {timeout}s"
        )

    want = set(scenario["reasons"])
    while time.time() < deadline:
        hit = _failure_reasons(context, scenario["namespace"]) & want
        if hit:
            return sorted(hit)[0]
        time.sleep(5)
    raise TimeoutError(
        f"none of {sorted(want)} appeared in ns/{scenario['namespace']} "
        f"within {timeout}s"
    )


class _ApiServerProxy:
    """Expose the apiserver locally via a single `kubectl proxy`, then build
    per-service proxy URLs off it (Prometheus, Loki, Tempo, ...) instead of
    spawning one `kubectl proxy` process per backend.
    """

    def __init__(self, context: str):
        self.context = context
        self.proc: subprocess.Popen | None = None
        self.base: str = ""

    def __enter__(self) -> "_ApiServerProxy":
        self.proc = subprocess.Popen(
            ["kubectl", "--context", self.context, "proxy", f"--port={PROXY_PORT}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(ROOT),
        )
        self.base = f"http://localhost:{PROXY_PORT}"
        deadline = time.time() + 30
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError("kubectl proxy exited early")
            try:
                with urllib.request.urlopen(f"{self.base}/healthz", timeout=2) as r:
                    if r.status == 200:
                        return self
            except (urllib.error.URLError, ConnectionError, OSError):
                time.sleep(1)
        raise TimeoutError(f"apiserver proxy did not come up within 30s")

    def url_for(self, namespace: str, service: str, port: int) -> str:
        return (f"{self.base}/api/v1/namespaces/{namespace}/services/"
                f"{service}:{port}/proxy")

    def wait_ready(self, namespace: str, service: str, port: int,
                   path: str = "/-/ready", timeout: int = 30) -> str:
        """Return the service's proxy URL once it answers `path` with 200 — a
        blind sleep raced the pipeline's collector node and gave connection
        refused on a cold backend."""
        url = self.url_for(namespace, service, port)
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"{url}{path}", timeout=2) as r:
                    if r.status == 200:
                        return url
            except (urllib.error.URLError, ConnectionError, OSError):
                time.sleep(1)
        raise TimeoutError(f"{service} not ready via proxy at {url} within {timeout}s")

    def __exit__(self, *exc) -> None:
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def wait_tempo_searchable(tempo_url: str, service: str, since_ts: int, timeout: int = 120) -> bool:
    """Poll Tempo's tag-search endpoint — the same one `TempoBackend.search_error_traces()`
    hits — until it returns at least one trace for `service`. A trace is
    queryable by trace-ID right after ingest, but Tempo's WAL→block flush needs
    time to elapse before the *tag search index* picks it up (see
    docs/evidence/tempo-loki-live.md point 2's caveat); without this wait a
    capture started right after `kubectl apply` reliably finds 0 traces even
    though the OTel pipeline is working.

    `since_ts` MUST be this capture's own `kubectl apply` time, not `now - N`:
    a rolling window re-matches a still-retained trace left over from a prior
    capture of the same scenario (same service name, run minutes earlier) and
    reports success before the *new* trace actually lands — the search finding
    something is not proof it found the right thing."""
    deadline = time.time() + timeout
    query = urllib.parse.urlencode({
        "tags": f"service.name={service}",
        "start": since_ts,
        "end": int(time.time()) + 60,
        "limit": 5,
    })
    url = f"{tempo_url}/api/search?{query}"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                if json.loads(r.read()).get("traces"):
                    return True
        except (urllib.error.URLError, ConnectionError, OSError, ValueError):
            pass
        time.sleep(5)
    return False


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


def grade_veracity(root_cause: str, keywords: list[str]) -> tuple[str, list[str]]:
    """Same rule as `test_root_cause_keywords` in
    tests/integration/use_cases/test_native_helm_dialogue.py: at least half the
    ground-truth keywords (min 1) must appear, case-insensitive, for a PASS."""
    text = root_cause.lower()
    matched = [kw for kw in keywords if kw.lower() in text]
    threshold = max(1, len(keywords) // 2)
    return ("PASS" if len(matched) >= threshold else "FAIL"), matched


def write_veracity(index: int, scenario_id: str, summary: dict, keywords: list[str]) -> Path:
    """Ground-truth root-cause check, paired 1:1 with tests/golden/real_00N.json
    by index. Deliberately kept out of data/examples/ (the FAISS store of
    *verified-correct* resolutions `workflow.nodes.save_example` feeds back as
    RAG context) — a FAIL here must never be retrieved as a resolved-incident
    example, or the LLM would be pointed at its own past mistake as ground truth."""
    root_cause = (summary.get("root_cause") or "").strip()
    veracity, matched = grade_veracity(root_cause, keywords)
    path = VERACITY_DIR / f"real_{index:03d}.json"
    VERACITY_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "scenario_id": scenario_id,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "root_cause_contains": keywords,
        "matched_keywords": matched,
        "actual_root_cause": root_cause,
        "veracity": veracity,
    }, indent=2, sort_keys=True) + "\n")
    return path


def append_evidence(scenario_id: str, context: str, summary: dict, reason: str) -> None:
    prom = summary["_ingestion_stats"].get("prometheus", {})
    otel = summary["_ingestion_stats"].get("otel", {})
    alerts = prom.get("alerts", 0)
    when = datetime.now(timezone.utc).isoformat()
    block = [
        f"\n## {scenario_id} — captured {when}\n",
        f"- **Cluster context:** `{context}` (live k3s)",
        f"- **Observed failure reason:** `{reason}` (real container state, not a fixture)",
        f"- **Live Prometheus alerts correlated:** {alerts} "
        f"(`prometheus` node fallback={prom.get('fallback', 'n/a')})",
    ]
    if "logs" in otel or "logs_fallback" in otel:
        block.append(
            f"- **Live Loki logs correlated:** {otel.get('logs', 0)} "
            f"(`otel` node logs_fallback={otel.get('logs_fallback', 'n/a')})"
        )
    if "traces" in otel or "traces_fallback" in otel:
        block.append(
            f"- **Live OTel traces correlated:** {otel.get('traces', 0)} "
            f"(`otel` node traces_fallback={otel.get('traces_fallback', 'n/a')})"
        )
    block += [
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
    apply_time = int(time.time())
    apply(context, scenario)
    try:
        wait_for = "Running-but-NotReady" if scenario.get("failure_mode") == "not_ready" else scenario["reasons"]
        print(f"  waiting for real failure {wait_for} …")
        reason = wait_for_failure(context, scenario, timeout)
        print(f"  observed: {reason}")
        with _ApiServerProxy(context) as proxy:
            prom_url = proxy.wait_ready(PROM_NS, PROM_SVC_NAME, PROM_PORT, "/-/ready")
            os.environ["PROMETHEUS_ENABLED"] = "true"
            os.environ["PROMETHEUS_URL"] = prom_url
            backends = [f"prometheus={prom_url}"]

            if scenario.get("needs_loki"):
                loki_url = proxy.wait_ready(OBS_NS, LOKI_SVC_NAME, LOKI_PORT, "/ready")
                os.environ["LOKI_ENABLED"] = "true"
                os.environ["LOKI_URL"] = loki_url
                backends.append(f"loki={loki_url}")

            if scenario.get("needs_otel"):
                tempo_url = proxy.wait_ready(OBS_NS, TEMPO_SVC_NAME, TEMPO_PORT, "/ready")
                os.environ["OTEL_ENABLED"] = "true"
                os.environ["OTEL_BACKEND_URL"] = tempo_url
                backends.append(f"otel(tempo)={tempo_url}")
                print(f"  waiting for Tempo's tag-search index to catch up "
                      f"(WAL→block flush) for service={scenario['otel_service']} …")
                if not wait_tempo_searchable(tempo_url, scenario["otel_service"], apply_time):
                    print(f"  !! Tempo search still empty after the wait — "
                          f"proceeding anyway, trace correlation may come back 0",
                          file=sys.stderr)

            # Force a fresh LLM analysis per capture (no example-cache reuse), and
            # give the local model room — mistral is slow on a full RCA prompt.
            os.environ["EXAMPLE_LOOKUP_DISABLED"] = "true"
            # CPU-only local Ollama (no GPU) took ~3min just for the hypothesize
            # LLM fill-in during h014 dry-run; the larger analyze-step prompt
            # needs more headroom than 300s gave it.
            os.environ.setdefault("OLLAMA_TIMEOUT", "900")
            print(f"  investigating live ({', '.join(backends)}) …")
            summary = investigate(context, scenario)
        golden = write_golden(index, scenario_id, summary)
        append_evidence(scenario_id, context, summary, reason)
        print(f"  → {golden.relative_to(ROOT)}  {summary['_triple']}")
        if scenario.get("root_cause_contains"):
            veracity_path = write_veracity(index, scenario_id, summary, scenario["root_cause_contains"])
            veracity_data = json.loads(veracity_path.read_text())
            print(f"  → {veracity_path.relative_to(ROOT)}  veracity={veracity_data['veracity']} "
                  f"matched={veracity_data['matched_keywords']}")
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
            wait_for = "Running-but-NotReady" if sc.get("failure_mode") == "not_ready" else sc["reasons"]
            print(f"  real_{i:03d} ← {sid}: apply {sc['manifest']} "
                  f"→ ns/{sc['namespace']}, wait {wait_for}")
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
