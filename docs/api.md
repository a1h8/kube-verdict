# REST API

KubeWhisperer exposes a FastAPI REST interface for the LangGraph RCA workflow. Swagger UI is auto-generated at `http://localhost:8000/docs` when the server is running.

## Start the API server

```bash
uvicorn api.app:app --reload
# → http://localhost:8000
# → http://localhost:8000/docs   (Swagger UI)
# → http://localhost:8000/openapi.json
```

## Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness check |
| `POST` | `/api/v1/sessions` | Create a session, returns `session_id` |
| `POST` | `/api/v1/sessions/{id}/run` | Start RCA on a query |
| `GET` | `/api/v1/sessions/{id}/state` | Poll current state |
| `GET` | `/api/v1/sessions/{id}/stream` | SSE stream of state updates |
| `POST` | `/api/v1/sessions/{id}/feedback` | Approve / reject remediation or re-run with extra context |
| `DELETE` | `/api/v1/sessions/{id}` | Delete session |

## Session lifecycle

```
POST /sessions          → IDLE
POST /sessions/{id}/run → RUNNING
                        → AWAITING_REVIEW  (human gate triggered)
POST /sessions/{id}/feedback (approve|reject) → RUNNING → COMPLETED
                           or
                        → COMPLETED / FAILED  (no interrupt)
```

## Request / response examples

### Create a session

```bash
SESSION=$(curl -s -X POST http://localhost:8000/api/v1/sessions | jq -r .session_id)
```

### Start RCA

```bash
curl -X POST http://localhost:8000/api/v1/sessions/$SESSION/run \
  -H "Content-Type: application/json" \
  -d '{
    "query": "payment-service is crashing repeatedly",
    "namespaces": ["production"],
    "kube_context": "k3d-kubewhisperer"
  }'
```

`namespaces`, `kubeconfig`, and `kube_context` are optional. When omitted, the defaults from `.env` apply.

### Poll state

```bash
curl http://localhost:8000/api/v1/sessions/$SESSION/state | jq .status
# "RUNNING" | "AWAITING_REVIEW" | "COMPLETED" | "FAILED"
```

### SSE stream (real-time updates)

```bash
curl -N http://localhost:8000/api/v1/sessions/$SESSION/stream
# data: {"session_id": "...", "status": "RUNNING", ...}
# data: {"session_id": "...", "status": "AWAITING_REVIEW", ...}
# data: {"done": true}
```

### Approve / reject remediation

```bash
# Approve
curl -X POST http://localhost:8000/api/v1/sessions/$SESSION/feedback \
  -H "Content-Type: application/json" \
  -d '{"human_decision": "approve"}'

# Reject
curl -X POST http://localhost:8000/api/v1/sessions/$SESSION/feedback \
  -H "Content-Type: application/json" \
  -d '{"human_decision": "reject"}'
```

### Re-run with extra context (after COMPLETED / FAILED)

```bash
curl -X POST http://localhost:8000/api/v1/sessions/$SESSION/feedback \
  -H "Content-Type: application/json" \
  -d '{"extra_context": "also check the redis sidecar — it was restarted at 02:14"}'
```

## SessionState fields

| Field | Type | Description |
|---|---|---|
| `session_id` | string | UUID |
| `status` | enum | `IDLE` `RUNNING` `AWAITING_REVIEW` `COMPLETED` `FAILED` |
| `confidence` | string | `HIGH` `MEDIUM` `LOW` — set after RCA |
| `current_hypothesis` | string | Active hypothesis being evaluated |
| `candidate_paths` | list[str] | All ranked hypotheses |
| `reasoning_history` | list[dict] | LLM reasoning steps |
| `edge_log` | list[EdgeEntry] | Router decisions with reason + snapshot |
| `events` | list[str] | K8s Warning events used as evidence |
| `alerts` | list[str] | Prometheus firing alerts |
| `traces` | list[str] | OTel error traces |
| `anchor_fixes` | list[str] | Helm commands for declared→observed drift |
| `policy_violations` | list[str] | OPA / Kyverno violations |
| `causal_chain` | list[str] | Root cause chain from report |
| `suggestions` | list[str] | Remediation commands proposed |
| `dry_run_results` | list[dict] | Dry-run validation results |
| `review_payload` | dict | Full review context when `AWAITING_REVIEW` |
| `error` | string | Error message if `FAILED` |
