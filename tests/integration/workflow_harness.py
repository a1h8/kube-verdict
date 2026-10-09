"""
Run an integration case through the real LangGraph workflow, offline.

Shared by tests/integration/test_workflow_e2e_h013_h015.py and
tools/freeze_journey_fixtures.py (spec: docs/test-cases.md → *Broadened offline
coverage* §1).

The case's Kubernetes snapshot is pre-built (case_loader, ``wire_signals=False``);
its Prometheus / Loki / OTel fixtures are then fetched by the workflow's own
collector nodes. Only the network edge of each collector is swapped for the
fixture — PrometheusCollector._fetch_alerts, LokiSource._query and the OTel
backend — so ``ingestion_stats`` records what a live run would.

Everything with a side effect or a random draw is pinned: example lookup off,
dry-run kubectl not executed, Monte Carlo seeded, PatchTST range queries return
nothing (synthetic mode). The LLM is scripted (ScriptedLLM).
"""
from __future__ import annotations

import os
from contextlib import ExitStack
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any
from unittest.mock import patch

import config as cfg
from ingestion.loki_source import LokiSource
from ingestion.prometheus_collector import PrometheusCollector
from reasoning.monte_carlo import run_monte_carlo
from signals.prometheus_source import PrometheusMetricSource
from tests.integration.cases.case_loader import (
    _FixtureOtelBackend,
    build_graph as build_case_graph,
    fixture_loki_query,
    load_case,
)
from workflow.graph import build_graph

CASES_ROOT = Path(__file__).parent / "cases"

_HYPOTHESIZE_PROMPT_PREFIX = "Kubernetes SRE expert."

_LOW_RESPONSE = """\
### 1. Summary
The evidence for this hypothesis is inconclusive.

### 2. Affected resources
- (none confirmed)

### 3. Root cause
Not established — the context does not support this hypothesis.

### 4. Causal chain
1. No corroborating signal found.
2. Hypothesis not confirmed.

### 5. Remediation
kubectl get events -n {namespace}

### 6. Confidence
LOW — the context does not support this hypothesis.
"""


@dataclass(frozen=True)
class CaseScript:
    """What the scripted LLM answers for one case, and what must reach it."""
    case: str
    query: str
    hypotheses: tuple[str, ...]
    final_response: str
    prompt_evidence: tuple[str, ...]   # strings the analyze prompt must contain
    collector: str                     # ingestion_stats key of the case's signal
    count_key: str                     # its count field

    @property
    def case_dir(self) -> Path:
        return CASES_ROOT / self.case


CASE_SCRIPTS: dict[str, CaseScript] = {
    "h013": CaseScript(
        case="h013_slo_error_budget_burn",
        query="why is checkout-api slow",
        hypotheses=(
            "checkout-api pods are CPU-throttled under load",
            "Dependency latency to payments-gateway burns the checkout-api SLO error budget",
            "A recent checkout-api rollout introduced a slow code path",
        ),
        final_response="""\
### 1. Summary
checkout-api is burning its SLO error budget at 14.4x because calls to payments-gateway got slow.

### 2. Affected resources
- Deployment/production/checkout-api — 3/3 Ready, SLO burn-rate alerts firing

### 3. Root cause
The p99 latency (1.84s vs a 400ms SLO) comes from calls to payments-gateway; CPU, memory and restarts are flat, so the network path to the dependency is the cause, not the pod.

### 4. Causal chain
1. Calls from checkout-api to payments-gateway slow down.
2. p99 latency breaches the 400ms SLO.
3. Multi-window burn-rate alerts fire (14.4x fast, 6x slow).

### 5. Remediation
kubectl rollout restart deployment/checkout-api -n production

### 6. Confidence
HIGH — the burn-rate and latency alerts agree and isolate payments-gateway.
""",
        prompt_evidence=("14.4x", "payments-gateway"),
        collector="prometheus",
        count_key="alerts",
    ),
    "h014": CaseScript(
        case="h014_cert_expiry",
        query="why is billing-gateway not ready",
        hypotheses=(
            "Expired mTLS certificate to payments-backend fails the readiness probe",
            "billing-gateway readiness probe timeout is too short",
            "payments-backend is down",
        ),
        final_response="""\
### 1. Summary
billing-gateway is not Ready because its mTLS certificate to payments-backend expired and was never renewed.

### 2. Affected resources
- Deployment/production/billing-gateway — 0/2 Ready

### 3. Root cause
The pods log "x509: certificate has expired"; the cert-manager issuer letsencrypt-prod cannot reach its ACME endpoint, so the certificate was not renewed.

### 4. Causal chain
1. ACME issuer cannot register its account.
2. The mTLS certificate expires without renewal.
3. TLS handshake to payments-backend fails, /ready returns 503.

### 5. Remediation
helm upgrade billing-gateway ./chart -n production --set certManager.issuer=letsencrypt-prod-http01

### 6. Confidence
HIGH — the x509 expiry log line names the cause directly.
""",
        prompt_evidence=("x509", "payments-backend"),
        collector="loki",
        count_key="logs",
    ),
    "h015": CaseScript(
        case="h015_etcd_compaction",
        query="why is inventory-api not ready",
        hypotheses=(
            "etcd compaction latency makes Range calls exceed the readiness timeout",
            "inventory-api readiness probe misconfigured",
            "inventory-api lacks memory",
        ),
        final_response="""\
### 1. Summary
inventory-api stays NotReady because etcd Range calls take ~5s after a compaction and hit DeadlineExceeded.

### 2. Affected resources
- Pod/production/inventory-api-7f9c8d6b5-x2qwe — Running, not Ready

### 3. Root cause
After an etcd compaction cycle, KV Range calls never recover (4.5–5.1s, "required revision has been compacted"), longer than the 2s readiness timeout.

### 4. Causal chain
1. etcd compaction starts.
2. Range calls slow to ~5s and return DeadlineExceeded.
3. The readiness probe (timeoutSeconds=2) keeps failing.

### 5. Remediation
helm upgrade inventory-api ./chart -n production --set readinessProbe.timeoutSeconds=6

### 6. Confidence
HIGH — the error traces show the etcd Range latency directly.
""",
        prompt_evidence=("etcd", "TRACES"),
        collector="otel",
        count_key="traces",
    ),
}


class ScriptedLLM:
    """Deterministic LLM: hypotheses on demand, then LOW, LOW, HIGH for analyze.

    Two LOW answers on the first hypothesis make log_confidence_decision see a
    declining path (LOW×2) and switch to the next one, so archive_path really
    runs and reasoning_history is non-empty; every later analyze answers HIGH.
    """

    model = "scripted"

    def __init__(self, script: CaseScript, namespace: str) -> None:
        self._script = script
        self._low = _LOW_RESPONSE.format(namespace=namespace)
        self.analyze_prompts: list[str] = []

    def is_available(self) -> bool:
        return True

    def model_is_pulled(self) -> bool:
        return True

    def generate(self, prompt: str, system: str | None = None, **_: Any) -> str:
        if prompt.startswith(_HYPOTHESIZE_PROMPT_PREFIX):
            return "\n".join(f"{i}. {h}" for i, h in enumerate(self._script.hypotheses, 1))
        self.analyze_prompts.append(prompt)
        return self._low if len(self.analyze_prompts) <= 2 else self._script.final_response


class StubStore:
    """No FAISS / embedding: retrieval returns nothing, the graph carries the evidence."""

    last_retrieval_stats: dict = {}

    def search(self, query, top_k=10):
        return []

    def hybrid_search(self, query, top_k=10):
        return []


@dataclass
class CaseRun:
    state: dict[str, Any]
    status: str                      # "AWAITING_REVIEW" | "COMPLETED"
    review_payload: dict | None
    llm: ScriptedLLM
    expect: dict = field(default_factory=dict)


def run_case(key: str) -> CaseRun:
    """Run one scripted case (``h013`` / ``h014`` / ``h015``) through the workflow."""
    script = CASE_SCRIPTS[key]
    case = load_case(script.case_dir)
    namespace = case["expect"].get("namespace", "default")
    graph = build_case_graph(case, wire_signals=False)
    llm = ScriptedLLM(script, namespace)

    alerts = case.get("prometheus_alerts", [])
    loki_query = fixture_loki_query(case.get("loki_streams", []))
    traces = case.get("otel_traces", [])

    with ExitStack() as stack:
        for name, value in {
            "PROMETHEUS_ENABLED": True, "LOKI_ENABLED": True, "OTEL_ENABLED": True,
            "METRICS_SERVER_ENABLED": False, "GITOPS_ENABLED": False,
        }.items():
            stack.enter_context(patch.object(cfg, name, value))
        stack.enter_context(patch.dict(os.environ, {"EXAMPLE_LOOKUP_DISABLED": "1"}))
        stack.enter_context(patch.object(
            PrometheusCollector, "_fetch_alerts", lambda self: alerts))
        stack.enter_context(patch.object(
            LokiSource, "_query", lambda self, logql, start, end: loki_query(logql, start, end)))
        stack.enter_context(patch(
            "ingestion.otel_backend.build_backend",
            lambda *a, **k: _FixtureOtelBackend(traces)))
        stack.enter_context(patch.object(
            PrometheusMetricSource, "_range_query", lambda self, *a, **k: None))
        stack.enter_context(patch(
            "workflow.nodes._exec_dry_run",
            lambda cmd: (f"{cmd} --dry-run=server", "[fixture-replay: dry-run not executed]", 0)))
        stack.enter_context(patch(
            "workflow.nodes.run_monte_carlo", partial(run_monte_carlo, seed=0)))

        workflow = build_graph()
        config = {"configurable": {
            "thread_id": f"fixture-replay-{key}",
            "graph": graph, "store": StubStore(), "llm": llm,
        }}
        workflow.invoke({"query": script.query, "namespaces": [namespace]}, config)
        snapshot = workflow.get_state(config)

    review_payload = None
    for task in snapshot.tasks or []:
        if getattr(task, "interrupts", None):
            review_payload = task.interrupts[0].value
            break
    return CaseRun(
        state=dict(snapshot.values),
        status="AWAITING_REVIEW" if snapshot.next else "COMPLETED",
        review_payload=review_payload,
        llm=llm,
        expect=case["expect"],
    )
