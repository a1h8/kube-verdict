"""
Stable IDP entry point: one call → one verdict.

``POST /api/v1/investigate`` runs the canonical investigation pipeline
(``services.investigation_service.run_investigation``, proposal-only — it stops
at the policy verdict, never executes) and returns the stable
:class:`~api.verdict_contract.VerdictEnvelope`. This is the synchronous
counterpart to the session flow, for portals/agents that want a verdict in a
single request rather than create → run → poll.
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.auth import require_token
from api.verdict_contract import VerdictEnvelope
from services.investigation_service import run_investigation

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["investigate"])


class InvestigateRequest(BaseModel):
    query: str | None = None
    service: str | None = None
    namespace: str | None = None
    environment: str | None = None
    signal: str | None = None
    namespaces: list[str] | None = None
    kube_context: str | None = None


def _effective_query(body: InvestigateRequest) -> str:
    """Prefer an explicit query; otherwise synthesise one from service + signal."""
    if body.query:
        return body.query
    parts = [p for p in (body.service, body.signal) if p]
    if parts:
        scope = f" in namespace {body.namespace}" if body.namespace else ""
        return f"Investigate {' '.join(parts)}{scope}"
    return "Investigate cluster incident"


@router.post("/investigate", response_model=VerdictEnvelope, dependencies=[Depends(require_token)])
async def investigate(body: InvestigateRequest) -> VerdictEnvelope:
    # Import the route module lazily so the preloaded FAISS store set by the
    # app lifespan is reused (avoids re-embedding on every call).
    from api.routes import sessions as sessions_mod

    namespaces = body.namespaces or ([body.namespace] if body.namespace else None)
    session_id = uuid.uuid4().hex

    final_state = await run_investigation(
        query=_effective_query(body),
        namespaces=namespaces,
        kube_context=body.kube_context,
        store=sessions_mod._faiss_store,
        thread_id=session_id,
    )
    return VerdictEnvelope.from_state(
        session_id,
        final_state,
        service=body.service,
        namespace=body.namespace,
        environment=body.environment,
    )
