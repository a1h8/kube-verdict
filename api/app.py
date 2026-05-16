from __future__ import annotations
from contextlib import asynccontextmanager

from fastapi import FastAPI


@asynccontextmanager
async def lifespan(app: FastAPI):
    from api.session_store import init_store
    from api.routes import sessions as sessions_mod
    from persistence.db import db_path
    from langgraph.checkpoint.sqlite import SqliteSaver
    import config as cfg
    from vectorstore.store import FAISSStore

    init_store()

    from persistence.db import init_db, get_db
    from persistence.vector_store_repo import count_docs
    init_db()

    index_path = cfg.VECTOR_STORE_PATH
    faiss_store = FAISSStore()
    if index_path.exists():
        # Option A — pre-built binary index, fastest path
        faiss_store.load(index_path)
        sessions_mod._faiss_store = faiss_store
    else:
        # Option B — rebuild from raw texts stored in DB
        conn = get_db()
        try:
            if count_docs(conn) > 0:
                faiss_store.rebuild_from_db(conn)
                sessions_mod._faiss_store = faiss_store
        finally:
            conn.close()

    with SqliteSaver.from_conn_string(db_path()) as checkpointer:
        sessions_mod._graph = sessions_mod._build_graph(checkpointer)
        yield


app = FastAPI(
    title="KubeWhisperer API",
    description="REST interface for the LangGraph RCA workflow — sessions, edge tracing, human-in-the-loop.",
    version="0.1.0",
    lifespan=lifespan,
)

from api.routes.health import router as health_router      # noqa: E402
from api.routes.sessions import router as sessions_router  # noqa: E402

app.include_router(health_router)
app.include_router(sessions_router)
