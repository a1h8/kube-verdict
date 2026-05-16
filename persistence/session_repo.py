"""
SQLite-backed session repository.

Persists API session metadata so sessions survive server restarts.
The asyncio.Task for running workflows is NOT stored (it is in-process only);
after a restart the task field is None and the status is set to FAILED so the
caller knows the run did not complete.
"""
from __future__ import annotations
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_json(obj: Any) -> str | None:
    if obj is None:
        return None
    return json.dumps(obj)


def _from_json(raw: str | None) -> Any:
    if raw is None:
        return None
    return json.loads(raw)


# ── Writes ────────────────────────────────────────────────────────────────────

def create_session(conn: sqlite3.Connection, session_id: str) -> None:
    now = _now()
    conn.execute(
        "INSERT INTO sessions (session_id, status, created_at, updated_at)"
        " VALUES (?, 'IDLE', ?, ?)",
        (session_id, now, now),
    )
    conn.commit()


def update_session(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    status: str | None = None,
    last_state: dict[str, Any] | None = None,
    review_payload: dict[str, Any] | None = None,
    error: str | None = None,
    clear_error: bool = False,
) -> None:
    fields: list[str] = ["updated_at = ?"]
    params: list[Any] = [_now()]

    if status is not None:
        fields.append("status = ?")
        params.append(status)
    if last_state is not None:
        fields.append("last_state = ?")
        params.append(_to_json(last_state))
    if review_payload is not None:
        fields.append("review_payload = ?")
        params.append(_to_json(review_payload))
    if error is not None:
        fields.append("error = ?")
        params.append(error)
    if clear_error:
        fields.append("error = NULL")

    params.append(session_id)
    conn.execute(
        f"UPDATE sessions SET {', '.join(fields)} WHERE session_id = ?",
        params,
    )
    conn.commit()


def delete_session(conn: sqlite3.Connection, session_id: str) -> None:
    conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
    conn.commit()


# ── Reads ─────────────────────────────────────────────────────────────────────

def get_session_row(conn: sqlite3.Connection, session_id: str) -> sqlite3.Row | None:
    cur = conn.execute(
        "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
    )
    return cur.fetchone()


def load_last_state(conn: sqlite3.Connection, session_id: str) -> dict[str, Any] | None:
    row = get_session_row(conn, session_id)
    if row is None:
        return None
    return _from_json(row["last_state"])


def load_review_payload(conn: sqlite3.Connection, session_id: str) -> dict[str, Any] | None:
    row = get_session_row(conn, session_id)
    if row is None:
        return None
    return _from_json(row["review_payload"])


def list_sessions(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    cur = conn.execute(
        "SELECT session_id, status, created_at, updated_at, error"
        " FROM sessions ORDER BY created_at DESC"
    )
    return cur.fetchall()
