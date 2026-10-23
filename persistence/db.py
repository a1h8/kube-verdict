"""
SQLite connection management + schema bootstrap.

The DB path is controlled by env var KUBEWHISPERER_DB (default: kubeverdict.db
in the current working directory).

Tables
------
sessions          — API session metadata + last_state snapshot
checkpoints       — LangGraph thread checkpoints (managed by SqliteSaver)

There is no vector-store table: each investigation builds its index from what it collected
and the knowledge base (docs/fix-plan-001.md, H40). The former ``vector_store_docs`` table and
its reconstruction path had no production caller (H70); a database that still has the table
keeps it, unused.
"""
from __future__ import annotations
import os
import sqlite3

_DB_PATH_DEFAULT = "kubeverdict.db"


def db_path() -> str:
    return os.environ.get("KUBEWHISPERER_DB", _DB_PATH_DEFAULT)


_SESSION_DDL = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id      TEXT PRIMARY KEY,
    status          TEXT NOT NULL DEFAULT 'IDLE',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    last_state      TEXT,
    review_payload  TEXT,
    error           TEXT
);
"""



def get_db(path: str | None = None) -> sqlite3.Connection:
    """Open a new connection with WAL mode + row_factory (*path*, else ``db_path()``)."""
    conn = sqlite3.connect(path or db_path(), check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db(path: str | None = None) -> None:
    """Create schema if it doesn't exist yet."""
    conn = get_db(path)
    with conn:
        conn.executescript(_SESSION_DDL)
    conn.close()
