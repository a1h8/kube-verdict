"""
Alertmanager webhook receiver.

POST /api/v1/webhook/alertmanager
  → for each firing alert, create a session and start an RCA run.
  → resolved alerts are silently skipped.
"""
from __future__ import annotations
import asyncio
import uuid
import logging

from fastapi import APIRouter, Depends

from api.auth import require_token
from api.models import AlertmanagerPayload, WebhookTriggered
from api.session_store import store
from api.webhook_mapper import alert_to_namespaces, alert_to_query, firing_alerts

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/webhook", tags=["webhook"])


@router.post("/alertmanager", response_model=WebhookTriggered, status_code=202, dependencies=[Depends(require_token)])
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
