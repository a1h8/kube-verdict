"""
Session lifecycle: create → run → feedback → state / stream.
"""
from __future__ import annotations
import asyncio
import uuid
import logging
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from langgraph.types import Command

from api.models import (
    EdgeEntry, FeedbackRequest, RunRequest,
    SessionCreated, SessionState, SessionStatus,
)
from api.session_store import Session, store
from workflow.graph import build_graph

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])


def _build_graph(checkpointer=None):
    return build_graph(checkpointer=checkpointer)


# Default in-memory graph — replaced at startup by app.py lifespan with a
# SqliteSaver-backed instance.
_graph = _build_graph()

# Pre-loaded FAISS store — set by app.py lifespan if index.faiss exists on disk.
# When set, index_node skips re-embedding and uses this store directly.
_faiss_store = None


# ── helpers ───────────────────────────────────────────────────────────────────

def _state_to_response(session: Session) -> SessionState:
    s = session.last_state
    raw_log    = s.get("edge_log") or []
    report     = s.get("report_dict") or {}
    return SessionState(
        session_id         = session.session_id,
        status             = session.status,
        query              = s.get("query"),
        kube_version       = s.get("kube_version"),
        confidence         = s.get("confidence"),
        current_hypothesis       = s.get("current_hypothesis"),
        candidate_paths          = s.get("candidate_paths") or [],
        reasoning_history        = s.get("reasoning_history") or [],
        hypothesis_sources       = s.get("hypothesis_sources") or [],
        path_confidence_history  = s.get("path_confidence_history") or [],
        edge_log                 = [EdgeEntry(**e) for e in raw_log],
        ingestion_stats    = s.get("ingestion_stats") or {},
        report             = report or None,
        events             = report.get("events") or [],
        traces             = report.get("traces") or [],
        alerts             = report.get("alerts") or [],
        anchor_fixes       = report.get("anchor_fixes") or [],
        policy_violations  = report.get("policy_violations") or [],
        causal_chain       = report.get("causal_chain") or [],
        suggestions        = report.get("remediation") or [],
        dry_run_results    = s.get("dry_run_results") or [],
        review_payload     = session.review_payload,
        error              = session.error,
    )


async def _run_graph(session: Session, initial_state: dict, resume_cmd: Command | None = None) -> None:
    """Run (or resume) the LangGraph workflow in a background task."""
    configurable: dict = {"thread_id": session.session_id}
    if _faiss_store is not None:
        configurable["store"] = _faiss_store
    cfg = {"configurable": configurable}
    try:
        store.set_status(session.session_id, SessionStatus.RUNNING)
        if resume_cmd is not None:
            events = _graph.astream(resume_cmd, cfg, stream_mode="values")
        else:
            events = _graph.astream(initial_state, cfg, stream_mode="values")

        async for state in events:
            store.set_last_state(session.session_id, dict(state))

        # Graph finished — check for interrupt (AWAITING_REVIEW) or completion
        snapshot = _graph.get_state(cfg)
        if snapshot.next:
            interrupt_data = None
            for task in (snapshot.tasks or []):
                if getattr(task, "interrupts", None):
                    interrupt_data = task.interrupts[0].value
                    break
            store.set_review_payload(session.session_id, interrupt_data)
            store.set_status(session.session_id, SessionStatus.AWAITING_REVIEW)
        else:
            store.set_status(session.session_id, SessionStatus.COMPLETED)

    except asyncio.CancelledError:
        pass
    except Exception as exc:
        log.exception("session %s failed", session.session_id)
        store.set_error(session.session_id, str(exc))
        store.set_status(session.session_id, SessionStatus.FAILED)


# ── endpoints ─────────────────────────────────────────────────────────────────

@router.post("", response_model=SessionCreated, status_code=201)
async def create_session() -> SessionCreated:
    session_id = str(uuid.uuid4())
    store.create(session_id)
    return SessionCreated(session_id=session_id)


@router.post("/{session_id}/run", response_model=SessionState)
async def run_session(session_id: str, body: RunRequest) -> SessionState:
    session = store.get_or_404(session_id)
    if session.status == SessionStatus.RUNNING:
        raise HTTPException(409, "session already running — wait for completion or send feedback")

    initial_state = {
        "query":        body.query,
        "namespaces":   body.namespaces,
        "kubeconfig":   body.kubeconfig,
        "kube_context": body.kube_context,
        "edge_log":     [],
    }
    store.set_last_state(session_id, initial_state)
    store.set_review_payload(session_id, None)
    store.set_status(session_id, SessionStatus.RUNNING)

    task = asyncio.create_task(_run_graph(session, initial_state))
    session.task = task
    return _state_to_response(session)


@router.post("/{session_id}/feedback", response_model=SessionState)
async def feedback(session_id: str, body: FeedbackRequest) -> SessionState:
    session = store.get_or_404(session_id)

    if session.status == SessionStatus.AWAITING_REVIEW:
        decision = (body.human_decision or "reject").lower()
        resume = Command(resume=decision)
        task = asyncio.create_task(_run_graph(session, {}, resume_cmd=resume))
        session.task = task

    elif session.status in (SessionStatus.COMPLETED, SessionStatus.FAILED):
        if not body.extra_context:
            raise HTTPException(400, "extra_context required to re-run a completed/failed session")
        base = dict(session.last_state)
        base["query"]    = f"{base.get('query', '')} — additional context: {body.extra_context}"
        base["edge_log"] = []
        task = asyncio.create_task(_run_graph(session, base))
        session.task = task

    else:
        raise HTTPException(409, f"session status is {session.status} — cannot accept feedback now")

    return _state_to_response(session)


@router.get("/{session_id}/state", response_model=SessionState)
async def get_state(session_id: str) -> SessionState:
    return _state_to_response(store.get_or_404(session_id))


@router.get("/{session_id}/stream")
async def stream_session(session_id: str) -> StreamingResponse:
    """SSE stream — emits one JSON event per LangGraph state update."""
    session = store.get_or_404(session_id)

    async def _generate() -> AsyncIterator[str]:
        cfg = {"configurable": {"thread_id": session_id}}
        async for event in _graph.astream(None, cfg, stream_mode="values"):
            store.set_last_state(session_id, dict(event))
            data = _state_to_response(session).model_dump_json()
            yield f"data: {data}\n\n"
        yield "data: {\"done\": true}\n\n"

    return StreamingResponse(_generate(), media_type="text/event-stream")


@router.delete("/{session_id}", status_code=204)
async def delete_session(session_id: str) -> None:
    store.get_or_404(session_id)
    store.delete(session_id)
