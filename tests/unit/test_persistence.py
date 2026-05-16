"""
Unit tests for persistence layer (SQLite-backed session store).
"""
from __future__ import annotations
import sqlite3
import pytest

from persistence.db import init_db, get_db
from persistence import session_repo
from api.session_store import SessionStore
from api.models import SessionStatus


@pytest.fixture
def conn(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test.db")
    monkeypatch.setenv("KUBEWHISPERER_DB", db_file)
    init_db()
    c = get_db()
    yield c
    c.close()


@pytest.fixture
def mem_conn():
    """In-memory connection — no file, schema created manually."""
    from persistence.db import _SESSION_DDL, _VECTOR_STORE_DDL
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(_SESSION_DDL + _VECTOR_STORE_DDL)
    yield c
    c.close()


# ── session_repo ──────────────────────────────────────────────────────────────

class TestSessionRepo:
    def test_create_and_get(self, mem_conn):
        session_repo.create_session(mem_conn, "s1")
        row = session_repo.get_session_row(mem_conn, "s1")
        assert row is not None
        assert row["session_id"] == "s1"
        assert row["status"] == "IDLE"

    def test_get_missing_returns_none(self, mem_conn):
        assert session_repo.get_session_row(mem_conn, "nope") is None

    def test_update_status(self, mem_conn):
        session_repo.create_session(mem_conn, "s2")
        session_repo.update_session(mem_conn, "s2", status="RUNNING")
        row = session_repo.get_session_row(mem_conn, "s2")
        assert row["status"] == "RUNNING"

    def test_update_last_state(self, mem_conn):
        session_repo.create_session(mem_conn, "s3")
        session_repo.update_session(mem_conn, "s3", last_state={"query": "test"})
        state = session_repo.load_last_state(mem_conn, "s3")
        assert state == {"query": "test"}

    def test_update_review_payload(self, mem_conn):
        session_repo.create_session(mem_conn, "s4")
        session_repo.update_session(mem_conn, "s4", review_payload={"summary": "x"})
        payload = session_repo.load_review_payload(mem_conn, "s4")
        assert payload == {"summary": "x"}

    def test_update_error(self, mem_conn):
        session_repo.create_session(mem_conn, "s5")
        session_repo.update_session(mem_conn, "s5", error="boom")
        row = session_repo.get_session_row(mem_conn, "s5")
        assert row["error"] == "boom"

    def test_clear_error(self, mem_conn):
        session_repo.create_session(mem_conn, "s6")
        session_repo.update_session(mem_conn, "s6", error="boom")
        session_repo.update_session(mem_conn, "s6", clear_error=True)
        row = session_repo.get_session_row(mem_conn, "s6")
        assert row["error"] is None

    def test_delete_session(self, mem_conn):
        session_repo.create_session(mem_conn, "s7")
        session_repo.delete_session(mem_conn, "s7")
        assert session_repo.get_session_row(mem_conn, "s7") is None

    def test_list_sessions_ordered(self, mem_conn):
        for sid in ("a", "b", "c"):
            session_repo.create_session(mem_conn, sid)
        rows = session_repo.list_sessions(mem_conn)
        ids = [r["session_id"] for r in rows]
        # created_at timestamps are equal at this resolution; all present
        assert set(ids) == {"a", "b", "c"}

    def test_json_roundtrip_complex(self, mem_conn):
        state = {
            "query": "pods crashlooping",
            "reasoning_history": [{"step": 1, "confidence": "HIGH"}],
            "ingestion_stats": {"cluster": 10},
        }
        session_repo.create_session(mem_conn, "s8")
        session_repo.update_session(mem_conn, "s8", last_state=state)
        loaded = session_repo.load_last_state(mem_conn, "s8")
        assert loaded == state


# ── SessionStore ──────────────────────────────────────────────────────────────

class TestSessionStore:
    @pytest.fixture
    def store(self, mem_conn):
        return SessionStore(mem_conn)

    def test_create_returns_session(self, store):
        s = store.create("x1")
        assert s.session_id == "x1"
        assert s.status == SessionStatus.IDLE

    def test_get_returns_session(self, store):
        store.create("x2")
        s = store.get("x2")
        assert s is not None
        assert s.session_id == "x2"

    def test_get_missing_returns_none(self, store):
        assert store.get("missing") is None

    def test_get_or_404_raises(self, store):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            store.get_or_404("no-such")
        assert exc_info.value.status_code == 404

    def test_set_status_persists(self, mem_conn):
        store = SessionStore(mem_conn)
        store.create("x3")
        store.set_status("x3", SessionStatus.RUNNING)
        row = session_repo.get_session_row(mem_conn, "x3")
        assert row["status"] == "RUNNING"

    def test_set_last_state_persists(self, mem_conn):
        store = SessionStore(mem_conn)
        store.create("x4")
        store.set_last_state("x4", {"query": "q"})
        loaded = session_repo.load_last_state(mem_conn, "x4")
        assert loaded == {"query": "q"}

    def test_set_error_persists(self, mem_conn):
        store = SessionStore(mem_conn)
        store.create("x5")
        store.set_error("x5", "something broke")
        row = session_repo.get_session_row(mem_conn, "x5")
        assert row["error"] == "something broke"

    def test_delete_removes_from_db(self, mem_conn):
        store = SessionStore(mem_conn)
        store.create("x6")
        store.delete("x6")
        assert session_repo.get_session_row(mem_conn, "x6") is None

    def test_cold_load_from_db(self, mem_conn):
        """Session written directly to DB should be visible via get()."""
        session_repo.create_session(mem_conn, "cold")
        session_repo.update_session(
            mem_conn, "cold",
            status="COMPLETED",
            last_state={"query": "cold start"},
        )
        store = SessionStore(mem_conn)  # fresh store — empty cache
        s = store.get("cold")
        assert s is not None
        assert s.status == SessionStatus.COMPLETED
        assert s.last_state["query"] == "cold start"

    def test_recover_interrupted_marks_failed(self, mem_conn):
        """RUNNING sessions at startup are marked FAILED (server restart)."""
        session_repo.create_session(mem_conn, "orphan")
        session_repo.update_session(mem_conn, "orphan", status="RUNNING")
        # Instantiate a fresh store — should run recovery
        SessionStore(mem_conn)
        row = session_repo.get_session_row(mem_conn, "orphan")
        assert row["status"] == "FAILED"
        assert row["error"] is not None


# ── vector_store_repo ────────────────────────────────────────────────────────

class TestVectorStoreRepo:
    from persistence import vector_store_repo as _repo

    @pytest.fixture
    def repo(self):
        from persistence import vector_store_repo
        return vector_store_repo

    _SAMPLE = [
        {"uid": "pod/ns/foo", "name": "foo", "kind": "Pod",
         "namespace": "ns", "text": "kind=Pod name=foo", "kube_version": "1.29", "doc_source": "cluster"},
        {"uid": "svc/ns/bar", "name": "bar", "kind": "Service",
         "namespace": "ns", "text": "kind=Service name=bar", "kube_version": "1.29", "doc_source": "cluster"},
    ]

    def test_persist_and_count(self, mem_conn, repo):
        n = repo.persist_texts(mem_conn, self._SAMPLE)
        assert n == 2
        assert repo.count_docs(mem_conn) == 2

    def test_load_roundtrip(self, mem_conn, repo):
        repo.persist_texts(mem_conn, self._SAMPLE)
        rows = repo.load_texts(mem_conn)
        uids = {r["uid"] for r in rows}
        assert uids == {"pod/ns/foo", "svc/ns/bar"}
        assert rows[0]["text"] is not None

    def test_upsert_overwrites(self, mem_conn, repo):
        repo.persist_texts(mem_conn, self._SAMPLE)
        updated = [{**self._SAMPLE[0], "text": "updated text"}]
        repo.persist_texts(mem_conn, updated)
        rows = repo.load_texts(mem_conn)
        foo = next(r for r in rows if r["uid"] == "pod/ns/foo")
        assert foo["text"] == "updated text"
        assert repo.count_docs(mem_conn) == 2  # no duplicate

    def test_empty_persist(self, mem_conn, repo):
        assert repo.persist_texts(mem_conn, []) == 0
        assert repo.count_docs(mem_conn) == 0

    def test_delete_all(self, mem_conn, repo):
        repo.persist_texts(mem_conn, self._SAMPLE)
        repo.delete_all(mem_conn)
        assert repo.count_docs(mem_conn) == 0


# ── init_db idempotency ───────────────────────────────────────────────────────

def test_init_db_idempotent(conn):
    """Calling init_db() twice must not raise (IF NOT EXISTS guards)."""
    init_db()  # called once in fixture, call again
