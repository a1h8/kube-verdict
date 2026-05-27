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
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent


def _exists(*paths: str) -> bool:
    return all((ROOT / p).exists() for p in paths)


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
                    "label": "Published to Artifact Hub",
                    "done": _grep(r"artifacthub\.io", "helm/kube-verdict/Chart.yaml"),
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
    ]


def _status(checks: list[dict]) -> str:
    passed = sum(1 for c in checks if c["done"])
    if passed == len(checks):
        return "DONE"
    if passed == 0:
        return "TODO"
    return "IN_PROGRESS"


def build() -> dict:
    blocs = _blocs()
    for b in blocs:
        b["status"] = _status(b["checks"])
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
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
