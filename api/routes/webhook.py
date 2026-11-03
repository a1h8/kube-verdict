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
from api.webhook_mapper import (
    alert_text, alert_to_namespaces, alert_to_query, firing_alerts, label_value, resource_of,
)
from api.signal_mapper import anomalous_signals, signal_text, signal_to_namespaces, signal_to_query

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/webhook", tags=["webhook"])

# One running investigation per alert identity, and a global cap: an alert storm used to start one
# full investigation (cluster collection + LLM) per alert, all at once (docs/fix-plan-001.md H53).
# The identity is (alert name, resource, namespaces): two pods crash-looping in one namespace are two
# investigations, each named in its own query (H71).
_running: dict[tuple, asyncio.Task] = {}


def _start(key: tuple, query: str, namespaces: list[str], alert_text: str = "") -> str | None:
    """Start an investigation for *key* unless one is running, the cap is reached, or no
    namespace is known (an alert without one used to investigate every namespace, H71)."""
    import config as cfg
    from api.routes.sessions import _run_graph
    if not namespaces:
        log.warning("webhook: %s has no namespace label and KUBE_NAMESPACES is empty — skipped", key)
        return None
    for k in [k for k, t in _running.items() if t.done()]:
        _running.pop(k, None)
    if key in _running:
        log.info("webhook: %s already under investigation — skipped", key)
        return None
    if len(_running) >= cfg.WEBHOOK_MAX_RUNNING:
        log.warning("webhook: %d investigations running (cap) — %s skipped", len(_running), key)
        return None
    session_id = str(uuid.uuid4())
    store.create(session_id)
    initial_state = {"query": query, "namespaces": namespaces, "alert_text": alert_text,
                     "edge_log": []}
    store.set_last_state(session_id, initial_state)
    session = store.get_or_404(session_id)
    task = asyncio.create_task(_run_graph(session, initial_state))
    session.task = task
    _running[key] = task
    return session_id


@router.post("/alertmanager", response_model=WebhookTriggered, status_code=202, dependencies=[Depends(require_auth)])
async def alertmanager_webhook(payload: AlertmanagerPayload) -> WebhookTriggered:
    active = firing_alerts(payload.alerts)
    skipped = len(payload.alerts) - len(active)
    session_ids: list[str] = []
    for alert in active:
        namespaces = alert_to_namespaces(alert)
        key = ("alert", label_value(alert.labels.get("alertname", "")), resource_of(alert.labels),
               tuple(namespaces))
        session_id = _start(key, alert_to_query(alert), namespaces, alert_text(alert))
        if session_id is None:
            skipped += 1
            continue
        log.info("webhook: started session %s for alert %s", session_id, alert.labels.get("alertname"))
        session_ids.append(session_id)
    return WebhookTriggered(session_ids=session_ids, skipped=skipped)


@router.post("/signal", response_model=WebhookTriggered, status_code=202, dependencies=[Depends(require_auth)])
async def signal_webhook(payload: SignalAlertPayload) -> WebhookTriggered:
    active = anomalous_signals(payload.alerts)
    skipped = len(payload.alerts) - len(active)
    session_ids: list[str] = []
    for alert in active:
        key = ("signal", alert.entity_uid, alert.metric_name)
        session_id = _start(key, signal_to_query(alert), signal_to_namespaces(alert),
                            signal_text(alert))
        if session_id is None:
            skipped += 1
            continue
        log.info("webhook: started session %s for signal %s/%s", session_id, alert.entity_uid, alert.metric_name)
        session_ids.append(session_id)
    return WebhookTriggered(session_ids=session_ids, skipped=skipped)
