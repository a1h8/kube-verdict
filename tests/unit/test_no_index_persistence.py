"""The vector index has no DB persistence path — docs/fix-plan-001.md, H70.

``persist_texts`` / ``rebuild_from_db`` and the ``vector_store_docs`` table had no production
caller, and a test mocked them as if ``index_node`` called them. Each investigation builds its
index from what it collected and the knowledge base (H40)."""
from __future__ import annotations

import importlib.util
import sqlite3

from persistence.db import init_db
from vectorstore.store import FAISSStore


def test_the_reconstruction_path_is_gone():
    assert not hasattr(FAISSStore, "persist_texts") and not hasattr(FAISSStore, "rebuild_from_db")
    assert importlib.util.find_spec("persistence.vector_store_repo") is None


def test_a_new_database_has_sessions_and_no_vector_table(tmp_path):
    path = str(tmp_path / "kv.db")
    init_db(path)
    tables = {r[0] for r in sqlite3.connect(path).execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "sessions" in tables                                                    # control
    assert "vector_store_docs" not in tables
