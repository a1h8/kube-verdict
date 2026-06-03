#!/usr/bin/env python3
"""
Generate dashboard/src/roadmap.json from deterministic code checks.

Status rules:
  DONE        — all checks pass
  IN_PROGRESS — at least one check passes, at least one fails
  TODO        — no checks pass

Usage:
    python tools/roadmap.py [--output dashboard/src/roadmap.json]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent


def _exists(*paths: str) -> bool:
    return all((ROOT / p).exists() for p in paths)


def _git_tag(pattern: str) -> bool:
    """True if at least one git tag matches `pattern` (anchored regex).

    Used to verify outcomes a workflow file alone cannot prove — e.g. that a
    version was actually tagged (and thus the release image actually built),
    not just that the publishing workflow exists. Requires tags to be present
    in the checkout (CI: actions/checkout with fetch-tags: true).
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(ROOT), "tag"],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return any(re.match(pattern, line.strip()) for line in out.splitlines())


def _grep(pattern: str, *targets: str) -> bool:
    for t in targets:
        p = ROOT / t
        files = [p] if p.is_file() else list(p.rglob("*.py")) if p.is_dir() else []
        for f in files:
            if re.search(pattern, f.read_text(errors="ignore")):
                return True
    return False


# ── bloc definitions ───────────────────────────────────────────────────────────

def _blocs() -> list[dict]:
    return [
        {
            "id": "B1",
            "title": "Evidence collection",
            "description": "K8s events, Prometheus, Loki, Helm drift, OTel traces, anchor engine",
            "checks": [
                {
                    "label": "K8s + Prometheus + Loki collectors",
                    "done": _exists(
                        "ingestion/k8s_collector.py",
                        "ingestion/prometheus_collector.py",
                        "ingestion/loki_source.py",
                    ),
                },
                {
                    "label": "Helm drift + anchor engine",
                    "done": _exists("ingestion/helm_drift.py", "ingestion/anchor_engine.py"),
                },
                {
                    "label": "GitOps collector (Flux / ArgoCD)",
                    "done": _exists("ingestion/gitops_collector.py"),
                },
                {
                    "label": "OTel traces (Jaeger / Tempo)",
                    "done": _exists("ingestion/otel_collector.py", "ingestion/otel_backend.py"),
                },
                {
                    "label": "OTLP push receiver (port 4318)",
                    "done": _exists("ingestion/otlp_receiver.py")
                    and _grep(r"OtlpReceiver|otlp_receiver", "ingestion/otel_backend.py"),
                },
            ],
        },
        {
            "id": "B2",
            "title": "Hybrid retrieval",
            "description": "BM25 + FAISS + RRF fusion, SQLite persistence across restarts",
            "checks": [
                {
                    "label": "BM25 + FAISS + RRF",
                    "done": _exists(
                        "vectorstore/bm25_retriever.py",
                        "vectorstore/store.py",
                        "vectorstore/rrf.py",
                    ),
                },
                {
                    "label": "Sentence-transformer embedder",
                    "done": _exists("vectorstore/embedder.py"),
                },
                {
                    "label": "SQLite persistence (survives restarts)",
                    "done": _exists("persistence/vector_store_repo.py")
                    and _grep(r"ON CONFLICT|upsert|persist_texts", "persistence/vector_store_repo.py"),
                },
            ],
        },
        {
            "id": "B3",
            "title": "RCA workflow",
            "description": "LangGraph pipeline: hypothesize → collect → analyze → remediate",
            "checks": [
                {
                    "label": "LangGraph graph + nodes",
                    "done": _exists("workflow/graph.py", "workflow/nodes.py"),
                },
                {
                    "label": "Confidence scoring",
                    "done": _exists("rca/confidence.py"),
                },
                {
                    "label": "Context window + prompt builder",
                    "done": _exists("rca/context_builder.py"),
                },
                {
                    "label": "Retry on LOW confidence (wider BFS)",
                    "done": _grep(r"retry_count|MAX_RETRIES", "workflow/nodes.py"),
                },
            ],
        },
        {
            "id": "B4",
            "title": "Human gate + API",
            "description": "REST API with approve/reject endpoint, SSE streaming, session persistence",
            "checks": [
                {
                    "label": "FastAPI server + session management",
                    "done": _exists("api/app.py", "api/routes/sessions.py"),
                },
                {
                    "label": "Approve / reject endpoint",
                    "done": _grep(r"human_decision|approve|reject", "api/routes/sessions.py"),
                },
                {
                    "label": "SSE streaming",
                    "done": _grep(r"StreamingResponse|text/event-stream", "api/routes/sessions.py"),
                },
                {
                    "label": "Alertmanager webhook receiver",
                    "done": _exists("api/routes/webhook.py"),
                },
            ],
        },
        {
            "id": "B5",
            "title": "Blast radius + Rollback plan",
            "description": "Risk scoring (LOW→CRITICAL), rollback plan generated before any action",
            "checks": [
                {
                    "label": "Rollback generation (helm rollback, rollout undo)",
                    "done": _grep(r"_generate_rollback|helm rollback|rollout undo", "rca/analyzer.py"),
                },
                {
                    "label": "Dedicated blast_radius module",
                    "done": _exists("remediation/blast_radius.py"),
                },
                {
                    "label": "Risk level scoring (LOW / MEDIUM / HIGH / CRITICAL)",
                    "done": _grep(r"CRITICAL|risk_level|risk_penalty", "remediation")
                    or _grep(r"CRITICAL|risk_level", "rca/analyzer.py"),
                },
                {
                    "label": "Rollback unavailable → hard NO_GO",
                    "done": _grep(
                        r"rollback.*available.*False|NO_GO.*rollback",
                        "remediation", "decision",
                    ),
                },
            ],
        },
        {
            "id": "B6",
            "title": "Decision Engine",
            "description": "Beam search paths, Monte Carlo stability, policy gate AUTO / HUMAN_REVIEW / NO_GO",
            "checks": [
                {
                    "label": "Beam search + path state machine",
                    "done": _exists("reasoning/beam_search.py"),
                },
                {
                    "label": "Monte Carlo stability (n=200 sims)",
                    "done": _exists("reasoning/monte_carlo.py"),
                },
                {
                    "label": "Policy gate (AUTO / HUMAN_REVIEW / NO_GO)",
                    "done": _exists("decision/policy_gate.py"),
                },
                {
                    "label": "DecisionEngine orchestrator (canonical IncidentReport → verdict, testable)",
                    "done": _exists("decision/decision_engine.py", "decision/models.py")
                    and _grep(r"class DecisionEngine", "decision/decision_engine.py"),
                },
                {
                    "label": "Template catalog (community runbooks)",
                    "done": _exists("reasoning/template_catalog.py"),
                },
            ],
        },
        {
            "id": "B7",
            "title": "Distribution",
            "description": "Helm chart, Artifact Hub listing, one-command quickstart",
            "checks": [
                {
                    "label": "Helm chart (Chart.yaml + values.yaml)",
                    "done": _exists("helm/kube-verdict/Chart.yaml", "helm/kube-verdict/values.yaml"),
                },
                {
                    "label": "Demo cluster setup (k3d)",
                    "done": _exists("demo/focused") and len(list((ROOT / "demo/focused").glob("scenario_*.py"))) >= 3,
                },
                {
                    # Verifies the publishing pipeline is wired — NOT that an image
                    # is live (a workflow file proves capability, not a release).
                    "label": "Release pipeline → GHCR (publishes on v* tag)",
                    "done": _exists(".github/workflows/release.yml")
                    and _grep(r"ghcr\.io", ".github/workflows/release.yml"),
                },
                {
                    # Factual: a v* tag was actually pushed → the image really built.
                    # Stays red until the first real release is cut.
                    "label": "Versioned release tagged (v* pushed → image on GHCR)",
                    "done": _git_tag(r"^v\d+\."),
                },
                {
                    "label": "Quickstart < 30 min documented",
                    "done": _grep(r"[Qq]uick.?[Ss]tart|one command|30 min|5 min", "README.md"),
                },
            ],
        },
        {
            "id": "B8",
            "title": "Agent Skills / MCP",
            "description": "Expose pipeline stages as composable agent skills — MCP server, SKILL.md, OpenAPI tool schema",
            "checks": [
                {
                    "label": "MCP server (kube-rca, helm-drift, blast-radius as tools)",
                    "done": _exists("mcp_server.py") or _exists("mcp/server.py"),
                },
                {
                    "label": "SKILL.md for Claude Code integration",
                    "done": _exists("SKILL.md") or _exists(".claude/SKILL.md"),
                },
                {
                    "label": "OpenAPI tool schema (OpenAI function-calling compatible)",
                    "done": _exists("openapi_tools.json") or _exists("api/tools_schema.json"),
                },
                {
                    "label": "Integration documented (Cursor / Claude Desktop)",
                    "done": _grep(r"[Cc]ursor|[Cc]laude [Dd]esktop|MCP.*integration", "README.md", "docs"),
                },
            ],
        },
        {
            "id": "B9",
            "title": "Decision Introspection UI",
            "description": "Real-time visualization of beam-search decisions, fallback collectors, and eliminated hypothesis paths",
            "checks": [
                {
                    "label": "API: expose reasoning_history + fallback_collectors in /state",
                    "done": _grep(r"eliminated_paths|fallback_collectors|reasoning_history", "api/routes/sessions.py"),
                },
                {
                    "label": "Edge-log timeline — routing decisions with reason, confidence, beam_switches",
                    "done": _grep(r"EdgeTimeline|EdgeLog|edge.log.timeline", "dashboard/src"),
                },
                {
                    "label": "Eliminated-paths panel — archived hypotheses with elimination reason",
                    "done": _grep(r"EliminatedPaths|eliminated.paths|Eliminated", "dashboard/src"),
                },
                {
                    "label": "Fallback-status overlay — per-collector OK / FALLBACK badge + error tooltip",
                    "done": _grep(r"FallbackStatus|fallback.badge|fallback_collectors", "dashboard/src"),
                },
                {
                    "label": "Beam-search tree — SVG dag: active path vs archived branches",
                    "done": _grep(r"BeamTree|beam.tree|beam.*svg|beam.*dag", "dashboard/src"),
                },
                {
                    "label": "Live SSE refresh — introspection panel updates in real time via /stream",
                    "done": _grep(r"IntrospectionPanel|introspection.*SSE|SSE.*introspect", "dashboard/src"),
                },
            ],
        },
        {
            "id": "B10",
            "title": "Loki Full Integration",
            "description": "Structured log parsing, error clustering, multi-tenant support, WebSocket tail, alert rule ingestion, dashboard log tab",
            "checks": [
                {
                    "label": "Structured log parsing — JSON fields as LokiLog annotations",
                    "done": _grep(r"json\.loads.*log|structured.*log|log_fields|parse_json_log", "ingestion/loki_source.py"),
                },
                {
                    "label": "Error clustering — near-duplicate log lines → LogCluster nodes",
                    "done": _grep(r"LogCluster|log.*cluster|cluster.*log", "ingestion"),
                },
                {
                    "label": "Multi-tenant support — X-Scope-OrgID header (LOKI_ORG_ID env var)",
                    "done": _grep(r"X-Scope-OrgID|LOKI_ORG_ID|org_id", "ingestion/loki_source.py"),
                },
                {
                    "label": "LogQL streaming tail — live SSE log events via /loki/api/v1/tail",
                    "done": _grep(r"loki/api/v1/tail|websocket.*loki|loki.*websocket", "ingestion"),
                },
                {
                    "label": "Loki alert rule ingestion — HAS_LOG_ALERT edges from ruler API",
                    "done": _grep(r"HAS_LOG_ALERT|loki.*rules|ruler.*loki", "ingestion", "ontology"),
                },
                {
                    "label": "Dashboard Loki tab — log lines with level badge + trace_id link",
                    "done": _grep(r"LokiTab|LokiPanel|loki.*tab", "dashboard/src"),
                },
                {
                    "label": "Integration test case — log-first RCA (no Prometheus signal)",
                    "done": _exists("tests/integration/cases") and any(
                        "loki" in str(p).lower() for p in (ROOT / "tests/integration/cases").iterdir()
                        if p.is_dir()
                    ),
                },
            ],
        },
        {
            "id": "B11",
            "title": "Production Hardening",
            "description": "What separates a validated prototype from a prod-grade deployment: auth, regression guard, supply-chain listing, scoped RBAC, secret management",
            "checks": [
                {
                    "label": "API auth (JWT / OIDC on session + webhook routes)",
                    "done": _grep(r"jwt|oidc|OAuth2|Bearer|verify_token|require_auth", "api"),
                },
                {
                    "label": "Golden-scenario regression guard (replay + diff in CI)",
                    "done": _exists("tests/golden")
                    or _grep(r"golden.*replay|replay.*golden|regression.*guard", "tests"),
                },
                {
                    "label": "Artifact Hub listing (artifacthub-repo.yml)",
                    "done": _exists("artifacthub-repo.yml")
                    or _exists("helm/kube-verdict/artifacthub-repo.yml"),
                },
                {
                    "label": "RBAC-aware scoping (service-account impersonation)",
                    "done": _grep(r"impersonat|as_user|ImpersonationConfig", "ingestion", "api"),
                },
                {
                    "label": "Secret management (Vault / external-secrets, no plaintext kubeconfig)",
                    "done": _grep(r"vault|external.?secret|VAULT_ADDR", "ingestion", "api", "helm"),
                },
            ],
        },
        {
            "id": "B12",
            "title": "Common Interface (IDP contract)",
            "description": "One canonical verdict shared by the API, the MCP tools and any IDP consumer — frozen schema, single investigation pipeline, published integration contract",
            "checks": [
                {
                    "label": "Canonical verdict model frozen (IncidentReport + schema contract test)",
                    "done": _exists("decision/models.py", "tests/unit/test_decision_models.py")
                    and _grep(r"class IncidentReport", "decision/models.py"),
                },
                {
                    "label": "MCP routed through the canonical investigation service",
                    "done": _grep(r"services\.investigation_service|run_investigation", "mcp_server.py"),
                },
                {
                    "label": "IDP integration contract published (docs/idp-contract.md)",
                    "done": _exists("docs/idp-contract.md"),
                },
            ],
        },
    ]


def _status(checks: list[dict]) -> str:
    passed = sum(1 for c in checks if c["done"])
    if passed == len(checks):
        return "DONE"
    if passed == 0:
        return "TODO"
    return "IN_PROGRESS"


# Maturity phases — group blocs into a credibility narrative rather than a flat list.
# Order here is the render order on the dashboard.
PHASES: list[tuple[str, list[str]]] = [
    ("Foundation", ["B1", "B2", "B3", "B4", "B5"]),
    ("Decision Engine", ["B6"]),
    ("Common Interface", ["B12"]),
    ("Distribution & Skills", ["B7", "B8"]),
    ("Deep Observability", ["B9", "B10"]),
    ("Production Hardening", ["B11"]),
]
_PHASE_OF = {bid: name for name, ids in PHASES for bid in ids}


def build() -> dict:
    blocs = _blocs()
    for b in blocs:
        b["status"] = _status(b["checks"])
        b["phase"] = _PHASE_OF.get(b["id"], "Other")
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "phases": [name for name, _ in PHASES],
        "blocs": blocs,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate KubeVerdict roadmap")
    parser.add_argument("--output", default="dashboard/src/roadmap.json")
    args = parser.parse_args()

    data = build()
    out = ROOT / args.output
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=2) + "\n")

    for b in data["blocs"]:
        icon = "✓" if b["status"] == "DONE" else ("…" if b["status"] == "IN_PROGRESS" else "✗")
        passed = sum(1 for c in b["checks"] if c["done"])
        print(f"  {icon} {b['id']} {b['title']} ({passed}/{len(b['checks'])})")

    print(f"\nWritten → {out}")
