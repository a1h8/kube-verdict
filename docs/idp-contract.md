# KubeVerdict IDP Contract

> Integration contract for consuming KubeVerdict as an **Internal Developer Platform capability** —
> diagnose, score, explain and gate Kubernetes / Helm / GitOps actions.
> This is an integration document, not marketing.
>
> **Status legend:** ✅ shipped today · 🎯 target (project converging toward it)

## Purpose

A Kubernetes incident-investigation capability that a platform can call to turn an
alert / event / deployment into an **evidence-grounded verdict** with a confidence
score, a blast-radius estimate, a proposed remediation, a rollback, and a policy
decision — without letting an LLM act on its own.

## Consumers

- Internal developer portal — e.g. **Backstage** (via REST API) 🎯
- Workflow orchestration / SRE automation — e.g. **n8n**, runbooks (via REST API / webhook) 🎯
- Alertmanager webhook ✅
- GitOps workflow (PR / patch proposal) 🎯
- Agent runtime / MCP tools ✅

## Inputs

| Input | Status |
|-------|--------|
| `namespace` + free-text `query` | ✅ |
| `kubeconfig` / `kube_context` (read-only) | ✅ |
| Alertmanager alert payload (labels / annotations) | ✅ (webhook) |
| Structured `service` / `environment` | 🎯 |
| GitOps repo / chart / release reference | 🎯 |

## Outputs (the verdict)

| Field | Status |
|-------|--------|
| `root_cause` | ✅ |
| `evidence[]` (events, alerts, traces, anchor fixes, policy violations) | ✅ |
| `causal_chain` | ✅ |
| `confidence` (label; score 🎯) | ✅ |
| `blast_radius` (LOW / MEDIUM / HIGH / CRITICAL) | ✅ |
| `remediation` + `rollback` | ✅ |
| `policy` (AUTO / HUMAN_REVIEW / NO_GO) | ✅ |
| **single stable JSON envelope** | 🎯 (see *Verdict schema → target*) |

## Verdict schema

### Today ✅ — surfaced on `SessionState` (split fields)

```json
{
  "verdict": "HUMAN_REVIEW",
  "verdict_reasons": ["score 0.65 < 0.85 AUTO threshold", "namespace 'prod' is production"],
  "incident_report": {
    "severity": "HIGH",
    "confidence": "HIGH",
    "root_cause": "Deployment payment-api missing env var DATABASE_URL",
    "impact": ["Deployment/prod/payment-api"],
    "evidence": ["CrashLoopBackOff x12", "Back-off restarting failed container"],
    "remediation": ["kubectl set env deploy/payment-api DATABASE_URL=... -n prod"],
    "rollback": ["kubectl rollout undo deploy/payment-api -n prod"]
  },
  "blast_radius": {
    "risk": "MEDIUM", "summary": "1 resource — ns: prod",
    "resources": ["Deployment/prod/payment-api"], "namespaces": ["prod"],
    "cluster_scoped": false, "command_count": 1
  }
}
```

### Target 🎯 — one unified envelope (Bloc B)

```json
{
  "session_id": "kv-2026-06-02-001",
  "service": "payment-api",
  "namespace": "prod",
  "environment": "prod",
  "root_cause": "Missing env var DATABASE_URL",
  "confidence_label": "HIGH",
  "confidence_score": 0.87,
  "policy": "HUMAN_REVIEW",
  "blast_radius": "MEDIUM",
  "evidence": [{ "source": "k8s", "kind": "event", "summary": "CrashLoopBackOff x12", "reference": "Pod/prod/payment-api-…" }],
  "remediation": { "action_type": "kubectl", "description": "set DATABASE_URL", "command": "kubectl set env …", "rollback": "kubectl rollout undo …" },
  "next_steps": ["approve to apply via dry-run", "or open a GitOps PR"]
}
```

> Convergence note: the target envelope is built by **fusing** the existing
> `IncidentReport`, `DecisionResult`, `BlastRadius` and `RollbackPlan` types —
> not by introducing a parallel model. (The envelope cannot be named `Verdict`;
> `Verdict` is already the policy-state enum.)

## Policy states ✅

| State | Meaning |
|-------|---------|
| `AUTO` | Auto-classify + auto-generate patch/rollback + mark low-risk. **Non-prod, LOW blast radius, rollback present, Monte-Carlo stable only.** Never applies in prod. |
| `HUMAN_REVIEW` | Default. Operator approves before any apply. **Production is always at least HUMAN_REVIEW.** |
| `NO_GO` | Blocked: score too low, CRITICAL blast radius, no rollback, or reasoning exhausted. |

## Safety guarantees ✅

- Read-only evidence collection by default (scoped RBAC).
- No autonomous production remediation — human gate before execution.
- Rollback required; absence forces `NO_GO`.
- Dry-run before any apply.
- Per-session audit trail (`edge_log`: every routing decision and why).

## Integration flow

```
Portal / API / Alertmanager / MCP
   → Investigation session            (POST /sessions, /run)        ✅
   → Evidence collection              (K8s events ✅, Helm/GitOps drift ✅, Prom/Loki/OTel 🎯-E2E)
   → Operational graph + hybrid retrieval + anchors + scoring        ✅
   → Verdict + action plan                                           ✅
   → Policy gate → AUTO / HUMAN_REVIEW / NO_GO                        ✅
        ├─ HUMAN_REVIEW → human approval (POST /feedback)            ✅
        ├─ approved     → dry-run → apply / GitOps PR                ✅ apply · 🎯 PR
        └─ NO_GO        → blocked, with reasons                       ✅
```

## API

Base path: `/api/v1`. OpenAPI at `/docs`.

```bash
ID=$(curl -s -X POST http://localhost:8000/api/v1/sessions | jq -r .session_id)   # ✅

# run an investigation (async — returns RUNNING)
curl -X POST http://localhost:8000/api/v1/sessions/$ID/run \                        # ✅
  -H 'Content-Type: application/json' \
  -d '{"query":"pods crashlooping","namespaces":["demo"]}'

# follow progress: poll state, or stream Server-Sent Events
curl http://localhost:8000/api/v1/sessions/$ID/state                                # ✅
curl -N http://localhost:8000/api/v1/sessions/$ID/stream                            # ✅ SSE

# approve / reject when status is AWAITING_REVIEW
curl -X POST http://localhost:8000/api/v1/sessions/$ID/feedback \                   # ✅
  -H 'Content-Type: application/json' -d '{"human_decision":"approve"}'

# Alertmanager entry point
curl -X POST http://localhost:8000/api/v1/webhook/alertmanager -d @alert.json       # ✅
```

Target one-shot endpoint for portal integration:

```bash
# 🎯 returns a stable Verdict envelope directly (sync on cache, else session id)
curl -X POST http://localhost:8000/api/v1/investigate \
  -H 'Content-Type: application/json' \
  -d '{"service":"webapp","namespace":"demo","environment":"dev","signal":"CrashLoopBackOff"}'
```

## Developer portal integration — Backstage 🎯

KubeVerdict is designed to sit **on top of** a portal's existing software catalog +
Kubernetes view, as a diagnosis capability — not as a competing portal. No plugin
ships in this repo yet; the integration pattern is:

- A Backstage entity page action — *"Investigate this service"* — on a catalog component.
- The Backstage backend calls `POST /api/v1/sessions` + `/run` with the entity's
  namespace/service.
- The SSE `stream` drives a live progress card.
- The final card renders the verdict: root cause, confidence, evidence, blast radius,
  proposed remediation, and the policy state (with the approve/reject action wired to
  `/feedback` when `HUMAN_REVIEW`).

This reuses the same REST contract above — no Backstage-specific server logic in KubeVerdict.

## Roadmap to the full contract (🎯)

1. Single stable verdict JSON envelope (fuse existing models) — *Bloc B*.
2. `confidence_score` alongside the label.
3. `POST /investigate` one-shot endpoint + structured `service`/`environment`/`signal` inputs.
4. GitOps PR / patch proposal as a remediation `action_type`.
5. Prometheus / Loki / OTel evidence validated end-to-end (collectors exist today).
6. Backstage plugin / portal entity action (reuses the REST contract — no KubeVerdict-side coupling).
