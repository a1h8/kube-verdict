"""
SQLite connection management + schema bootstrap.

The DB path is controlled by env var KUBEWHISPERER_DB (default: kubewhisperer.db
in the current working directory).

Tables
------
sessions          — API session metadata + last_state snapshot
checkpoints       — LangGraph thread checkpoints (managed by SqliteSaver)
vector_store_docs — Raw entity texts for FAISS index reconstruction (Option B)
"""
from __future__ import annotations
import os
import sqlite3

_DB_PATH_DEFAULT = "kubewhisperer.db"


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

# ON CONFLICT (uid) DO UPDATE is valid in both SQLite ≥3.24 and PostgreSQL.
_VECTOR_STORE_DDL = """
CREATE TABLE IF NOT EXISTS vector_store_docs (
    uid          TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    kind         TEXT NOT NULL,
    namespace    TEXT,
    text         TEXT NOT NULL,
    kube_version TEXT NOT NULL DEFAULT '',
    doc_source   TEXT NOT NULL DEFAULT 'cluster',
    indexed_at   TEXT NOT NULL
);
"""


def get_db() -> sqlite3.Connection:
    """Open a new connection with WAL mode + row_factory."""
    conn = sqlite3.connect(db_path(), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db() -> None:
    """Create schema if it doesn't exist yet."""
    conn = get_db()
    with conn:
        conn.executescript(_SESSION_DDL + _VECTOR_STORE_DDL)
    conn.close()
