"""
SQLite-backed session registry.

Each session wraps a LangGraph thread (thread_id == session_id) and tracks
the background asyncio task running the graph so the API can interrupt it
for human_review or extra_context injection.

Persistence contract
--------------------
- Metadata (status, last_state, error, review_payload) → SQLite via
  persistence.session_repo so it survives server restarts.
- asyncio.Task → in-process only; not persisted. After a restart, sessions
  that were RUNNING are surfaced as FAILED so clients can resubmit.
"""
from __future__ import annotations
import asyncio
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from api.models import SessionStatus
from persistence import session_repo


@dataclass
class Session:
    session_id: str
    status: SessionStatus = SessionStatus.IDLE
    task: asyncio.Task | None = None
    review_payload: dict[str, Any] | None = None
    last_state: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class SessionStore:
    """
    Thread-safe (single event-loop) session registry backed by SQLite.

    An in-process cache avoids repeated DB reads for hot paths (SSE streaming,
    frequent /state polls).  Writes go to both cache and DB atomically.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._cache: dict[str, Session] = {}
        self._recover_interrupted()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def create(self, session_id: str) -> Session:
        s = Session(session_id=session_id)
        self._cache[session_id] = s
        session_repo.create_session(self._conn, session_id)
        return s

    def get(self, session_id: str) -> Session | None:
        if session_id in self._cache:
            return self._cache[session_id]
        # Cold path: session exists in DB but not in cache (restart scenario)
        row = session_repo.get_session_row(self._conn, session_id)
        if row is None:
            return None
        s = Session(
            session_id=session_id,
            status=SessionStatus(row["status"]),
            last_state=session_repo._from_json(row["last_state"]) or {},
            review_payload=session_repo._from_json(row["review_payload"]),
            error=row["error"],
        )
        self._cache[session_id] = s
        return s

    def get_or_404(self, session_id: str) -> Session:
        s = self.get(session_id)
        if s is None:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail=f"session {session_id!r} not found")
        return s

    def delete(self, session_id: str) -> None:
        s = self._cache.pop(session_id, None)
        if s and s.task and not s.task.done():
            s.task.cancel()
        session_repo.delete_session(self._conn, session_id)

    # ── Mutations (write-through) ─────────────────────────────────────────────

    def set_status(self, session_id: str, status: SessionStatus) -> None:
        if s := self._cache.get(session_id):
            s.status = status
        session_repo.update_session(self._conn, session_id, status=status.value)

    def set_last_state(self, session_id: str, state: dict[str, Any]) -> None:
        if s := self._cache.get(session_id):
            s.last_state = state
        session_repo.update_session(self._conn, session_id, last_state=state)

    def set_review_payload(self, session_id: str, payload: dict[str, Any] | None) -> None:
        if s := self._cache.get(session_id):
            s.review_payload = payload
        session_repo.update_session(self._conn, session_id, review_payload=payload)

    def set_error(self, session_id: str, error: str) -> None:
        if s := self._cache.get(session_id):
            s.error = error
        session_repo.update_session(self._conn, session_id, error=error)

    # ── Startup recovery ──────────────────────────────────────────────────────

    def _recover_interrupted(self) -> None:
        """Mark sessions that were RUNNING at shutdown as FAILED."""
        rows = session_repo.list_sessions(self._conn)
        for row in rows:
            if row["status"] == SessionStatus.RUNNING.value:
                session_repo.update_session(
                    self._conn,
                    row["session_id"],
                    status=SessionStatus.FAILED.value,
                    error="Server restarted while session was running.",
                )


# Module-level singleton — replaced with a real connection at app startup via
# init_store().  Tests call init_store(":memory:") for isolation.
_store: SessionStore | None = None


def init_store(db_path: str | None = None) -> SessionStore:
    """Initialise (or replace) the module-level store. Call once at startup.

    Pass db_path=":memory:" in tests for a fully isolated in-memory store.
    """
    global _store
    from persistence.db import _SESSION_DDL

    if db_path == ":memory:":
        # Each call must share a single connection — ":memory:" gives a fresh
        # database per connect(), so we can't use two separate connections.
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.executescript(_SESSION_DDL)
    else:
        from persistence.db import get_db, init_db
        import os
        if db_path is not None:
            os.environ["KUBEWHISPERER_DB"] = db_path
        init_db()
        conn = get_db()

    _store = SessionStore(conn)
    return _store


def get_store() -> SessionStore:
    if _store is None:
        return init_store()
    return _store


# Legacy alias used by routes — lazy so the import works before init_store()
class _StoreProxy:
    """Forwards attribute access to the singleton, resolving lazily."""
    def __getattr__(self, name: str):  # type: ignore[override]
        return getattr(get_store(), name)


store: SessionStore = _StoreProxy()  # type: ignore[assignment]
