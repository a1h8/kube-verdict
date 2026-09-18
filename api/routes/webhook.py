"""
Webhook receivers — Alertmanager and pushed temporal-evidence signals.

POST /api/v1/webhook/alertmanager
  → for each firing alert, create a session and start an RCA run.
  → resolved alerts are silently skipped.

POST /api/v1/webhook/signal
  → for each anomalous pushed signal (e.g. PatchTST's kubeverdict-alert sink),
    create a session and start an RCA run.
  → normal-severity signals are silently skipped.
"""
from __future__ import annotations
import asyncio
import uuid
import logging

from fastapi import APIRouter, Depends

from api.oidc import require_auth
from api.models import AlertmanagerPayload, SignalAlertPayload, WebhookTriggered
from api.session_store import store
from api.webhook_mapper import alert_to_namespaces, alert_to_query, firing_alerts
from api.signal_mapper import anomalous_signals, signal_to_namespaces, signal_to_query

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/webhook", tags=["webhook"])


@router.post("/alertmanager", response_model=WebhookTriggered, status_code=202, dependencies=[Depends(require_auth)])
async def alertmanager_webhook(payload: AlertmanagerPayload) -> WebhookTriggered:
    from api.routes.sessions import _run_graph

    active = firing_alerts(payload.alerts)
    skipped = len(payload.alerts) - len(active)

    session_ids: list[str] = []
    for alert in active:
        session_id = str(uuid.uuid4())
        store.create(session_id)

        initial_state = {
            "query":      alert_to_query(alert),
            "namespaces": alert_to_namespaces(alert),
            "edge_log":   [],
        }
        store.set_last_state(session_id, initial_state)

        session = store.get_or_404(session_id)
        task = asyncio.create_task(_run_graph(session, initial_state))
        session.task = task

        log.info("webhook: started session %s for alert %s", session_id, alert.labels.get("alertname"))
        session_ids.append(session_id)

    return WebhookTriggered(session_ids=session_ids, skipped=skipped)


@router.post("/signal", response_model=WebhookTriggered, status_code=202, dependencies=[Depends(require_auth)])
async def signal_webhook(payload: SignalAlertPayload) -> WebhookTriggered:
    from api.routes.sessions import _run_graph

    active = anomalous_signals(payload.alerts)
    skipped = len(payload.alerts) - len(active)

    session_ids: list[str] = []
    for alert in active:
        session_id = str(uuid.uuid4())
        store.create(session_id)

        initial_state = {
            "query":      signal_to_query(alert),
            "namespaces": signal_to_namespaces(alert),
            "edge_log":   [],
        }
        store.set_last_state(session_id, initial_state)

        session = store.get_or_404(session_id)
        task = asyncio.create_task(_run_graph(session, initial_state))
        session.task = task

        log.info("webhook: started session %s for signal %s/%s", session_id, alert.entity_uid, alert.metric_name)
        session_ids.append(session_id)

    return WebhookTriggered(session_ids=session_ids, skipped=skipped)
