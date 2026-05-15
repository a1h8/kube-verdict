"""
In-memory session registry.

Each session wraps a LangGraph thread (thread_id == session_id) and tracks
the background asyncio task running the graph so the API can interrupt it
for human_review or extra_context injection.
"""
from __future__ import annotations
import asyncio
from dataclasses import dataclass, field
from typing import Any

from api.models import SessionStatus


@dataclass
class Session:
    session_id: str
    status: SessionStatus = SessionStatus.IDLE
    task: asyncio.Task | None = None
    review_payload: dict[str, Any] | None = None   # set at human_review interrupt
    last_state: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def create(self, session_id: str) -> Session:
        s = Session(session_id=session_id)
        self._sessions[session_id] = s
        return s

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def get_or_404(self, session_id: str) -> Session:
        s = self.get(session_id)
        if s is None:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail=f"session {session_id!r} not found")
        return s

    def delete(self, session_id: str) -> None:
        s = self._sessions.pop(session_id, None)
        if s and s.task and not s.task.done():
            s.task.cancel()


store = SessionStore()
