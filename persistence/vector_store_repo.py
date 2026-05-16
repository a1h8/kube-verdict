"""
Raw-text persistence for FAISS index reconstruction (Option B).

Stores the metadata produced by FAISSStore so the index can be rebuilt
from text alone — without re-collecting from the Kubernetes cluster.

SQL dialect note
----------------
INSERT ... ON CONFLICT (uid) DO UPDATE is valid in both SQLite ≥3.24 and
PostgreSQL, so swapping the driver is the only change needed for Postgres.
"""
from __future__ import annotations
import sqlite3
from datetime import datetime, timezone
from typing import Any


_COLS = ("uid", "name", "kind", "namespace", "text", "kube_version", "doc_source")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def persist_texts(conn: sqlite3.Connection, metadata: list[dict[str, Any]]) -> int:
    """
    Upsert all metadata dicts into vector_store_docs.
    Returns the number of rows written.
    """
    if not metadata:
        return 0

    now = _now()
    rows = [
        (
            m["uid"], m.get("name", ""), m.get("kind", ""),
            m.get("namespace"), m["text"],
            m.get("kube_version", ""), m.get("doc_source", "cluster"),
            now,
        )
        for m in metadata
    ]
    conn.executemany(
        """
        INSERT INTO vector_store_docs
            (uid, name, kind, namespace, text, kube_version, doc_source, indexed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (uid) DO UPDATE SET
            name         = excluded.name,
            kind         = excluded.kind,
            namespace    = excluded.namespace,
            text         = excluded.text,
            kube_version = excluded.kube_version,
            doc_source   = excluded.doc_source,
            indexed_at   = excluded.indexed_at
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def load_texts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return all rows as metadata dicts, same shape as FAISSStore._metadata."""
    cur = conn.execute(
        "SELECT uid, name, kind, namespace, text, kube_version, doc_source"
        " FROM vector_store_docs"
    )
    return [dict(row) for row in cur.fetchall()]


def count_docs(conn: sqlite3.Connection) -> int:
    cur = conn.execute("SELECT COUNT(*) FROM vector_store_docs")
    return cur.fetchone()[0]


def delete_all(conn: sqlite3.Connection) -> None:
    """Wipe the table — useful for tests or forced re-indexing."""
    conn.execute("DELETE FROM vector_store_docs")
    conn.commit()
